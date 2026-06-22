"""
StreamDock device runtime.

Ties a :class:`StreamDockHID` transport to a :class:`StreamDockModel` and
provides the high-level operations StreamController needs: open/close, image
and brightness control, and an input reader thread that decodes HID reports
into structured :class:`StreamDockInput` events.
"""

import io
import time
import threading
from dataclasses import dataclass
from typing import Optional, Callable, List

from loguru import logger as log
from PIL import Image

from .protocol import StreamDockHID
from .models import StreamDockModel, MODELS, PRODUCTS

_CELL_ROT = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}


@dataclass
class StreamDockInput:
    """A decoded input event.

    type:
      * ``"key"``        -- a grid key; ``index`` = grid index, ``pressed`` set
      * ``"dial_press"`` -- a knob press; ``index`` = dial index, ``pressed`` set
      * ``"dial_turn"``  -- a knob turn; ``index`` = dial index, ``direction`` = +1 CW / -1 CCW
      * ``"swipe"``      -- a touch swipe; ``direction`` = +1 right / -1 left
    """
    type: str
    index: int = 0
    pressed: bool = False
    direction: int = 0


class StreamDockDevice:
    """A single attached StreamDock, addressed by its model's data tables."""

    HEARTBEAT_INTERVAL = 10.0  # seconds

    def __init__(self, path, vendor_id: int, product_id: int, serial_number: str, model: StreamDockModel):
        self.path = path
        self.vendor_id = int(vendor_id or 0)
        self.product_id = int(product_id or 0)
        self.serial_number = serial_number or ""
        self.model = model

        self.hid = StreamDockHID()
        self.firmware_version = ""

        # callback(device, StreamDockInput); fired from the reader thread.
        self.input_callback: Optional[Callable] = None

        self._run = False
        self._disconnected = False
        self._read_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def open(self) -> bool:
        if not self.hid.open(self.path):
            return False
        m = self.model
        self.hid.set_report_config(m.report_input, m.report_output, m.report_feature, m.report_id)
        self.hid.wakeup_screen()
        self.hid.set_key_brightness(100)  # sane default; StreamController sets the real value
        self.hid.clear_all_keys()
        # Full-screen black clear: CLE doesn't visibly wipe some panels, so paint
        # a black fill over every LCD key (covering the gaps around the cutouts)
        # to remove any old/factory content before StreamController draws.
        fill = getattr(m, "screen_clear_size", ())
        if fill:
            blk = self._black_jpeg(fill)
            for hw in sorted(set(m.image_key_map.values())):
                self.hid.set_key_image(blk, hw)
        self.hid.refresh_screen()
        self.firmware_version = self.hid.get_firmware_version()

        self._run = True
        self._read_thread = threading.Thread(target=self._read_loop, name=f"StreamDockRead-{self.serial_number}", daemon=True)
        self._read_thread.start()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name=f"StreamDockHB-{self.serial_number}", daemon=True)
        self._heartbeat_thread.start()
        return True

    def close(self):
        self._run = False
        for t in (self._read_thread, self._heartbeat_thread):
            if t is not None and t.is_alive():
                try:
                    t.join(timeout=1.0)
                except RuntimeError:
                    pass
        # NOTE: deliberately do NOT send notify_disconnected ("CLE..DC") here.
        # On the N3 (and likely the rest of the family) that command latches the
        # panel into a "host disconnected" state in which it ignores ALL display
        # writes until a physical USB replug -- input reports still come through.
        # That made StreamController restarts look half-dead: the deck responded
        # to button/dial presses but never redrew (and key changes did nothing).
        # Just releasing the HID handle leaves the panel writable, so the next
        # open() re-initialises and redraws normally.
        self.hid.close()

    @property
    def is_open(self) -> bool:
        return self.hid.is_open

    def connected(self) -> bool:
        """True while this device's USB path is still enumerable."""
        if self._disconnected:
            return False
        try:
            for info in StreamDockHID.enumerate(self.vendor_id, self.product_id):
                if info.get("path") == self.path:
                    return True
            return False
        except Exception:
            return True

    # ------------------------------------------------------------------ #
    # Output
    # ------------------------------------------------------------------ #
    def set_key_image(self, grid_index: int, jpeg_bytes: bytes):
        hw = self.model.image_key_map.get(grid_index)
        if hw is None:
            return
        if getattr(self.model, "cell_size", ()):
            jpeg_bytes = self._frame_key_image(grid_index, jpeg_bytes)
        self.hid.set_key_image(jpeg_bytes, hw)

    @staticmethod
    def _black_jpeg(size) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", tuple(size), (0, 0, 0)).save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def _frame_key_image(self, grid_index: int, jpeg_bytes: bytes) -> bytes:
        """Paste the rendered key image into the device's tiling cell at a
        per-(col,row) offset and rotate, so each icon lands in its off-centre
        bezel cutout (single-screen panels like the N3)."""
        m = self.model
        try:
            content = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        except Exception as e:
            log.error(f"StreamDock frame decode failed: {e}")
            return jpeg_bytes
        if getattr(m, "icon_size", ()):
            content = content.resize(tuple(m.icon_size))
        col = grid_index % m.cols
        row = grid_index // m.cols
        cx = m.col_x_offsets[col] if col < len(m.col_x_offsets) else 0
        cy = m.row_y_offsets[row] if row < len(m.row_y_offsets) else 0
        canvas = Image.new("RGB", tuple(m.cell_size), (0, 0, 0))
        # centre the (possibly scaled-down) content in the cell, then apply the
        # per-key offset so it lands centred in its bezel cutout
        px = cx + (canvas.width - content.width) // 2
        py = cy + (canvas.height - content.height) // 2
        canvas.paste(content, (px, py))
        rot = _CELL_ROT.get(m.cell_rotation % 360)
        if rot is not None:
            canvas = canvas.transpose(rot)
        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def clear_key(self, grid_index: int):
        hw = self.model.image_key_map.get(grid_index)
        if hw is not None:
            self.hid.clear_key(hw)

    def clear_all(self):
        self.hid.clear_all_keys()

    def set_brightness(self, percent: int):
        self.hid.set_key_brightness(percent)

    def refresh(self):
        self.hid.refresh_screen()

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #
    def _decode(self, code: int, state: int) -> Optional[StreamDockInput]:
        m = self.model
        if code in m.button_map:
            return StreamDockInput("key", index=m.button_map[code], pressed=(state == 0x01))
        if code in m.knob_rotate_map:
            dial, direction = m.knob_rotate_map[code]
            return StreamDockInput("dial_turn", index=dial, direction=direction)
        if code in m.knob_press_map:
            return StreamDockInput("dial_press", index=m.knob_press_map[code], pressed=(state == 0x01))
        if code in m.swipe_map:
            return StreamDockInput("swipe", direction=m.swipe_map[code])
        return None

    def _handle_report(self, arr) -> Optional[StreamDockInput]:
        """Parse one raw input report and dispatch it. Returns the event (or None)."""
        offset = self.model.code_offset
        if not arr or len(arr) < offset + 2:
            return None
        if arr[9] == 0xFF:
            # Write acknowledgement, not an input event.
            return None
        event = self._decode(arr[offset], arr[offset + 1])
        if event is not None and self.input_callback is not None:
            self.input_callback(self, event)
        return event

    def _read_loop(self):
        errors = 0
        while self._run:
            try:
                arr = self.hid.read(timeout_ms=100)
                errors = 0
            except Exception as e:
                if not self._run:
                    break
                errors += 1
                if errors == 1:
                    log.error(f"StreamDock read error (device disconnected?): {e}")
                # A few consecutive read errors means the device is gone. Stop the
                # reader (and heartbeat) and mark it disconnected instead of
                # spinning/spamming forever -- connected() then reports False so
                # the deck manager removes it; it re-adds on replug.
                if errors >= 3:
                    self._disconnected = True
                    self._run = False
                    log.info(f"StreamDock {self.serial_number} disconnected; reader stopped.")
                    break
                time.sleep(0.1)
                continue
            # None is a benign read timeout (already throttled by timeout_ms).
            if arr is None:
                continue
            try:
                self._handle_report(arr)
            except Exception as e:
                log.error(f"StreamDock report handling error: {e}")

    def _heartbeat_loop(self):
        time.sleep(1.0)  # let the reader settle first
        while self._run:
            try:
                self.hid.heartbeat()
            except Exception as e:
                log.error(f"StreamDock heartbeat error: {e}")
            # Sleep in small slices so close() stays responsive.
            waited = 0.0
            while self._run and waited < self.HEARTBEAT_INTERVAL:
                time.sleep(0.1)
                waited += 0.1


def enumerate_stream_dock_devices() -> List[StreamDockDevice]:
    """Return an (unopened) :class:`StreamDockDevice` for every attached StreamDock."""
    devices = []
    seen_paths = set()
    for (vid, pid), key in PRODUCTS.items():
        try:
            infos = StreamDockHID.enumerate(vid, pid)
        except Exception as e:
            log.error(f"StreamDock enumeration failed for {vid:04x}:{pid:04x}: {e}")
            continue
        for info in infos:
            path = info.get("path")
            if path in seen_paths:
                continue
            seen_paths.add(path)
            devices.append(StreamDockDevice(
                path,
                info.get("vendor_id", vid),
                info.get("product_id", pid),
                info.get("serial_number", "") or "",
                MODELS[key],
            ))
    return devices
