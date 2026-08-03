# StreamDock Output Hardening Design

## Evidence and Scope

The N3 display froze twice on an exact minute boundary: once at `16:55:00`
and again at a displayed `10:55:00`. Both freezes happened before the desktop
locked. Buttons and dials continued to work, and the logs showed neither a
blocked HID write nor a failed input reader. Display reinitialization, a full
StreamController restart, and `USBDEVFS_RESET` all left the old frame visible.
Only removing and restoring USB power cleared the panel firmware state.

After the second physical replug, a controlled probe sent the same clock
content as three separate N3 frames: `10:54:59`, `10:55:00`, and `10:55:01`.
It used the current HID transport without modification but encoded the N3 cell
JPEGs at quality 100 instead of 90. The panel reached `10:55:01`. This does not
prove that JPEG quantization caused both freezes, but it is the first candidate
change to pass the exact content and boundary that had failed in normal use.

This change covers all JPEGs created inside the StreamDock backend, canonical
HIDAPI output-report framing, and keepalive scheduling. It does not change
RemoteDeck image encoding or rewrite JPEG bytes already produced by
StreamController for models that do not need cell framing.

## JPEG Encoding

Use one `JPEG_QUALITY = 100` setting for both StreamDock encoding paths:

- full-screen black images used while initializing or repainting a panel;
- cell-framed key images used by single-screen models such as the N3.

Other StreamDock models receive the native JPEG bytes unchanged. The existing
end-to-end test protects that path. The independent
[stevemurr/streamdock](https://github.com/stevemurr/streamdock) driver also
encodes device images at quality 100, which gives this setting a working
reference outside the vendor SDK.

## Canonical HID Reports

HIDAPI requires the first byte passed to `write()` to be the report ID. A
device without numbered reports still uses `0x00` in that position. The current
transport omits that byte for command reports and adds it only when a bulk
chunk happens to start with zero. That works accidentally with the Linux
backend for most payloads, but a short final chunk beginning with zero loses
one payload byte after padding.

Every output write will use the same encoder:

1. Pad the protocol payload to the model's 512-byte or 1024-byte report size.
2. Reject a payload larger than one protocol report.
3. Prepend the model's configured report ID.

`hid.write()` therefore receives 513 or 1025 bytes. The first byte is the
report ID, followed by the complete command or bulk payload. A zero at the
start of JPEG data remains data at byte one of the HIDAPI buffer. The
[HIDAPI documentation](https://github.com/libusb/hidapi) and the independent
StreamDock driver use this form.

## Keepalive Priority

A `BAT` header and all JPEG chunks must remain contiguous. Inserting
`CONNECT` into the middle would corrupt the image transfer. The existing
reentrant lock preserves that boundary, but it gives a waiting keepalive no
priority over later image transactions.

Replace the lock with a condition-based output gate. One complete CRT
transaction owns the gate at a time. A heartbeat registers as a priority
waiter; once the active transaction finishes, normal transactions remain
blocked until the waiting heartbeat has sent `CONNECT`. The heartbeat never
interrupts an active image and cannot be overtaken indefinitely by newly
queued images. Calls remain synchronous, so HID errors still reach the caller,
and the existing write-stall timestamp still covers the active transaction.

This is scheduling at transaction boundaries, not packet interleaving. No new
writer thread or shutdown queue is needed.

## Tests and Hardware Check

The transport tests will cover the exact output buffer shape for commands,
full bulk chunks, short bulk chunks beginning with zero, and a nonzero report
ID. An oversized payload must fail before any HID write.

A concurrency test will block one image transaction, then queue another image
and a heartbeat. After the first image completes, `CONNECT` must be written
before the second image. The first `BAT` header and its chunks must stay
adjacent.

JPEG tests will compare both generated StreamDock image paths with Pillow's
quality-100 quantization tables. They will also confirm that native JPEG bytes
for an ordinary multi-key model are still forwarded unchanged.

After the automated suite passes, build and run this branch against the N3.
Send the `10:54:59` through `10:55:01` sequence through the updated production
encoder, then leave StreamController running for normal lock and suspend use.
Buttons, dials, and display updates must all continue without a restart or
physical replug.

## Findings Kept Separate

The investigation exposed two other problems that are not part of this patch.
The DBus controller registry can retain an old controller object after a deck
reconnect, and startup currently logs an Elgato `DeviceManager` API mismatch.
Neither was present at the two freeze boundaries, and neither explains a frame
that survives process restart and a logical USB reset.

Software also cannot detect this powered firmware latch: HID output continues
without an error while the pixels stay unchanged. Automatic USB power cycling
is not portable and may reset other devices on the same hub. Prevention is the
safe boundary for this change.

## Sources

- [Phaeilo's pure-Python transport contribution](https://github.com/MiraboxSpace/StreamDock-Device-SDK/pull/76)
- [stevemurr/streamdock](https://github.com/stevemurr/streamdock)
- [4ndv/mirajazz](https://github.com/4ndv/mirajazz)
- [rigor789/mirabox-streamdock-node](https://github.com/rigor789/mirabox-streamdock-node)
- [HIDAPI](https://github.com/libusb/hidapi)

## Non-goals

This patch will not add pixel-level freeze detection, automatic hub power
cycling, DBus registry repair, Elgato startup repair, or a new lock/suspend
policy. It will not send `HAN` during automatic lock handling or weaken the
existing resume recovery ownership rules.
