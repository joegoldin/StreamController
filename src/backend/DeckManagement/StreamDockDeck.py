"""
StreamDock device support for StreamController.

StreamDock devices are third-party Stream Deck clones manufactured by MiraBox.
This module adapts the StreamDock-Device-SDK (vendored as a git submodule and
using its pure-Python, reverse-engineered HID transport -- no closed-source
``.dll``/``.so`` blobs) to the small duck-typed device interface that
StreamController already speaks for Elgato Stream Decks (see ``BetterDeck`` and
``FakeDeck``).

The :class:`StreamDockDeck` adapter wraps a single SDK device and translates
between the two worlds:

* StreamController works with 0-indexed, row-major key grids; the SDK works with
  1-indexed :class:`ButtonKey` values and per-device hardware key maps.
* StreamController expects native (already encoded + rotated) JPEG bytes for
  ``set_key_image``; the SDK's transport accepts those directly via
  ``set_key_image_stream``.
* StreamController registers three separate callbacks
  (``set_key_callback``/``set_dial_callback``/``set_touchscreen_callback`` with
  ``DialEventType``/``TouchscreenEventType`` values); the SDK emits a single
  unified :class:`InputEvent` stream. We fan that out to the right callback.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or any later version.
"""

import os
import sys
import time
import hashlib
import threading

from loguru import logger as log

# ---------------------------------------------------------------------------
# Make the vendored StreamDock SDK importable.
#
# The SDK is included as a git submodule at the repository root and uses
# absolute imports (``from StreamDock... import``), so its ``src`` directory
# has to be on ``sys.path`` before we import anything from it.
# ---------------------------------------------------------------------------
_SDK_SRC = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..",
        "StreamDock-Device-SDK", "Python-SDK", "src",
    )
)
if os.path.isdir(_SDK_SRC) and _SDK_SRC not in sys.path:
    sys.path.insert(0, _SDK_SRC)

# The StreamDeck library ships these enums; StreamController dispatches dial and
# touchscreen events using them, so the values we pass back must match exactly.
from StreamDeck.Devices.StreamDeck import DialEventType, TouchscreenEventType

try:
    from StreamDock.ProductIDs import g_products
    from StreamDock.Transport.LibUSBHIDAPI import LibUSBHIDAPI
    from StreamDock.FeatrueOption import device_type
    from StreamDock.InputTypes import EventType, ButtonKey, KnobId, Direction
    STREAMDOCK_SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - only hit if the submodule is missing
    log.warning(
        "StreamDock SDK not available - StreamDock devices will not be detected. "
        f"Did you run 'git submodule update --init'? ({e})"
    )
    STREAMDOCK_SDK_AVAILABLE = False
    g_products = []


# Every USB vendor id used by a known StreamDock product, as lowercase hex
# strings (matching the format that usb-monitor / pyudev report). DeckManager
# uses this to recognise hotplug events from MiraBox devices.
if STREAMDOCK_SDK_AVAILABLE:
    STREAMDOCK_VENDOR_IDS = sorted({vid for vid, _pid, _cls in g_products})
    STREAMDOCK_VENDOR_ID_STRINGS = {f"{vid:04x}" for vid in STREAMDOCK_VENDOR_IDS}
else:
    STREAMDOCK_VENDOR_IDS = []
    STREAMDOCK_VENDOR_ID_STRINGS = set()


# Per-device layout profile: (rows, cols, dial_count, friendly_name).
#
# ``rows * cols`` is the primary square-key grid that StreamController shows.
# Some devices additionally expose secondary LCD strips or extra hardware
# buttons that don't fit a uniform grid; those are intentionally left out of the
# v1 grid and documented as a limitation. Grid key index ``i`` (0-based,
# row-major) maps to the SDK's ``ButtonKey(i + 1)``; the per-device
# ``get_image_key`` mapping then handles the physical/firmware key order.
def _build_profiles():
    if not STREAMDOCK_SDK_AVAILABLE:
        return {}
    return {
        device_type.dock_293:    (3, 5, 0, "StreamDock 293"),
        device_type.dock_293v3:  (3, 5, 0, "StreamDock 293 V3"),
        device_type.dock_293s:   (3, 5, 0, "StreamDock 293s"),
        device_type.dock_293sv3: (3, 5, 0, "StreamDock 293s V3"),
        device_type.dock_n4:     (2, 5, 0, "StreamDock N4"),
        device_type.dock_n4pro:  (2, 5, 4, "StreamDock N4 Pro"),
        device_type.dock_m3:     (3, 5, 3, "StreamDock M3"),
        device_type.dock_m18:    (3, 5, 0, "StreamDock M18"),
        device_type.dock_n3:     (2, 3, 3, "StreamDock N3"),
        device_type.dock_n1:     (3, 5, 1, "StreamDock N1"),
        device_type.dock_xl:     (4, 8, 2, "StreamDock XL"),
        device_type.k1pro:       (2, 3, 3, "StreamDock K1 Pro"),
        device_type.dock_universal: (3, 5, 0, "StreamDock"),
    }


_DEVICE_PROFILES = _build_profiles()
_DEFAULT_PROFILE = (3, 5, 0, "StreamDock")

# How long (in seconds) the background refresher waits between flushing pending
# image changes to the panel. ~30 ms keeps animations smooth (~30 fps) while
# coalescing the burst of per-key writes that a full page render produces into a
# single panel refresh.
_REFRESH_INTERVAL = 0.03


def _knob_index(knob_id) -> int | None:
    """Map a :class:`KnobId` (``KNOB_1``..``KNOB_4``) to a 0-based dial index."""
    if knob_id is None:
        return None
    try:
        # KnobId values look like "knob_1"; turn that into 0.
        return int(str(knob_id.value).rsplit("_", 1)[1]) - 1
    except (ValueError, IndexError, AttributeError):
        return None


class StreamDockDeck:
    """Adapts a single StreamDock SDK device to StreamController's deck interface.

    Instances are created by :func:`enumerate_stream_dock_decks` and then handed
    to ``DeckController`` exactly like an Elgato ``StreamDeck`` device. The
    public method surface mirrors the subset of ``StreamDeck.Devices.StreamDeck``
    that ``BetterDeck``/``DeckController``/``DeckManager`` actually use.
    """

    def __init__(self, device):
        self.device = device

        # Cache identity up front; the path is the stable per-port id.
        self._path = getattr(device, "path", b"")
        self._vendor_id = int(getattr(device, "vendor_id", 0) or 0)
        self._product_id = int(getattr(device, "product_id", 0) or 0)
        self._serial = getattr(device, "serial_number", "") or ""

        # The device's ``deviceType`` (and report sizes) are only populated by
        # ``set_device()``, which normally runs during ``open()/init()``. We need
        # the type now to choose the layout profile, and ``set_device()`` only
        # mutates in-memory state (no I/O), so it is safe to call early. It is
        # idempotent and will run again during open().
        try:
            self.device.set_device()
        except Exception as e:
            log.warning(f"StreamDock set_device() failed during init: {e}")

        rows, cols, dials, friendly = _DEVICE_PROFILES.get(
            getattr(device.feature_option, "deviceType", None), _DEFAULT_PROFILE
        )
        self._rows = rows
        self._cols = cols
        self._dial_count = dials
        self._deck_type = friendly

        self._is_open = False

        # StreamController callbacks (set after open()). The SDK only supports a
        # single callback, so we register one dispatcher and fan out from it.
        self._key_cb = None
        self._dial_cb = None
        self._touch_cb = None

        self._key_states = [False] * self.key_count()
        self._dial_states = [False] * self._dial_count

        # Background refresher: StreamDock panels only show pixels after an
        # explicit refresh, so we batch them instead of flushing per key.
        self._dirty = threading.Event()
        self._run_refresh = False
        self._refresh_thread = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def open(self, *args, **kwargs):
        """Open and initialise the device. Idempotent.

        ``DeckManager`` and ``DeckController`` may both call this (the latter
        forwards ``beta_resume_mode`` positionally), so extra args are ignored.
        """
        if self._is_open:
            return
        # open() starts the SDK's read + heartbeat threads; init() sets the
        # report sizes/device type and clears the panel. The transport returns
        # False (instead of raising) when the device can't be claimed, e.g. it
        # is already open in another instance -- surface that as an error so the
        # caller skips this deck, mirroring the Elgato code path.
        if self.device.open() is False:
            try:
                self.device.close()
            except Exception:
                pass
            raise RuntimeError(f"Failed to open StreamDock device at {self.id()}")
        self.device.init()
        self._is_open = True

        # One dispatcher, registered once, routes every InputEvent.
        self.device.set_key_callback(self._on_sdk_event)

        self._run_refresh = True
        self._refresh_thread = threading.Thread(
            target=self._refresh_worker, name=f"StreamDockRefresh-{self.id()}", daemon=True
        )
        self._refresh_thread.start()

    def close(self):
        self._run_refresh = False
        self._dirty.set()  # wake the refresher so it can exit promptly
        if self._refresh_thread is not None:
            try:
                self._refresh_thread.join(timeout=1.0)
            except RuntimeError:
                pass
        self._is_open = False
        try:
            self.device.close()
        except Exception as e:
            log.warning(f"Error closing StreamDock device: {e}")

    def is_open(self) -> bool:
        return self._is_open

    def connected(self) -> bool:
        """True while the underlying USB device is still enumerable."""
        try:
            import hid
            target = self._path if isinstance(self._path, bytes) else str(self._path).encode()
            for info in hid.enumerate(self._vendor_id, self._product_id):
                path = info.get("path")
                if isinstance(path, str):
                    path = path.encode()
                if path == target:
                    return True
            return False
        except Exception:
            # If enumeration fails for any reason, assume still connected rather
            # than tearing the deck down spuriously.
            return True

    # ------------------------------------------------------------------ #
    # Identity
    # ------------------------------------------------------------------ #
    def vendor_id(self) -> int:
        return self._vendor_id

    def product_id(self) -> int:
        return self._product_id

    def id(self) -> str:
        path = self._path
        if isinstance(path, bytes):
            path = path.decode(errors="ignore")
        return str(path)

    def get_serial_number(self) -> str:
        if self._serial:
            return self._serial
        # Some StreamDock units report an empty serial. Fall back to a stable id
        # derived from the USB path so settings still persist for one session /
        # as long as the device stays on the same port.
        path = self._path if isinstance(self._path, bytes) else str(self._path).encode()
        return "streamdock-" + hashlib.sha1(path).hexdigest()[:12]

    def get_firmware_version(self) -> str:
        return getattr(self.device, "firmware_version", "") or ""

    def deck_type(self) -> str:
        return self._deck_type

    # ------------------------------------------------------------------ #
    # Layout / capabilities
    # ------------------------------------------------------------------ #
    def key_count(self) -> int:
        return self._rows * self._cols

    def key_layout(self) -> tuple:
        return (self._rows, self._cols)

    def dial_count(self) -> int:
        return self._dial_count

    def touch_key_count(self) -> int:
        return 0

    def is_visual(self) -> bool:
        return True

    def is_touch(self) -> bool:
        # The SD+-style touch strip is not mapped in v1; StreamDock background
        # screens are a different concept. Dials still work without it.
        return False

    def key_image_format(self) -> dict:
        return self.device.key_image_format()

    def touchscreen_image_format(self) -> dict:
        try:
            return self.device.touchscreen_image_format()
        except Exception:
            return {"size": (800, 100), "format": "JPEG", "flip": (False, False), "rotation": 0}

    def screen_image_format(self) -> dict:
        return self.touchscreen_image_format()

    # ------------------------------------------------------------------ #
    # Output
    # ------------------------------------------------------------------ #
    def set_brightness(self, percent) -> None:
        try:
            if isinstance(percent, float) and percent <= 1.0:
                percent = percent * 100
            percent = int(max(0, min(100, percent)))
            self.device.set_brightness(percent)
        except Exception as e:
            log.error(f"StreamDock set_brightness failed: {e}")

    def set_key_image(self, key, image) -> None:
        """Set the native (JPEG bytes) image on key ``key`` (0-based)."""
        if not self._is_open:
            return
        try:
            logical = ButtonKey(int(key) + 1)
            hardware_key = self.device.get_image_key(logical)
        except (ValueError, KeyError):
            return  # key outside this device's supported grid

        try:
            if image is None:
                self.device.transport.clear_key(hardware_key)
            else:
                self.device.transport.set_key_image_stream(bytes(image), hardware_key)
            self._mark_dirty()
        except Exception as e:
            log.error(f"StreamDock set_key_image failed for key {key}: {e}")

    def set_key_color(self, key, r, g, b) -> None:
        # StreamDock keys are JPEG screens, not RGB LEDs; render a solid tile.
        return

    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0) -> None:
        # Touch strip not mapped in v1 (is_touch() is False, so this is unused).
        return

    def set_screen_image(self, image) -> None:
        return

    def reset(self) -> None:
        try:
            self.device.clearAllIcon()
            self._mark_dirty()
        except Exception as e:
            log.error(f"StreamDock reset failed: {e}")

    def set_poll_frequency(self, hz) -> None:
        # The SDK owns its own reader thread; nothing to configure here.
        return

    # ------------------------------------------------------------------ #
    # Input state
    # ------------------------------------------------------------------ #
    def key_states(self) -> list:
        return list(self._key_states)

    def dial_states(self) -> list:
        return list(self._dial_states)

    # ------------------------------------------------------------------ #
    # Callbacks
    # ------------------------------------------------------------------ #
    def set_key_callback(self, callback) -> None:
        self._key_cb = callback

    def set_key_callback_async(self, async_callback, loop=None) -> None:
        import asyncio
        loop = loop or asyncio.get_event_loop()

        def callback(deck, key, state):
            asyncio.run_coroutine_threadsafe(async_callback(deck, key, state), loop)

        self.set_key_callback(callback)

    def set_dial_callback(self, callback) -> None:
        self._dial_cb = callback

    def set_dial_callback_async(self, async_callback, loop=None) -> None:
        import asyncio
        loop = loop or asyncio.get_event_loop()

        def callback(*args):
            asyncio.run_coroutine_threadsafe(async_callback(*args), loop)

        self.set_dial_callback(callback)

    def set_touchscreen_callback(self, callback) -> None:
        self._touch_cb = callback

    def set_touchscreen_callback_async(self, async_callback, loop=None) -> None:
        import asyncio
        loop = loop or asyncio.get_event_loop()

        def callback(*args):
            asyncio.run_coroutine_threadsafe(async_callback(*args), loop)

        self.set_touchscreen_callback(callback)

    # ------------------------------------------------------------------ #
    # Internal: SDK event fan-out
    # ------------------------------------------------------------------ #
    def _on_sdk_event(self, device, event) -> None:
        """Translate a single SDK :class:`InputEvent` to StreamController calls.

        Runs on the SDK reader thread; StreamController's callbacks are expected
        to be thread-safe (they marshal to the GTK main loop themselves).
        """
        try:
            et = event.event_type
            if et == EventType.BUTTON:
                idx = int(event.key) - 1
                if 0 <= idx < self.key_count():
                    pressed = event.state == 1
                    self._key_states[idx] = pressed
                    if self._key_cb is not None:
                        self._key_cb(self, idx, pressed)

            elif et == EventType.KNOB_PRESS:
                di = _knob_index(event.knob_id)
                if di is not None and 0 <= di < self._dial_count:
                    pressed = event.state == 1
                    self._dial_states[di] = pressed
                    if self._dial_cb is not None:
                        self._dial_cb(self, di, DialEventType.PUSH, pressed)

            elif et == EventType.KNOB_ROTATE:
                di = _knob_index(event.knob_id)
                if di is not None and 0 <= di < self._dial_count:
                    if self._dial_cb is not None:
                        value = -1 if event.direction == Direction.LEFT else 1
                        self._dial_cb(self, di, DialEventType.TURN, value)

            elif et == EventType.SWIPE and self._touch_cb is not None:
                # Map a swipe to a touchscreen drag. StreamController only checks
                # x vs x_out to decide direction.
                if event.direction == Direction.LEFT:
                    value = {"x": 1, "x_out": 0, "y": 0}
                else:
                    value = {"x": 0, "x_out": 1, "y": 0}
                self._touch_cb(self, TouchscreenEventType.DRAG, value)
        except Exception as e:
            log.error(f"StreamDock event dispatch error: {e}")

    # ------------------------------------------------------------------ #
    # Internal: batched panel refresh
    # ------------------------------------------------------------------ #
    def _mark_dirty(self) -> None:
        self._dirty.set()

    def _refresh_worker(self) -> None:
        while self._run_refresh:
            # Wait for a change (or wake periodically as a safety net).
            self._dirty.wait(timeout=1.0)
            if not self._run_refresh:
                break
            if self._dirty.is_set():
                self._dirty.clear()
                try:
                    self.device.refresh()
                except Exception as e:
                    log.error(f"StreamDock refresh failed: {e}")
                # Rate-limit so continuous animations don't saturate the bus.
                time.sleep(_REFRESH_INTERVAL)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def enumerate_stream_dock_devices() -> list:
    """Return freshly constructed (unopened) SDK device objects for every
    attached StreamDock.

    Mirrors ``StreamDock.DeviceManager.enumerate`` but without importing the
    SDK's ``DeviceManager`` (which pulls in platform-specific hotplug deps).
    """
    if not STREAMDOCK_SDK_AVAILABLE:
        return []

    devices = []
    seen_paths = set()
    for vid, pid, class_type in g_products:
        try:
            found = LibUSBHIDAPI.enumerate_devices(vendor_id=vid, product_id=pid)
        except Exception as e:
            log.error(f"StreamDock enumeration failed for {vid:04x}:{pid:04x}: {e}")
            continue
        for info in found:
            path = info.get("path")
            if path in seen_paths:
                continue
            seen_paths.add(path)
            device_info = LibUSBHIDAPI.create_device_info_from_dict(info)
            transport = LibUSBHIDAPI(device_info)
            try:
                devices.append(class_type(transport, info))
            except Exception as e:
                log.error(f"Failed to construct StreamDock device {vid:04x}:{pid:04x}: {e}")
    return devices


def enumerate_stream_dock_decks() -> list:
    """Return a :class:`StreamDockDeck` adapter for every attached StreamDock."""
    return [StreamDockDeck(dev) for dev in enumerate_stream_dock_devices()]
