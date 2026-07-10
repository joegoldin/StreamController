"""
Smoke tests for in-tree StreamDock support.

Exercise the model data tables, the device runtime (image routing, input report
decoding incl. K1Pro's report-id offset, dispatch), the StreamDockDeck adapter,
and the real BetterDeck + StreamDeck PILHelper image pipeline -- all with a stub
HID transport, so no hardware is required.

Run from the repo root:  python3 tests/test_streamdock.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.backend.DeckManagement.StreamDock import (
    MODELS,
    PRODUCTS,
    VENDOR_ID_STRINGS,
    StreamDockDevice,
    StreamDockInput,
)
from src.backend.DeckManagement.StreamDockDeck import (
    StreamDockDeck,
    enumerate_stream_dock_decks,
    STREAMDOCK_VENDOR_ID_STRINGS,
)
from StreamDeck.Devices.StreamDeck import DialEventType, TouchscreenEventType


class StubHID:
    """Records writes; can be primed with input reports for the reader."""

    def __init__(self):
        self.images = []      # (bytes, hardware_key)
        self.cleared = []     # hardware_key
        self.brightness = None
        self.refreshes = 0
        self.heartbeats = 0
        self.opened = False
        self.closed = False
        self.report_cfg = None

    @property
    def is_open(self):
        return self.opened and not self.closed

    def open(self, path): self.opened = True; return True
    def close(self): self.closed = True
    def set_report_config(self, i, o, f, r): self.report_cfg = (i, o, f, r)
    def set_mode(self, mode): self.mode = mode
    def sleep_screen(self): pass
    def wakeup_screen(self): pass
    def set_key_brightness(self, b): self.brightness = b
    def clear_all_keys(self): pass
    def clear_key(self, k): self.cleared.append(k)
    def refresh_screen(self): self.refreshes += 1
    def get_firmware_version(self): return "1.2.3"
    def heartbeat(self): self.heartbeats += 1
    def notify_disconnected(self): pass
    def set_key_image(self, jpeg, k): self.images.append((bytes(jpeg), k))
    def read(self, timeout_ms=100):
        import time; time.sleep(0.005); return None


def make_deck(model_key, vid=0x6603, pid=0x1005):
    model = MODELS[model_key]
    dev = StreamDockDevice(b"/dev/hidraw_test", vid, pid, "TEST123", model)
    stub = StubHID()
    dev.hid = stub
    return StreamDockDeck(dev), dev, stub


def report_for(model, code, state, length=64):
    """Craft a raw input report carrying (code, state) at the model's offset."""
    arr = bytearray(length)
    arr[9] = 0x00  # not an ack
    arr[model.code_offset] = code
    arr[model.code_offset + 1] = state
    return bytes(arr)


_passed = 0
def check(cond, msg):
    global _passed
    if not cond:
        raise AssertionError(msg)
    _passed += 1
    print(f"  ok: {msg}")


def test_model_data_integrity():
    print("model data integrity:")
    check(len(MODELS) == 12, "12 models defined")
    check(len(PRODUCTS) == 26, "26 (vid,pid) products mapped")
    for key, m in MODELS.items():
        check(m.key_count == m.rows * m.cols, f"{key}: key_count == rows*cols")
        screenless = set(getattr(m, "screenless_keys", ()) or ())
        with_screen = set(range(m.key_count)) - screenless
        check(set(m.image_key_map) == with_screen,
              f"{key}: image map covers exactly the keys with a display")
        check(all(0 <= gi < m.key_count for gi in m.button_map.values()), f"{key}: button_map -> valid grid index")
        dials_used = {d for d, _ in m.knob_rotate_map.values()} | set(m.knob_press_map.values())
        check(all(0 <= d < m.dials for d in dials_used), f"{key}: knob maps -> valid dial index")
    check(MODELS["K1Pro"].code_offset == 10 and MODELS["StreamDock293V3"].code_offset == 9,
          "K1Pro event code offset is 10, others 9")


def test_image_routing():
    print("image routing (grid index -> hardware key):")
    deck, dev, stub = make_deck("StreamDock293V3")
    deck._is_open = True
    deck.set_key_image(0, b"IMG0")     # grid 0 -> hw 11
    deck.set_key_image(14, b"IMG14")   # grid 14 -> hw 5
    check((b"IMG0", 11) in stub.images, "293V3 grid 0 -> hardware 11")
    check((b"IMG14", 5) in stub.images, "293V3 grid 14 -> hardware 5")
    deck.set_key_image(0, None)
    check(11 in stub.cleared, "293V3 None image clears hardware 11")

    # 293s has a diagonal layout: grid 0 -> hw 13
    deck2, _, stub2 = make_deck("StreamDock293s", vid=0x5548, pid=0x6670)
    deck2._is_open = True
    deck2.set_key_image(0, b"X")
    check((b"X", 13) in stub2.images, "293s grid 0 -> hardware 13 (diagonal layout)")


def test_input_decode_and_dispatch():
    print("input report decode + dispatch:")
    # 293V3 button: code 0x0b -> grid 0
    deck, dev, _ = make_deck("StreamDock293V3")
    keys = []
    deck.set_key_callback(lambda d, k, s: keys.append((k, s)))
    dev._handle_report(report_for(dev.model, 0x0b, 0x01))   # press grid 0
    dev._handle_report(report_for(dev.model, 0x0f, 0x02))   # release grid 4
    check((0, True) in keys, "293V3 code 0x0b press -> key 0 down")
    check((4, False) in keys, "293V3 code 0x0f release -> key 4 up")
    check(deck.key_states()[0] is True, "key_states tracked")

    # ack reports (arr[9] == 0xFF) are ignored
    ack = bytearray(64); ack[9] = 0xFF
    check(dev._handle_report(bytes(ack)) is None, "ack report (arr[9]=0xFF) ignored")

    # M3 knobs
    deck_m3, dev_m3, _ = make_deck("StreamDockM3", vid=0x5548, pid=0x1020)
    dials = []
    deck_m3.set_dial_callback(lambda d, i, et, v: dials.append((i, et, v)))
    dev_m3._handle_report(report_for(dev_m3.model, 0x50, 0x00))  # knob 0 left
    dev_m3._handle_report(report_for(dev_m3.model, 0x35, 0x01))  # knob 0 press (code 0x35 -> dial 0)
    check((0, DialEventType.TURN, -1) in dials, "M3 code 0x50 -> dial 0 TURN CCW")
    check(any(i == 0 and et == DialEventType.PUSH and v is True for i, et, v in dials), "M3 code 0x35 -> dial 0 PUSH")

    # K1Pro uses code offset 10 (report id 0x04). code 0x05 -> grid 0
    deck_k1, dev_k1, _ = make_deck("K1Pro", vid=0x6603, pid=0x1015)
    k1keys = []
    deck_k1.set_key_callback(lambda d, k, s: k1keys.append((k, s)))
    dev_k1._handle_report(report_for(dev_k1.model, 0x05, 0x01))
    check((0, True) in k1keys, "K1Pro code 0x05 at offset 10 -> key 0 down")

    # N4Pro swipe -> touchscreen DRAG
    deck_n4p, dev_n4p, _ = make_deck("StreamDockN4Pro", vid=0x5548, pid=0x1008)
    touch = []
    deck_n4p.set_touchscreen_callback(lambda d, et, v: touch.append((et, v)))
    dev_n4p._handle_report(report_for(dev_n4p.model, 0x38, 0x00))  # swipe left
    check(len(touch) == 1 and touch[0][0] == TouchscreenEventType.DRAG, "N4Pro swipe -> DRAG")
    check(touch[0][1]["x"] > touch[0][1]["x_out"], "N4Pro swipe-left -> drag left")


def test_brightness():
    print("brightness:")
    deck, dev, stub = make_deck("StreamDock293V3")
    deck.set_brightness(75)
    check(stub.brightness == 75, "brightness 75 -> 75")
    deck.set_brightness(0.5)
    check(stub.brightness == 50, "brightness 0.5 (fraction) -> 50")
    deck.set_brightness(250)
    check(stub.brightness == 100, "brightness clamped to 100")


def test_layouts():
    print("layouts:")
    specs = {
        "StreamDock293V3": (15, (3, 5), 0),
        "StreamDockXL": (32, (4, 8), 2),
        "StreamDockN3": (9, (3, 3), 3),
        "StreamDockM3": (15, (3, 5), 3),
        "K1Pro": (6, (2, 3), 3),
        "StreamDockN4Pro": (10, (2, 5), 4),
    }
    for key, (kc, layout, dials) in specs.items():
        deck, _, _ = make_deck(key)
        check(deck.key_count() == kc, f"{key} key_count == {kc}")
        check(deck.key_layout() == layout, f"{key} key_layout == {layout}")
        check(deck.dial_count() == dials, f"{key} dial_count == {dials}")
        check(deck.is_visual() and not deck.is_touch(), f"{key} visual, not touch")


def test_end_to_end_image_pipeline():
    print("end-to-end image pipeline (BetterDeck + PILHelper):")
    from PIL import Image
    from StreamDeck.ImageHelpers import PILHelper
    from src.backend.DeckManagement.BetterDeck import BetterDeck

    deck, dev, stub = make_deck("StreamDock293V3")
    deck._is_open = True
    better = BetterDeck(deck, rotation=0)

    img = Image.new("RGB", deck.key_image_format()["size"], (10, 20, 30))
    native = PILHelper.to_native_key_format(better, img)
    check(native[:2] == b"\xff\xd8", "PILHelper produced a JPEG (FFD8 magic)")

    better.set_key_image(0, native)            # rotation 0 -> identity -> hw 11
    sent = [d for d, k in stub.images if k == 11]
    check(len(sent) == 1, "BetterDeck key 0 -> hardware key 11")
    check(sent[0] == bytes(native), "exact JPEG bytes forwarded unchanged")

    import io
    decoded = Image.open(io.BytesIO(sent[0]))
    check(decoded.size == (112, 112), f"sent JPEG decodes to 112x112 (got {decoded.size})")


def test_refresh_worker_lifecycle():
    print("refresh worker lifecycle:")
    import time
    deck, dev, stub = make_deck("StreamDock293V3")
    deck.open()
    check(stub.opened and deck.is_open(), "open() opened device + adapter")
    check(stub.report_cfg == (513, 1025, 0, 0), "open() configured HID reports")
    check(deck._refresh_thread.is_alive(), "refresh thread alive")

    before = stub.refreshes
    deck.set_key_image(0, b"\xff\xd8imgdata")
    time.sleep(0.2)
    check(stub.refreshes > before, "set_key_image triggered a panel refresh")
    check((b"\xff\xd8imgdata", 11) in stub.images, "image bytes reached transport")

    deck.close()
    time.sleep(0.1)
    check(stub.closed, "close() closed the device")
    check(not deck._refresh_thread.is_alive(), "refresh thread stopped after close()")


def test_vendor_ids_and_enumerate():
    print("vendor ids + enumerate:")
    check(STREAMDOCK_VENDOR_ID_STRINGS == VENDOR_ID_STRINGS, "DeckManager VID set wired to package")
    check("6603" in VENDOR_ID_STRINGS and "5548" in VENDOR_ID_STRINGS, "known MiraBox VIDs present")
    check("0fd9" not in VENDOR_ID_STRINGS, "Elgato VID not claimed by StreamDock")
    decks = enumerate_stream_dock_decks()
    check(isinstance(decks, list), "enumerate returns a list")
    check(all(getattr(d, "deck_type", None) for d in decks),
          "every enumerated deck looks like a StreamDockDeck (may be [] without hardware)")


def main():
    tests = [
        test_model_data_integrity,
        test_image_routing,
        test_input_decode_and_dispatch,
        test_brightness,
        test_layouts,
        test_end_to_end_image_pipeline,
        test_refresh_worker_lifecycle,
        test_vendor_ids_and_enumerate,
    ]
    for t in tests:
        t()
    print(f"\nAll {_passed} checks passed across {len(tests)} tests.")


if __name__ == "__main__":
    main()
