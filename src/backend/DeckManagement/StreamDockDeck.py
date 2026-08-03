"""
StreamDock device support for StreamController.

StreamDock devices are third-party Stream Deck clones manufactured by MiraBox.
This module adapts StreamController's in-tree, pure-Python StreamDock HID
implementation (``src/backend/DeckManagement/StreamDock``; no closed-source
vendor libraries) to the small duck-typed device interface StreamController
already speaks for Elgato Stream Decks (see ``BetterDeck`` and ``FakeDeck``).

The :class:`StreamDockDeck` adapter wraps a single :class:`StreamDockDevice`
and translates between the two worlds:

* StreamController works with 0-indexed, row-major key grids; the device speaks
  per-model hardware key maps (already resolved to grid indices in the model).
* StreamController expects native (already encoded + rotated) JPEG bytes for
  ``set_key_image``; those are streamed straight to the device, with panel
  refreshes batched so a full page render flushes once.
* StreamController registers three callbacks
  (``set_key_callback``/``set_dial_callback``/``set_touchscreen_callback`` with
  ``DialEventType``/``TouchscreenEventType`` values); the device emits a single
  :class:`StreamDockInput` stream. We fan that out to the right callback.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or any later version.
"""

import hashlib
import os
import re
import threading
import time

from loguru import logger as log

# The StreamDeck library ships these enums; StreamController dispatches dial and
# touchscreen events using them, so the values we pass back must match exactly.
from StreamDeck.Devices.StreamDeck import DialEventType, TouchscreenEventType

try:
    from src.backend.DeckManagement.StreamDock import (
        enumerate_stream_dock_devices,
        VENDOR_ID_STRINGS,
    )
    STREAMDOCK_AVAILABLE = True
except Exception as e:  # pragma: no cover - only if hidapi is missing
    log.warning(f"StreamDock support unavailable (is 'hidapi' installed?): {e}")
    STREAMDOCK_AVAILABLE = False
    VENDOR_ID_STRINGS = set()

    def enumerate_stream_dock_devices():
        return []

# Name kept for DeckManager, which matches USB hotplug vendor ids against it.
STREAMDOCK_VENDOR_ID_STRINGS = VENDOR_ID_STRINGS

# How long (in seconds) the background refresher waits between flushing pending
# image changes. ~30 ms keeps animations smooth (~30 fps) while coalescing the
# burst of per-key writes a full page render produces into a single refresh.
_REFRESH_INTERVAL = 0.03
_USB_TOPOLOGY_RE = re.compile(r"^\d+-\d+(?:\.\d+)*$")


def _usb_topology_from_hid_path(path) -> str | None:
    """Resolve a stable USB port chain from a libusb or hidraw path."""
    if isinstance(path, bytes):
        path = path.decode(errors="ignore")
    path = str(path)

    candidates = [os.path.basename(path)]
    basename = candidates[0]
    if basename.startswith("hidraw"):
        target = os.path.realpath(f"/sys/class/hidraw/{basename}/device")
        candidates.extend(reversed(target.split(os.sep)))

    for candidate in candidates:
        topology = candidate.split(":", 1)[0]
        if _USB_TOPOLOGY_RE.fullmatch(topology):
            return topology
    return None


class StreamDockDeck:
    """Adapts a single :class:`StreamDockDevice` to StreamController's deck interface.

    Instances are created by :func:`enumerate_stream_dock_decks` and handed to
    ``DeckController`` exactly like an Elgato ``StreamDeck`` device. The public
    method surface mirrors the subset of ``StreamDeck.Devices.StreamDeck`` that
    ``BetterDeck``/``DeckController``/``DeckManager`` actually use.
    """

    def __init__(self, device):
        self.device = device
        self._reinit_lock = threading.Lock()
        self.model = device.model

        self._path = device.path
        self._vendor_id = device.vendor_id
        self._product_id = device.product_id
        self._serial = device.serial_number
        self._usb_topology = _usb_topology_from_hid_path(device.path)

        self._is_open = False

        # StreamController callbacks (the device emits a single event stream that
        # we fan out to these).
        self._key_cb = None
        self._dial_cb = None
        self._touch_cb = None

        self._key_states = [False] * self.key_count()
        self._dial_states = [False] * self.model.dials

        # Background refresher: StreamDock panels only show pixels after an
        # explicit refresh, so we batch them instead of flushing per key.
        self._dirty = threading.Event()
        self._run_refresh = False
        self._refresh_thread = None

        self.device.input_callback = self._on_device_event

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
        # device.open() configures HID reports, wakes/clears the panel, and
        # starts the reader + heartbeat threads. It returns False (rather than
        # raising) when the device can't be claimed, e.g. it is already open in
        # another instance -- surface that so the caller skips this deck,
        # mirroring the Elgato code path.
        if not self.device.open():
            raise RuntimeError(f"Failed to open StreamDock device at {self.id()}")
        self._is_open = True

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
        return self.device.connected()

    # ------------------------------------------------------------------ #
    # Identity
    # ------------------------------------------------------------------ #
    def vendor_id(self) -> int:
        return self._vendor_id

    def product_id(self) -> int:
        return self._product_id

    def id(self) -> str:
        path = self.device.path
        if isinstance(path, bytes):
            path = path.decode(errors="ignore")
        return str(path)

    def get_serial_number(self) -> str:
        if self._serial:
            return self._serial
        # Some StreamDock units report an empty serial. Fall back to a stable id
        # derived from the USB path so settings still persist as long as the
        # device stays on the same port.
        path = self._path if isinstance(self._path, bytes) else str(self._path).encode()
        return "streamdock-" + hashlib.sha1(path).hexdigest()[:12]

    def recovery_identity(self):
        """Stable physical identity used while a USB reset can change HID paths."""
        if self._serial:
            return ("streamdock", self._vendor_id, self._product_id, "serial", self._serial)
        if self._usb_topology:
            return (
                "streamdock",
                self._vendor_id,
                self._product_id,
                "topology",
                self._usb_topology,
            )
        return None

    def get_firmware_version(self) -> str:
        return self.device.firmware_version or ""

    def deck_type(self) -> str:
        return self.model.name

    # ------------------------------------------------------------------ #
    # Layout / capabilities
    # ------------------------------------------------------------------ #
    def key_count(self) -> int:
        return self.model.key_count

    def key_layout(self) -> tuple:
        return (self.model.rows, self.model.cols)

    def dial_count(self) -> int:
        return self.model.dials

    def large_dial_indices(self) -> set:
        """Dial indices that are physically larger than the rest (UI sizing hint)."""
        return set(getattr(self.model, "large_dials", ()) or ())

    def screenless_key_indices(self) -> set:
        """Grid indices of keys with no image screen (input-only buttons)."""
        return set(getattr(self.model, "screenless_keys", ()) or ())

    def touch_key_count(self) -> int:
        return 0

    def is_visual(self) -> bool:
        return True

    def is_touch(self) -> bool:
        # The SD+-style touch strip is not mapped in v1; StreamDock background
        # screens are a different concept. Dials still work without it.
        return False

    def key_image_format(self) -> dict:
        return self.model.key_image_format

    def touchscreen_image_format(self) -> dict:
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
            if image is None:
                self.device.clear_key(int(key))
            else:
                self.device.set_key_image(int(key), bytes(image))
            self._mark_dirty()
        except Exception as e:
            log.error(f"StreamDock set_key_image failed for key {key}: {e}")

    def set_key_color(self, key, r, g, b) -> None:
        # StreamDock keys are JPEG screens, not RGB LEDs.
        return

    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0) -> None:
        # Touch strip not mapped in v1 (is_touch() is False, so this is unused).
        return

    def set_screen_image(self, image) -> None:
        return

    def reset(self) -> None:
        try:
            self.device.clear_all()
            self._mark_dirty()
        except Exception as e:
            log.error(f"StreamDock reset failed: {e}")

    def reinitializing(self) -> bool:
        """Return whether this deck currently owns an in-place recovery."""
        return self._reinit_lock.locked()

    def reinit_display(self) -> bool:
        """Reinitialize display output without interrupting the input transport."""
        if self._reinit_lock.locked():
            return False
        try:
            return bool(self.device.reinit_display())
        except Exception as e:
            log.error(f"StreamDock display re-init failed: {e}")
            return False

    def reinit_panel(self) -> bool:
        """Attempt panel recovery: USB reset + reopen + official handshake +
        repaint of the last drawn images. Used for suspend/disconnect recovery;
        normal lock-screen transitions use only the ordinary screensaver.

        Runs in a background thread (the reset + settle takes ~3s and callers
        include the GTK main loop) and is single-flight: a re-entrant call
        while a recovery is already running is dropped.
        """
        if not self._reinit_lock.acquire(blocking=False):
            return False

        def _run():
            try:
                self.device.reinit_panel()
                self._mark_dirty()
            except Exception as e:
                log.error(f"StreamDock reinit_panel failed: {e}")
            finally:
                self._reinit_lock.release()

        try:
            threading.Thread(target=_run, name="StreamDockReinit", daemon=True).start()
        except Exception as e:
            self._reinit_lock.release()
            log.error(f"Failed to start StreamDock panel recovery: {e}")
            return False
        return True

    def sleep_panel(self) -> None:
        """Send an explicit HAN panel-off request.

        Automatic lock handling does not call this because HAN can latch N3
        firmware into a display-frozen state requiring a physical replug.
        """
        try:
            self.device.sleep_panel()
        except Exception as e:
            log.error(f"StreamDock sleep_panel failed: {e}")

    def set_poll_frequency(self, hz) -> None:
        # The device owns its own reader thread; nothing to configure here.
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
    # Internal: device event fan-out
    # ------------------------------------------------------------------ #
    def _on_device_event(self, device, event) -> None:
        """Translate a :class:`StreamDockInput` to StreamController callbacks.

        Runs on the device's reader thread; StreamController's callbacks are
        expected to be thread-safe (they marshal to the GTK main loop).
        """
        try:
            if event.type == "key":
                if 0 <= event.index < self.key_count():
                    self._key_states[event.index] = event.pressed
                    if self._key_cb is not None:
                        self._key_cb(self, event.index, event.pressed)

            elif event.type == "dial_press":
                if 0 <= event.index < self.model.dials:
                    self._dial_states[event.index] = event.pressed
                    if self._dial_cb is not None:
                        self._dial_cb(self, event.index, DialEventType.PUSH, event.pressed)

            elif event.type == "dial_turn":
                if 0 <= event.index < self.model.dials and self._dial_cb is not None:
                    self._dial_cb(self, event.index, DialEventType.TURN, event.direction)

            elif event.type == "swipe" and self._touch_cb is not None:
                # StreamController only checks x vs x_out to decide direction.
                if event.direction < 0:
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


def enumerate_stream_dock_decks() -> list:
    """Return a :class:`StreamDockDeck` adapter for every attached StreamDock."""
    return [StreamDockDeck(dev) for dev in enumerate_stream_dock_devices()]
