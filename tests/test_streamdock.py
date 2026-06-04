"""
Smoke tests for the StreamDock adapter (src/backend/DeckManagement/StreamDockDeck.py).

These exercise the adapter against the *real* StreamDock SDK device classes
(293 V3, M3, N3, XL) using a stub HID transport, so no hardware is required.
They validate the layout profiles, image routing (logical key -> hardware key),
and the InputEvent -> StreamController callback fan-out.

Run from the repo root:  python3 tests/test_streamdock.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.backend.DeckManagement.StreamDockDeck import (
    StreamDockDeck,
    enumerate_stream_dock_decks,
    STREAMDOCK_VENDOR_ID_STRINGS,
)

from StreamDeck.Devices.StreamDeck import DialEventType, TouchscreenEventType
from StreamDock.Devices.StreamDock293V3 import StreamDock293V3
from StreamDock.Devices.StreamDockM3 import StreamDockM3
from StreamDock.Devices.StreamDockN3 import StreamDockN3
from StreamDock.Devices.StreamDockXL import StreamDockXL
from StreamDock.InputTypes import InputEvent, EventType, ButtonKey, KnobId, Direction


class StubTransport:
    """Records HID writes instead of performing them."""

    def __init__(self):
        self.images = []          # (bytes, hardware_key)
        self.cleared = []         # hardware_key
        self.brightness = None

    # set_device() touches these
    def set_report_size(self, i, o, f):
        self.report_size = (i, o, f)

    def set_report_id(self, report_id):
        self.report_id = report_id

    # adapter image path
    def set_key_image_stream(self, data, key_index):
        self.images.append((bytes(data), key_index))

    def clear_key(self, key_index):
        self.cleared.append(key_index)

    # device.set_brightness -> transport.setBrightness
    def setBrightness(self, percent):
        self.brightness = percent

    def refresh_screen(self):
        pass

    # called by the SDK device's __del__/close() during GC
    def disconnected(self):
        pass

    def close(self):
        pass


def make_deck(device_cls, vendor_id=0x6603, product_id=0x1005):
    transport = StubTransport()
    info = {
        "vendor_id": vendor_id,
        "product_id": product_id,
        "path": b"/dev/hidraw_test",
        "serial_number": "TEST123",
    }
    device = device_cls(transport, info)
    deck = StreamDockDeck(device)
    return deck, transport


# Tiny assert helpers
_passed = 0
def check(cond, msg):
    global _passed
    if not cond:
        raise AssertionError(msg)
    _passed += 1
    print(f"  ok: {msg}")


def test_293v3_layout_and_images():
    print("StreamDock293V3:")
    deck, transport = make_deck(StreamDock293V3)
    check(deck.key_count() == 15, "293V3 key_count == 15")
    check(deck.key_layout() == (3, 5), "293V3 key_layout == (3,5)")
    check(deck.dial_count() == 0, "293V3 dial_count == 0")
    check(deck.is_visual() and not deck.is_touch(), "293V3 visual, not touch")
    check(deck.deck_type() == "StreamDock 293 V3", "293V3 friendly name")
    fmt = deck.key_image_format()
    check(fmt["size"] == (112, 112) and fmt["format"] == "JPEG", "293V3 key image format")

    # set_key_image must be open; fake it
    deck._is_open = True
    deck.set_key_image(0, b"IMG0")    # grid 0 -> KEY_1 -> hw 11
    deck.set_key_image(14, b"IMG14")  # grid 14 -> KEY_15 -> hw 5
    check((b"IMG0", 11) in transport.images, "293V3 key 0 -> hardware 11")
    check((b"IMG14", 5) in transport.images, "293V3 key 14 -> hardware 5")
    # out-of-range key is ignored, not crashed
    deck.set_key_image(99, b"NOPE")
    check(all(d != b"NOPE" for d, _ in transport.images), "293V3 out-of-range key ignored")
    # None clears
    deck.set_key_image(0, None)
    check(11 in transport.cleared, "293V3 None image clears hardware 11")


def test_293v3_button_event():
    print("StreamDock293V3 button events:")
    deck, _ = make_deck(StreamDock293V3)
    events = []
    deck.set_key_callback(lambda d, k, s: events.append((k, s)))
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.BUTTON, key=ButtonKey.KEY_1, state=1))
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.BUTTON, key=ButtonKey.KEY_15, state=0))
    check((0, True) in events, "KEY_1 press -> (0, True)")
    check((14, False) in events, "KEY_15 release -> (14, False)")
    check(deck.key_states()[0] is True, "key_states[0] tracked")


def test_brightness():
    print("brightness:")
    deck, transport = make_deck(StreamDock293V3)
    deck.set_brightness(75)
    check(transport.brightness == 75, "brightness 75 -> 75")
    deck.set_brightness(0.5)
    check(transport.brightness == 50, "brightness 0.5 (fraction) -> 50")
    deck.set_brightness(250)
    check(transport.brightness == 100, "brightness clamped to 100")


def test_m3_dials():
    print("StreamDockM3 dials:")
    deck, _ = make_deck(StreamDockM3, vendor_id=0x5548, product_id=0x1020)
    check(deck.dial_count() == 3, "M3 dial_count == 3")
    check(deck.key_count() == 15 and deck.key_layout() == (3, 5), "M3 grid 3x5")
    dial_events = []
    deck.set_dial_callback(lambda d, i, et, v: dial_events.append((i, et, v)))
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.KNOB_ROTATE, knob_id=KnobId.KNOB_2, direction=Direction.LEFT))
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.KNOB_ROTATE, knob_id=KnobId.KNOB_1, direction=Direction.RIGHT))
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.KNOB_PRESS, knob_id=KnobId.KNOB_3, state=1))
    check((1, DialEventType.TURN, -1) in dial_events, "KNOB_2 left -> dial 1 TURN -1 (CCW)")
    check((0, DialEventType.TURN, 1) in dial_events, "KNOB_1 right -> dial 0 TURN +1 (CW)")
    check((2, DialEventType.PUSH, True) in dial_events, "KNOB_3 press -> dial 2 PUSH True")


def test_n3_grid_and_swipe():
    print("StreamDockN3 grid + swipe:")
    deck, _ = make_deck(StreamDockN3, vendor_id=0x6603, product_id=0x1002)
    check(deck.key_count() == 6 and deck.key_layout() == (2, 3), "N3 grid 2x3 (6 keys)")
    check(deck.dial_count() == 3, "N3 dial_count == 3")
    # The 3 extra bottom buttons (KEY_7..9) are outside the 6-key grid -> ignored
    keys = []
    deck.set_key_callback(lambda d, k, s: keys.append(k))
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.BUTTON, key=ButtonKey.KEY_7, state=1))
    check(keys == [], "N3 KEY_7 (out of grid) ignored")
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.BUTTON, key=ButtonKey.KEY_6, state=1))
    check(keys == [5], "N3 KEY_6 -> grid index 5")
    # swipe
    touch = []
    deck.set_touchscreen_callback(lambda d, et, v: touch.append((et, v)))
    deck._on_sdk_event(deck.device, InputEvent(event_type=EventType.SWIPE, direction=Direction.LEFT))
    check(len(touch) == 1 and touch[0][0] == TouchscreenEventType.DRAG, "N3 swipe -> DRAG")
    check(touch[0][1]["x"] > touch[0][1]["x_out"], "N3 swipe LEFT -> x > x_out (drag left)")


def test_xl_layout():
    print("StreamDockXL:")
    deck, _ = make_deck(StreamDockXL, vendor_id=0x5548, product_id=0x1028)
    check(deck.key_count() == 32 and deck.key_layout() == (4, 8), "XL grid 4x8 (32 keys)")
    check(deck.dial_count() == 2, "XL dial_count == 2")


def test_end_to_end_image_pipeline():
    """The real BetterDeck + StreamDeck PILHelper must produce device-native
    JPEG bytes that reach the transport with the correct hardware key."""
    print("end-to-end image pipeline (BetterDeck + PILHelper):")
    from PIL import Image
    from StreamDeck.ImageHelpers import PILHelper
    from src.backend.DeckManagement.BetterDeck import BetterDeck

    deck, transport = make_deck(StreamDock293V3)
    deck._is_open = True
    better = BetterDeck(deck, rotation=0)

    # Build an image at the device's key size and convert to native bytes.
    img = Image.new("RGB", deck.key_image_format()["size"], (10, 20, 30))
    native = PILHelper.to_native_key_format(better, img)
    check(native[:2] == b"\xff\xd8", "PILHelper produced a JPEG (FFD8 magic)")

    # Setting via BetterDeck (rotation 0 -> identity) must hit hardware key 11.
    better.set_key_image(0, native)
    sent = [d for d, k in transport.images if k == 11]
    check(len(sent) == 1, "BetterDeck key 0 -> transport hardware key 11")
    check(sent[0] == bytes(native), "exact JPEG bytes forwarded unchanged")

    # Decode the sent JPEG back and confirm dimensions match the device format.
    import io
    decoded = Image.open(io.BytesIO(sent[0]))
    check(decoded.size == (112, 112), f"sent JPEG decodes to 112x112 (got {decoded.size})")


class _FakeFeature:
    def __init__(self):
        from StreamDock.FeatrueOption import device_type
        self.deviceType = device_type.dock_293v3


class _FakeDevice:
    """Minimal stand-in exercising open()/refresh worker/close() without HID."""

    def __init__(self):
        self.path = b"/dev/hidrawX"
        self.vendor_id = 0x6603
        self.product_id = 0x1005
        self.serial_number = "FAKE"
        self.firmware_version = "1.0"
        self.feature_option = _FakeFeature()
        self.refresh_count = 0
        self.opened = False
        self.closed = False
        self._cb = None

        class _T:
            def __init__(self): self.images = []
            def set_key_image_stream(self, data, key): self.images.append((bytes(data), key))
            def clear_key(self, key): pass
        self.transport = _T()

    def set_device(self): pass
    def open(self): self.opened = True; return True
    def init(self): pass
    def close(self): self.closed = True
    def refresh(self): self.refresh_count += 1
    def set_key_callback(self, cb): self._cb = cb
    def set_brightness(self, p): pass
    def clearAllIcon(self): pass
    def get_image_key(self, logical): return int(logical)
    def key_image_format(self): return {"size": (112, 112), "format": "JPEG", "flip": (False, False), "rotation": 0}
    def touchscreen_image_format(self): return {"size": (800, 480), "format": "JPEG", "flip": (False, False), "rotation": 0}


def test_refresh_worker_lifecycle():
    print("refresh worker lifecycle:")
    import time
    dev = _FakeDevice()
    deck = StreamDockDeck(dev)
    deck.open()
    check(dev.opened and deck.is_open(), "open() opened device + started worker")
    check(deck._refresh_thread is not None and deck._refresh_thread.is_alive(), "refresh thread alive")

    before = dev.refresh_count
    deck.set_key_image(0, b"\xff\xd8imgdata")   # grid 0 -> ButtonKey(1) -> hw 1
    # Worker flushes within ~30ms; give it a moment.
    time.sleep(0.2)
    check(dev.refresh_count > before, "set_key_image triggered a panel refresh")
    check((b"\xff\xd8imgdata", 1) in dev.transport.images, "image bytes reached transport")

    deck.close()
    time.sleep(0.1)
    check(dev.closed, "close() closed the device")
    check(not deck._refresh_thread.is_alive(), "refresh thread stopped after close()")


def test_vendor_ids_and_enumerate():
    print("vendor ids + enumerate:")
    # Known MiraBox VIDs are present, Elgato's 0fd9 is not
    check("6603" in STREAMDOCK_VENDOR_ID_STRINGS, "0x6603 recognised as StreamDock VID")
    check("5548" in STREAMDOCK_VENDOR_ID_STRINGS, "0x5548 recognised as StreamDock VID")
    check("0fd9" not in STREAMDOCK_VENDOR_ID_STRINGS, "Elgato VID not claimed by StreamDock")
    # No hardware in CI -> empty list, but must not raise
    check(enumerate_stream_dock_decks() == [], "enumerate returns [] with no hardware")


def main():
    tests = [
        test_293v3_layout_and_images,
        test_293v3_button_event,
        test_brightness,
        test_m3_dials,
        test_n3_grid_and_swipe,
        test_xl_layout,
        test_end_to_end_image_pipeline,
        test_refresh_worker_lifecycle,
        test_vendor_ids_and_enumerate,
    ]
    for t in tests:
        t()
    print(f"\nAll {_passed} checks passed across {len(tests)} tests.")


if __name__ == "__main__":
    main()
