"""
Pure-Python HID transport for MiraBox StreamDock devices.

Speaks the StreamDock ``CRT`` HID protocol directly via the ``hid`` (hidapi)
package -- no closed-source vendor libraries. The protocol was reverse
engineered; this is a cleaned, StreamController-focused port of that work.

Attribution: the pure-Python StreamDock HID transport this module is based on
was reverse engineered and contributed by Philip Huppert (GitHub: Phaeilo) in
"Pure Python HID transport implementation":
https://github.com/MiraboxSpace/StreamDock-Device-SDK/pull/76

The MOD mode-select command and its place in the official connect handshake
(MOD=software -> DIS -> LIG) -- the missing half of the "host gone" latch
recovery -- plus the HAN-is-true-panel-off and CONNECT-keepalive semantics
come from Steve Murr's independent reverse engineering
(https://github.com/stevemurr/streamdock), corroborated by 4ndv's mirajazz
(https://github.com/4ndv/mirajazz, byte-identical MOD frames on N-series
hardware) and https://github.com/rigor789/mirabox-streamdock-node.

Wire format: HIDAPI receives the configured report ID followed by a fixed-size
protocol payload. Every command payload begins with a 5-byte header
(``b"CRT\\x00\\x00"`` by default), then a 3-letter ASCII command and optional
big-endian parameters. Bulk payloads (e.g. JPEG key images) are streamed in
fixed-size reports afterwards.
"""

import struct
import threading
import time
from typing import Optional, List

import hid


class StreamDockHID:
    """Low-level HID I/O + StreamDock command encoding for one device."""

    _DEFAULT_REPORT_SIZE = 1024

    def __init__(self):
        self._device: Optional[hid.device] = None
        self._is_open = False
        self._report_id = 0
        self._input_report_size = 0
        self._output_report_size = 0
        self._feature_report_size = 0
        self._write_condition = threading.Condition()
        self._write_active = False
        self._normal_waiters = 0
        self._priority_waiters = 0
        self._write_started_at: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Enumeration
    # ------------------------------------------------------------------ #
    @staticmethod
    def enumerate(vendor_id: int, product_id: int) -> List[dict]:
        """Return matching HID devices (interface 0 only) as info dicts."""
        devices = []
        for info in hid.enumerate(vendor_id, product_id):
            if info.get("interface_number") not in (0, -1):
                # StreamDock control interface is 0; skip companion interfaces.
                # (-1 is reported on platforms without interface numbers.)
                continue
            devices.append(info)
        return devices

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def open(self, path) -> bool:
        if self._is_open or self._device is not None:
            return False
        try:
            self._device = hid.device()
            self._device.open_path(path)
            self._device.set_nonblocking(False)
            self._is_open = True
            return True
        except Exception:
            self._device = None
            self._is_open = False
            return False

    def close(self):
        try:
            if self._device is not None:
                self._device.close()
        finally:
            self._device = None
            self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open and self._device is not None

    def set_report_config(self, input_size: int, output_size: int, feature_size: int, report_id: int = 0):
        self._input_report_size = input_size
        self._output_report_size = output_size
        self._feature_report_size = feature_size
        self._report_id = report_id & 0xFF

    # ------------------------------------------------------------------ #
    # Packet encoding
    # ------------------------------------------------------------------ #
    @property
    def _report_size(self) -> int:
        if self._output_report_size > 0:
            return self._output_report_size - 1
        return self._DEFAULT_REPORT_SIZE

    def _encode_report(self, payload: bytes) -> bytes:
        size = self._report_size
        if len(payload) > size:
            raise ValueError(
                f"StreamDock output payload is {len(payload)} bytes; report limit is {size}"
            )
        return bytes([self._report_id]) + payload.ljust(size, b"\x00")

    def _acquire_write(self, priority: bool):
        with self._write_condition:
            if priority:
                self._priority_waiters += 1
            else:
                self._normal_waiters += 1
            try:
                while self._write_active or (not priority and self._priority_waiters):
                    self._write_condition.wait()
                self._write_active = True
            finally:
                if priority:
                    self._priority_waiters -= 1
                else:
                    self._normal_waiters -= 1

    def _release_write(self):
        with self._write_condition:
            self._write_active = False
            self._write_condition.notify_all()

    def _crt(
        self,
        cmd: str,
        params: bytes = b"",
        bulk: bytes = b"",
        crt: bytes = b"CRT\x00\x00",
        priority: bool = False,
    ):
        """Send a CRT command, optionally followed by a streamed bulk payload."""
        self._acquire_write(priority)
        try:
            if self._device is None:
                return
            self._write_started_at = time.monotonic()
            pkt = crt + cmd.encode("ascii") + params
            self._device.write(self._encode_report(pkt))

            if bulk:
                size = self._report_size
                for i in range(0, len(bulk), size):
                    chunk = bulk[i:i + size]
                    self._device.write(self._encode_report(chunk))
        finally:
            self._write_started_at = None
            self._release_write()

    def write_stalled(self, threshold: float) -> bool:
        """Return whether the active HID write has exceeded ``threshold`` seconds.

        Input reads use a separate thread, so a wedged output endpoint can leave
        buttons working while every display update and CONNECT keepalive queues
        behind the same write lock. This lock-free timestamp lets that reader
        report the otherwise invisible failure mode.
        """
        started_at = self._write_started_at
        return started_at is not None and time.monotonic() - started_at >= threshold

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def read(self, timeout_ms: int = 100) -> Optional[bytes]:
        """Read one input report. Returns None on timeout; raises on HID error.

        The caller (the device reader thread) distinguishes a benign timeout
        (None) from a real error (exception, e.g. the device was unplugged) so
        it can back off instead of spinning.
        """
        if self._device is None:
            return None
        size = max(self._input_report_size, 1024) if self._input_report_size else 1024
        data = self._device.read(size, timeout_ms=timeout_ms)
        return bytes(data) if data else None

    def get_firmware_version(self) -> str:
        if self._device is None:
            return ""
        try:
            # hidapi: get_input_report(report_num, max_length) -> list[int],
            # with the report id as the first returned byte.
            result = self._device.get_input_report(self._report_id, self._report_size)
            raw = bytes(result[1:]).split(b"\x00")[0]
            return raw.decode("utf-8", errors="ignore") if raw else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # Screen / panel control
    # ------------------------------------------------------------------ #
    # Device operating modes (MOD command). The firmware boots into KEYBOARD
    # mode on some models; SOFTWARE mode is what the official app selects at
    # connect and is what host-drawn images expect.
    MODE_KEYBOARD = 1
    MODE_CALCULATOR = 2
    MODE_SOFTWARE = 3

    def set_mode(self, mode: int):
        """MOD: select the device operating mode (ASCII digit payload).

        Part of the official connect handshake (MOD=software, then DIS + LIG).
        Hardware note (N3): after a host suspend -- or the DC command -- the
        firmware latches into a "host gone" state that ACKs but ignores all
        display writes; a USB port reset followed by a fresh open + this
        handshake clears it. Either step alone does not.
        """
        self._crt("MOD", b"\x00\x00" + bytes([0x30 + (mode & 0x0F)]))

    def wakeup_screen(self):
        self._crt("DIS")

    def refresh_screen(self):
        self._crt("STP")

    def sleep_screen(self):
        self._crt("HAN")

    def heartbeat(self):
        self._crt("CONNECT", priority=True)

    def notify_disconnected(self):
        self._crt("CLE\x00\x00DC")

    # ------------------------------------------------------------------ #
    # Keys
    # ------------------------------------------------------------------ #
    def set_key_brightness(self, brightness: int):
        self._crt("LIG", struct.pack(">HB", 0, brightness & 0xFF))

    def clear_key(self, key_index: int):
        self._crt("CLE", struct.pack(">HB", 0, key_index & 0xFF))

    def clear_all_keys(self):
        self.clear_key(0xFF)

    def set_key_image(self, jpeg_data: bytes, key_index: int):
        """Send a JPEG image to a hardware key index."""
        self._crt("BAT", struct.pack(">IB", len(jpeg_data), key_index & 0xFF), jpeg_data)
