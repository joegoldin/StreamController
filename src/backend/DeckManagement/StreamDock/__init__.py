"""
First-class StreamDock (MiraBox) HID support for StreamController.

A self-contained, pure-Python implementation of the reverse-engineered
StreamDock HID protocol -- no submodule and no closed-source vendor libraries.

  * :mod:`.protocol` -- low-level HID I/O + CRT command encoding
  * :mod:`.models`   -- per-device data tables (grids, image formats, key maps)
  * :mod:`.device`   -- device runtime (open/close, image/brightness, input)
"""

from .models import StreamDockModel, MODELS, PRODUCTS, VENDOR_ID_STRINGS, model_for
from .protocol import StreamDockHID
from .device import StreamDockDevice, StreamDockInput, enumerate_stream_dock_devices

__all__ = [
    "StreamDockModel",
    "MODELS",
    "PRODUCTS",
    "VENDOR_ID_STRINGS",
    "model_for",
    "StreamDockHID",
    "StreamDockDevice",
    "StreamDockInput",
    "enumerate_stream_dock_devices",
]
