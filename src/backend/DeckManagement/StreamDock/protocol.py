"""
Pure-Python HID transport for MiraBox StreamDock devices.

Speaks the StreamDock ``CRT`` HID protocol directly via the ``hid`` (hidapi)
package -- no closed-source vendor libraries. The protocol was reverse
engineered; this is a cleaned, StreamController-focused port of that work.

Wire format: every command is an output report beginning with a 5-byte header
(``b"CRT\\x00\\x00"`` by default) followed by a 3-letter ASCII command and
optional big-endian parameters, zero-padded to the report size. Bulk payloads
(e.g. JPEG key images) are streamed in report-sized chunks afterwards.
"""

import struct
import threading
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
        self._write_lock = threading.RLock()

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

    def _pad(self, buffer: bytes) -> bytes:
        if len(buffer) >= self._report_size:
            return buffer
        return buffer + b"\x00" * (self._report_size - len(buffer))

    def _crt(self, cmd: str, params: bytes = b"", bulk: bytes = b"", crt: bytes = b"CRT\x00\x00"):
        """Send a CRT command, optionally followed by a streamed bulk payload."""
        with self._write_lock:
            if self._device is None:
                return
            pkt = crt + cmd.encode("ascii") + params
            self._device.write(self._pad(pkt))

            if bulk:
                size = self._report_size
                for i in range(0, len(bulk), size):
                    chunk = bulk[i:i + size]
                    # hidapi drops a leading null byte on some platforms; if the
                    # chunk starts with 0x00 we prepend an extra one so the device
                    # still receives the intended bytes.
                    prefix = b"\x00" if chunk[:1] == b"\x00" else b""
                    self._device.write(self._pad(prefix + chunk))

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def read(self, timeout_ms: int = 100) -> Optional[bytes]:
        if self._device is None:
            return None
        try:
            size = max(self._input_report_size, 1024) if self._input_report_size else 1024
            data = self._device.read(size, timeout_ms=timeout_ms)
            return bytes(data) if data else None
        except Exception:
            return None

    def get_firmware_version(self) -> str:
        if self._device is None:
            return ""
        try:
            buf = bytes([self._report_id]) + b"\x00" * self._report_size
            result = self._device.get_input_report(buf)
            raw = bytes(result[1:]).split(b"\x00")[0]
            return raw.decode("utf-8", errors="ignore") if raw else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # Screen / panel control
    # ------------------------------------------------------------------ #
    def wakeup_screen(self):
        self._crt("DIS")

    def refresh_screen(self):
        self._crt("STP")

    def sleep_screen(self):
        self._crt("HAN")

    def heartbeat(self):
        self._crt("CONNECT")

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
