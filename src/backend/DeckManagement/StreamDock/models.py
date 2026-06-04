"""
StreamDock device models -- data tables for MiraBox StreamDock hardware.

Auto-generated from the reverse-engineered StreamDock protocol. Each model
captures its key grid, image format, HID report configuration, and the
hardware<->logical maps for setting images and decoding input reports.

Maps:
  image_key_map   grid index (0-based, row-major) -> hardware key for images
  button_map      input report code               -> grid index
  knob_rotate_map input report code               -> (dial index, +1 CW / -1 CCW)
  knob_press_map  input report code               -> dial index
  swipe_map       input report code               -> +1 right / -1 left
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StreamDockModel:
    key: str
    name: str
    rows: int
    cols: int
    dials: int
    key_image_size: tuple
    key_image_rotation: int
    key_image_flip: tuple
    report_input: int
    report_output: int
    report_feature: int
    report_id: int
    code_offset: int  # byte offset of the event code in an input report
    image_key_map: dict = field(default_factory=dict)
    button_map: dict = field(default_factory=dict)
    knob_rotate_map: dict = field(default_factory=dict)
    knob_press_map: dict = field(default_factory=dict)
    swipe_map: dict = field(default_factory=dict)

    @property
    def key_count(self) -> int:
        return self.rows * self.cols

    @property
    def key_image_format(self) -> dict:
        return {
            "size": self.key_image_size,
            "format": "JPEG",
            "rotation": self.key_image_rotation,
            "flip": self.key_image_flip,
        }


MODELS = {
    "StreamDock293": StreamDockModel(
        key="StreamDock293", name="StreamDock 293", rows=3, cols=5, dials=0,
        key_image_size=(100, 100), key_image_rotation=180, key_image_flip=(False, False),
        report_input=513, report_output=513, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 11, 0x01: 12, 0x02: 13, 0x03: 14, 0x04: 15, 0x05: 6, 0x06: 7, 0x07: 8, 0x08: 9, 0x09: 10, 0x0a: 1, 0x0b: 2, 0x0c: 3, 0x0d: 4, 0x0e: 5},
        button_map={0x01: 10, 0x02: 11, 0x03: 12, 0x04: 13, 0x05: 14, 0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0a: 9, 0x0b: 0, 0x0c: 1, 0x0d: 2, 0x0e: 3, 0x0f: 4},
        knob_rotate_map={},
        knob_press_map={},
        swipe_map={},
    ),
    "StreamDock293V3": StreamDockModel(
        key="StreamDock293V3", name="StreamDock 293 V3", rows=3, cols=5, dials=0,
        key_image_size=(112, 112), key_image_rotation=180, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 11, 0x01: 12, 0x02: 13, 0x03: 14, 0x04: 15, 0x05: 6, 0x06: 7, 0x07: 8, 0x08: 9, 0x09: 10, 0x0a: 1, 0x0b: 2, 0x0c: 3, 0x0d: 4, 0x0e: 5},
        button_map={0x01: 10, 0x02: 11, 0x03: 12, 0x04: 13, 0x05: 14, 0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0a: 9, 0x0b: 0, 0x0c: 1, 0x0d: 2, 0x0e: 3, 0x0f: 4},
        knob_rotate_map={},
        knob_press_map={},
        swipe_map={},
    ),
    "StreamDock293s": StreamDockModel(
        key="StreamDock293s", name="StreamDock 293s", rows=3, cols=5, dials=0,
        key_image_size=(85, 85), key_image_rotation=90, key_image_flip=(False, False),
        report_input=513, report_output=513, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 13, 0x01: 10, 0x02: 7, 0x03: 4, 0x04: 1, 0x05: 14, 0x06: 11, 0x07: 8, 0x08: 5, 0x09: 2, 0x0a: 15, 0x0b: 12, 0x0c: 9, 0x0d: 6, 0x0e: 3},
        button_map={0x01: 4, 0x02: 9, 0x03: 14, 0x04: 3, 0x05: 8, 0x06: 13, 0x07: 2, 0x08: 7, 0x09: 12, 0x0a: 1, 0x0b: 6, 0x0c: 11, 0x0d: 0, 0x0e: 5, 0x0f: 10},
        knob_rotate_map={},
        knob_press_map={},
        swipe_map={},
    ),
    "StreamDock293sV3": StreamDockModel(
        key="StreamDock293sV3", name="StreamDock 293s V3", rows=3, cols=5, dials=0,
        key_image_size=(96, 96), key_image_rotation=90, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 13, 0x01: 10, 0x02: 7, 0x03: 4, 0x04: 1, 0x05: 14, 0x06: 11, 0x07: 8, 0x08: 5, 0x09: 2, 0x0a: 15, 0x0b: 12, 0x0c: 9, 0x0d: 6, 0x0e: 3},
        button_map={0x01: 4, 0x02: 9, 0x03: 14, 0x04: 3, 0x05: 8, 0x06: 13, 0x07: 2, 0x08: 7, 0x09: 12, 0x0a: 1, 0x0b: 6, 0x0c: 11, 0x0d: 0, 0x0e: 5, 0x0f: 10},
        knob_rotate_map={},
        knob_press_map={},
        swipe_map={},
    ),
    "StreamDockN3": StreamDockModel(
        key="StreamDockN3", name="StreamDock N3", rows=2, cols=3, dials=3,
        key_image_size=(64, 64), key_image_rotation=-90, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 1, 0x01: 2, 0x02: 3, 0x03: 4, 0x04: 5, 0x05: 6},
        button_map={0x01: 0, 0x02: 1, 0x03: 2, 0x04: 3, 0x05: 4, 0x06: 5},
        knob_rotate_map={0x50: (2, -1), 0x51: (2, 1), 0x60: (1, -1), 0x61: (1, 1), 0x90: (0, -1), 0x91: (0, 1)},
        knob_press_map={0x33: 0, 0x34: 1, 0x35: 2},
        swipe_map={},
    ),
    "StreamDockN4": StreamDockModel(
        key="StreamDockN4", name="StreamDock N4", rows=2, cols=5, dials=0,
        key_image_size=(112, 112), key_image_rotation=180, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 11, 0x01: 12, 0x02: 13, 0x03: 14, 0x04: 15, 0x05: 6, 0x06: 7, 0x07: 8, 0x08: 9, 0x09: 10},
        button_map={0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0a: 9, 0x0b: 0, 0x0c: 1, 0x0d: 2, 0x0e: 3, 0x0f: 4},
        knob_rotate_map={},
        knob_press_map={},
        swipe_map={},
    ),
    "StreamDockN1": StreamDockModel(
        key="StreamDockN1", name="StreamDock N1", rows=3, cols=5, dials=1,
        key_image_size=(96, 96), key_image_rotation=0, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 1, 0x01: 2, 0x02: 3, 0x03: 4, 0x04: 5, 0x05: 6, 0x06: 7, 0x07: 8, 0x08: 9, 0x09: 10, 0x0a: 11, 0x0b: 12, 0x0c: 13, 0x0d: 14, 0x0e: 15},
        button_map={0x01: 0, 0x02: 1, 0x03: 2, 0x04: 3, 0x05: 4, 0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0a: 9, 0x0b: 10, 0x0c: 11, 0x0d: 12, 0x0e: 13, 0x0f: 14},
        knob_rotate_map={0x32: (0, -1), 0x33: (0, 1)},
        knob_press_map={0x23: 0},
        swipe_map={},
    ),
    "StreamDockN4Pro": StreamDockModel(
        key="StreamDockN4Pro", name="StreamDock N4 Pro", rows=2, cols=5, dials=4,
        key_image_size=(112, 112), key_image_rotation=180, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 11, 0x01: 12, 0x02: 13, 0x03: 14, 0x04: 15, 0x05: 6, 0x06: 7, 0x07: 8, 0x08: 9, 0x09: 10},
        button_map={0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0a: 9, 0x0b: 0, 0x0c: 1, 0x0d: 2, 0x0e: 3, 0x0f: 4},
        knob_rotate_map={0x50: (1, -1), 0x51: (1, 1), 0x70: (3, -1), 0x71: (3, 1), 0x90: (2, -1), 0x91: (2, 1), 0xa0: (0, -1), 0xa1: (0, 1)},
        knob_press_map={0x33: 2, 0x35: 1, 0x36: 3, 0x37: 0},
        swipe_map={0x38: -1, 0x39: 1},
    ),
    "StreamDockXL": StreamDockModel(
        key="StreamDockXL", name="StreamDock XL", rows=4, cols=8, dials=2,
        key_image_size=(80, 80), key_image_rotation=180, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 25, 0x01: 26, 0x02: 27, 0x03: 28, 0x04: 29, 0x05: 30, 0x06: 31, 0x07: 32, 0x08: 17, 0x09: 18, 0x0a: 19, 0x0b: 20, 0x0c: 21, 0x0d: 22, 0x0e: 23, 0x0f: 24, 0x10: 9, 0x11: 10, 0x12: 11, 0x13: 12, 0x14: 13, 0x15: 14, 0x16: 15, 0x17: 16, 0x18: 1, 0x19: 2, 0x1a: 3, 0x1b: 4, 0x1c: 5, 0x1d: 6, 0x1e: 7, 0x1f: 8},
        button_map={0x01: 24, 0x02: 25, 0x03: 26, 0x04: 27, 0x05: 28, 0x06: 29, 0x07: 30, 0x08: 31, 0x09: 16, 0x0a: 17, 0x0b: 18, 0x0c: 19, 0x0d: 20, 0x0e: 21, 0x0f: 22, 0x10: 23, 0x11: 8, 0x12: 9, 0x13: 10, 0x14: 11, 0x15: 12, 0x16: 13, 0x17: 14, 0x18: 15, 0x19: 0, 0x1a: 1, 0x1b: 2, 0x1c: 3, 0x1d: 4, 0x1e: 5, 0x1f: 6, 0x20: 7},
        knob_rotate_map={0x21: (0, 1), 0x23: (0, -1), 0x24: (1, -1), 0x26: (1, 1)},
        knob_press_map={},
        swipe_map={},
    ),
    "StreamDockM18": StreamDockModel(
        key="StreamDockM18", name="StreamDock M18", rows=3, cols=5, dials=0,
        key_image_size=(64, 64), key_image_rotation=0, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 11, 0x01: 12, 0x02: 13, 0x03: 14, 0x04: 15, 0x05: 6, 0x06: 7, 0x07: 8, 0x08: 9, 0x09: 10, 0x0a: 1, 0x0b: 2, 0x0c: 3, 0x0d: 4, 0x0e: 5},
        button_map={0x01: 10, 0x02: 11, 0x03: 12, 0x04: 13, 0x05: 14, 0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0a: 9, 0x0b: 0, 0x0c: 1, 0x0d: 2, 0x0e: 3, 0x0f: 4},
        knob_rotate_map={},
        knob_press_map={},
        swipe_map={},
    ),
    "StreamDockM3": StreamDockModel(
        key="StreamDockM3", name="StreamDock M3", rows=3, cols=5, dials=3,
        key_image_size=(96, 96), key_image_rotation=90, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=0, code_offset=9,
        image_key_map={0x00: 11, 0x01: 12, 0x02: 13, 0x03: 14, 0x04: 15, 0x05: 6, 0x06: 7, 0x07: 8, 0x08: 9, 0x09: 10, 0x0a: 1, 0x0b: 2, 0x0c: 3, 0x0d: 4, 0x0e: 5},
        button_map={0x01: 10, 0x02: 11, 0x03: 12, 0x04: 13, 0x05: 14, 0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0a: 9, 0x0b: 0, 0x0c: 1, 0x0d: 2, 0x0e: 3, 0x0f: 4},
        knob_rotate_map={0x50: (0, -1), 0x51: (0, 1), 0x90: (1, -1), 0x91: (1, 1), 0xa0: (2, -1), 0xa1: (2, 1)},
        knob_press_map={0x33: 1, 0x35: 0, 0x37: 2},
        swipe_map={},
    ),
    "K1Pro": StreamDockModel(
        key="K1Pro", name="StreamDock K1 Pro", rows=2, cols=3, dials=3,
        key_image_size=(64, 64), key_image_rotation=-90, key_image_flip=(False, False),
        report_input=513, report_output=1025, report_feature=0, report_id=4, code_offset=10,
        image_key_map={0x00: 5, 0x01: 3, 0x02: 1, 0x03: 6, 0x04: 4, 0x05: 2},
        button_map={0x01: 2, 0x02: 5, 0x03: 1, 0x04: 4, 0x05: 0, 0x06: 3},
        knob_rotate_map={0x50: (0, -1), 0x51: (0, 1), 0x60: (1, -1), 0x61: (1, 1), 0x90: (2, -1), 0x91: (2, 1)},
        knob_press_map={0x25: 0, 0x30: 1, 0x31: 2},
        swipe_map={},
    ),
}

# (vendor_id, product_id) -> model key
PRODUCTS = {
    (0x5500, 0x1001): "StreamDock293",
    (0x6603, 0x1005): "StreamDock293V3",
    (0x6603, 0x1006): "StreamDock293V3",
    (0x6603, 0x1010): "StreamDock293V3",
    (0x5548, 0x6670): "StreamDock293s",
    (0x6603, 0x1014): "StreamDock293sV3",
    (0x6603, 0x1002): "StreamDockN3",
    (0x6603, 0x1003): "StreamDockN3",
    (0x6602, 0x1002): "StreamDockN3",
    (0x6602, 0x1003): "StreamDockN3",
    (0x6602, 0x2929): "StreamDockN3",
    (0x1500, 0x3001): "StreamDockN3",
    (0x6602, 0x1001): "StreamDockN4",
    (0x6603, 0x1007): "StreamDockN4",
    (0x6603, 0x1011): "StreamDockN1",
    (0x6603, 0x1000): "StreamDockN1",
    (0x5548, 0x1008): "StreamDockN4Pro",
    (0x5548, 0x1021): "StreamDockN4Pro",
    (0x5548, 0x1023): "StreamDockN4Pro",
    (0x5548, 0x1028): "StreamDockXL",
    (0x5548, 0x1031): "StreamDockXL",
    (0x6603, 0x1009): "StreamDockM18",
    (0x6603, 0x1012): "StreamDockM18",
    (0x5548, 0x1020): "StreamDockM3",
    (0x6603, 0x1015): "K1Pro",
    (0x6603, 0x1019): "K1Pro",
}

# Lowercase 4-hex-digit vendor id strings, for matching usb-monitor / pyudev events.
VENDOR_ID_STRINGS = {f'{vid:04x}' for vid, _pid in PRODUCTS}


def model_for(vendor_id: int, product_id: int):
    """Return the StreamDockModel for a USB (vid, pid), or None if unknown."""
    key = PRODUCTS.get((vendor_id, product_id))
    return MODELS.get(key) if key else None
