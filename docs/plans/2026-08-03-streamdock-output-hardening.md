# StreamDock Output Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encode every StreamDock-generated JPEG at quality 100, frame HIDAPI output reports correctly, and prevent image traffic from starving `CONNECT` keepalives.

**Architecture:** Keep the synchronous `StreamDockHID` API and treat each command plus its bulk payload as one output transaction. A shared report encoder prepends the configured HID report ID to a fixed-size protocol payload. A condition gate gives a waiting heartbeat priority after the active transaction finishes, without inserting packets into an image transfer.

**Tech Stack:** Python 3.14, Pillow, hidapi, `threading.Condition`, in-tree smoke tests.

## Global Constraints

- Set every JPEG created by `src/backend/DeckManagement/StreamDock/device.py` to quality 100.
- Forward native JPEG bytes unchanged for StreamDock models that do not use cell framing.
- Pass HIDAPI exactly one report-ID byte followed by the model's 512-byte or 1024-byte protocol payload.
- Keep each `BAT` header and all of its bulk chunks adjacent.
- Give a waiting `CONNECT` heartbeat priority only between complete transactions.
- Keep write calls synchronous and preserve write-stall telemetry.
- Do not change lock/suspend policy, add panel-sleep commands, or power-cycle USB hubs.

---

### Task 1: Encode generated JPEGs at quality 100

**Files:**
- Modify: `tests/test_streamdock.py`
- Modify: `src/backend/DeckManagement/StreamDock/device.py`

**Interfaces:**
- Consumes: `StreamDockDevice._black_jpeg(size) -> bytes` and `StreamDockDevice._frame_key_image(grid_index, jpeg_bytes) -> bytes`.
- Produces: module constant `JPEG_QUALITY = 100`, used by both StreamDock encoding paths.

- [ ] **Step 1: Write the failing JPEG regression**

Add `io` and Pillow imports at the top of `tests/test_streamdock.py`, then add:

```python
def jpeg_bytes(size, color=(10, 20, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=100)
    return buf.getvalue()


def check_quality_100(jpeg, label):
    quantization = Image.open(io.BytesIO(jpeg)).quantization
    check(bool(quantization), f"{label}: JPEG has quantization tables")
    check(
        all(value == 1 for table in quantization.values() for value in table),
        f"{label}: JPEG uses quality 100 quantization",
    )


def test_generated_jpeg_quality():
    print("generated JPEG quality:")
    check_quality_100(StreamDockDevice._black_jpeg((112, 112)), "panel clear")

    model = MODELS["StreamDockN3"]
    device = StreamDockDevice(b"/dev/hidraw_test", 0x6603, 0x1003, "TEST123", model)
    framed = device._frame_key_image(0, jpeg_bytes(model.key_image_size))
    check_quality_100(framed, "N3 framed key")
```

Register `test_generated_jpeg_quality` in `main()` immediately after `test_end_to_end_image_pipeline`.

- [ ] **Step 2: Run the StreamDock test and verify RED**

Run:

```bash
sed 's#  main.py "$@" *$#  "$@"#' /tmp/streamcontroller-mirabox-result-pinned/bin/.streamcontroller-wrapped | bash -s -- /tmp/StreamController-mirabox-display/tests/test_streamdock.py
```

Expected: FAIL at `panel clear: JPEG uses quality 100 quantization` because `_black_jpeg()` still uses quality 85.

- [ ] **Step 3: Add one quality setting to the StreamDock backend**

Add near `_CELL_ROT` in `device.py`:

```python
JPEG_QUALITY = 100
```

Use it in both saves:

```python
Image.new("RGB", tuple(size), (0, 0, 0)).save(
    buf, format="JPEG", quality=JPEG_QUALITY
)
```

```python
canvas.save(buf, format="JPEG", quality=JPEG_QUALITY)
```

- [ ] **Step 4: Run the StreamDock test and verify GREEN**

Run the command from Step 2. Expected: every registered smoke test passes, including both quality-100 checks and the unchanged-byte end-to-end image check.

- [ ] **Step 5: Commit the JPEG change**

```bash
git add src/backend/DeckManagement/StreamDock/device.py tests/test_streamdock.py
git commit -m "fix(streamdock): encode generated JPEGs at quality 100"
```

---

### Task 2: Encode canonical HIDAPI output reports

**Files:**
- Modify: `tests/test_streamdock.py`
- Modify: `src/backend/DeckManagement/StreamDock/protocol.py`

**Interfaces:**
- Consumes: `StreamDockHID._report_size`, configured by `set_report_config()`.
- Produces: `StreamDockHID._encode_report(payload: bytes) -> bytes`, which returns the report-ID byte plus an exact fixed-size payload.

- [ ] **Step 1: Write failing report-framing regressions**

Add this recording backend and test:

```python
class RecordingHIDDevice:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)


def transport_with_recorder(output_size=1025, report_id=0):
    transport = StreamDockHID()
    recorder = RecordingHIDDevice()
    transport._device = recorder
    transport._is_open = True
    transport.set_report_config(513, output_size, 0, report_id)
    return transport, recorder


def test_hid_output_report_framing():
    print("HID output report framing:")

    transport, recorder = transport_with_recorder()
    transport.heartbeat()
    report = recorder.writes[0]
    check(len(report) == 1025, "v3 command includes report ID plus 1024-byte payload")
    check(report[0] == 0, "unnumbered command uses report ID zero")
    check(report[1:13] == b"CRT\x00\x00CONNECT", "command starts after report ID")

    legacy, legacy_recorder = transport_with_recorder(output_size=513)
    legacy.heartbeat()
    check(len(legacy_recorder.writes[0]) == 513, "legacy command uses 512-byte payload")

    numbered, numbered_recorder = transport_with_recorder(report_id=4)
    numbered.heartbeat()
    check(numbered_recorder.writes[0][0] == 4, "configured nonzero report ID is preserved")

    transport, recorder = transport_with_recorder()
    transport.set_key_image(b"\x00AB", 1)
    bulk = recorder.writes[1]
    check(len(bulk) == 1025, "short bulk report has exact HIDAPI length")
    check(bulk[1:4] == b"\x00AB", "zero-leading bulk data is not consumed as report ID")
    check(set(bulk[4:]) == {0}, "short bulk report is zero padded")

    transport, recorder = transport_with_recorder()
    transport.set_key_image(b"A" * 1025, 1)
    check(len(recorder.writes) == 3, "1025-byte image uses a BAT header and two chunks")
    check(all(len(report) == 1025 for report in recorder.writes), "every image report has exact HIDAPI length")
    check(recorder.writes[1][1:] == b"A" * 1024, "full bulk chunk is unchanged")
    check(recorder.writes[2][1:2] == b"A", "final bulk byte is preserved")

    transport, recorder = transport_with_recorder(output_size=513)
    try:
        transport._crt("X" * 508)
    except ValueError:
        pass
    else:
        raise AssertionError("oversized command payload was accepted")
    check(recorder.writes == [], "oversized payload fails before HID write")
```

Register `test_hid_output_report_framing` before `test_write_stall_detection`.

- [ ] **Step 2: Run the StreamDock test and verify RED**

Run the Task 1 test command. Expected: FAIL because the first command report is 1024 bytes and begins with `C`, not a report-ID byte.

- [ ] **Step 3: Implement one report encoder**

Replace `_pad()` with:

```python
def _encode_report(self, payload: bytes) -> bytes:
    size = self._report_size
    if len(payload) > size:
        raise ValueError(
            f"StreamDock output payload is {len(payload)} bytes; report limit is {size}"
        )
    return bytes([self._report_id]) + payload.ljust(size, b"\x00")
```

Inside `_crt()`, encode the command and every chunk through this method:

```python
pkt = crt + cmd.encode("ascii") + params
self._device.write(self._encode_report(pkt))

if bulk:
    size = self._report_size
    for i in range(0, len(bulk), size):
        self._device.write(self._encode_report(bulk[i:i + size]))
```

Delete the conditional zero-prefix workaround and its obsolete HIDAPI comment.

- [ ] **Step 4: Run the StreamDock test and verify GREEN**

Run the Task 1 test command. Expected: all framing, bulk-integrity, and existing smoke checks pass.

- [ ] **Step 5: Commit the framing change**

```bash
git add src/backend/DeckManagement/StreamDock/protocol.py tests/test_streamdock.py
git commit -m "fix(streamdock): frame HID output reports correctly"
```

---

### Task 3: Prioritize keepalives between image transactions

**Files:**
- Modify: `tests/test_streamdock.py`
- Modify: `src/backend/DeckManagement/StreamDock/protocol.py`

**Interfaces:**
- Consumes: synchronous `_crt()` transactions and `heartbeat()`.
- Produces: `_acquire_write(priority: bool)` and `_release_write()`, backed by a `threading.Condition`; `_crt(..., priority=False)`; priority `heartbeat()` calls.

- [ ] **Step 1: Write the failing transaction-order regression**

Add:

```python
class BlockingFirstWriteDevice(RecordingHIDDevice):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self._calls = 0
        self._lock = threading.Lock()

    def write(self, data):
        with self._lock:
            self._calls += 1
            call = self._calls
            self.writes.append(bytes(data))
        if call == 1:
            self.entered.set()
            self.release.wait(timeout=2)
        return len(data)


def wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_heartbeat_priority_preserves_image_transactions():
    print("heartbeat transaction priority:")
    transport = StreamDockHID()
    recorder = BlockingFirstWriteDevice()
    transport._device = recorder
    transport._is_open = True
    transport.set_report_config(513, 1025, 0, 0)

    first = threading.Thread(target=transport.set_key_image, args=(b"A" * 1025, 1))
    second = threading.Thread(target=transport.set_key_image, args=(b"B", 2))
    heartbeat = threading.Thread(target=transport.heartbeat)
    started = []

    try:
        first.start()
        started.append(first)
        check(recorder.entered.wait(timeout=1), "first image owns the output transaction")
        second.start()
        started.append(second)
        check(wait_for(lambda: getattr(transport, "_normal_waiters", 0) == 1), "second image is queued")
        heartbeat.start()
        started.append(heartbeat)
        check(wait_for(lambda: getattr(transport, "_priority_waiters", 0) == 1), "heartbeat is queued with priority")
    finally:
        recorder.release.set()
        for thread in started:
            thread.join(timeout=1)

    for thread in started:
        check(not thread.is_alive(), "queued output thread completed")

    payloads = [report[1:] for report in recorder.writes]
    check(payloads[0].startswith(b"CRT\x00\x00BAT"), "first BAT header is first")
    check(payloads[1] == b"A" * 1024, "first image full chunk stays after its header")
    check(payloads[2][:1] == b"A", "first image final chunk stays in its transaction")
    check(payloads[3].startswith(b"CRT\x00\x00CONNECT"), "waiting heartbeat runs after the active image")
    check(payloads[4].startswith(b"CRT\x00\x00BAT"), "queued image runs after the heartbeat")
```

Register this test immediately before `test_write_stall_detection`.

- [ ] **Step 2: Run the StreamDock test and verify RED**

Run the Task 1 test command. Expected: FAIL at `second image is queued` because the current `RLock` has no priority-aware waiter state.

- [ ] **Step 3: Implement the priority output gate**

Replace `_write_lock` initialization with:

```python
self._write_condition = threading.Condition()
self._write_active = False
self._normal_waiters = 0
self._priority_waiters = 0
```

Add before `_crt()`:

```python
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
```

Change `_crt()` to accept `priority: bool = False`, acquire the gate before checking `_device`, and release it in `finally`:

```python
def _crt(
    self,
    cmd: str,
    params: bytes = b"",
    bulk: bytes = b"",
    crt: bytes = b"CRT\x00\x00",
    priority: bool = False,
):
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
                self._device.write(self._encode_report(bulk[i:i + size]))
    finally:
        self._write_started_at = None
        self._release_write()
```

Give `CONNECT` priority:

```python
def heartbeat(self):
    self._crt("CONNECT", priority=True)
```

- [ ] **Step 4: Run the StreamDock test and verify GREEN**

Run the Task 1 test command. Expected: the first image remains contiguous, `CONNECT` runs next, and the second image follows. All existing smoke checks must pass.

- [ ] **Step 5: Commit the scheduling change**

```bash
git add src/backend/DeckManagement/StreamDock/protocol.py tests/test_streamdock.py
git commit -m "fix(streamdock): prioritize display keepalives"
```

---

### Task 4: Verify, build, run, and publish

**Files:**
- Modify after push: `/home/joe/dotfiles/modules/system/_streamcontroller.nix`
- Update through GitHub: PR `joegoldin/StreamController#2`

**Interfaces:**
- Consumes: the three passing changes above and the existing Nix package override.
- Produces: a running hardware-test build, a pushed feature branch, an updated dotfiles source pin, and a revised PR description.

- [ ] **Step 1: Run the full local regression set**

Run each test through the pinned package's Python environment:

```bash
for test in tests/test_streamdock.py tests/test_streamdock_reconnect.py tests/test_lock_screen_panel.py tests/test_lock_screen_integration.py tests/test_media_player_tasks.py; do sed 's#  main.py "$@" *$#  "$@"#' /tmp/streamcontroller-mirabox-result-pinned/bin/.streamcontroller-wrapped | bash -s -- "/tmp/StreamController-mirabox-display/$test"; done
python3 -m compileall -q src/backend/DeckManagement tests
git diff --check
```

Expected: every test exits zero, `compileall` is silent, and `git diff --check` prints nothing.

- [ ] **Step 2: Build a local Nix package from this worktree**

Build the existing dotfiles package override with the source replaced by the local worktree:

```bash
nix build --impure --out-link /tmp/streamcontroller-mirabox-output-hardening --expr 'let f = builtins.getFlake "/home/joe/dotfiles"; pkgs = f.nixosConfigurations.melina.pkgs; in ((import /home/joe/dotfiles/modules/system/_streamcontroller.nix { inherit pkgs; }).package.overrideAttrs (_: { src = /tmp/StreamController-mirabox-display; version = "1.5.0-beta.15-mirabox-output-hardening"; }))'
```

Expected: `/tmp/streamcontroller-mirabox-output-hardening/bin/streamcontroller` exists.

- [ ] **Step 3: Replace the running hardware-test process**

Stop `streamcontroller.mirabox-after-probe`, start the new binary in a fresh zmx session, and inspect its history until the N3 is open and its page has loaded:

```bash
zmx run streamcontroller.mirabox-after-probe $'\003'
zmx run streamcontroller.mirabox-output-hardening /tmp/streamcontroller-mirabox-output-hardening/bin/streamcontroller -b
zmx history streamcontroller.mirabox-output-hardening
```

Expected: one new StreamController process remains, the N3 initializes in software mode, and the active page loads without a traceback from the changed code.

- [ ] **Step 4: Exercise the exact clock boundary with production code**

Copy the existing probe:

```bash
cp /tmp/n3_quality100_probe.py /tmp/n3_output_hardening_probe.py
```

Replace its manual cell composition with a `StreamDockDevice` encoder:

```python
from src.backend.DeckManagement.StreamDock.device import StreamDockDevice
from src.backend.DeckManagement.StreamDock.models import MODELS

ENCODER = StreamDockDevice(
    b"/dev/hidraw_probe",
    VID,
    PID,
    "PROBE",
    MODELS["StreamDockN3"],
)


def framed_clock_jpeg(text: str) -> bytes:
    font = ImageFont.truetype(FONT_PATH, 14, encoding="unic")
    key = Image.new("RGBA", (85, 85), (0, 0, 0, 0))
    draw = ImageDraw.Draw(key)
    draw.text(
        (42.5, 42.5),
        text=text,
        font=font,
        anchor="mm",
        align="center",
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 255),
    )
    native = io.BytesIO()
    key.convert("RGB").save(native, "JPEG", quality=100)
    return ENCODER._frame_key_image(0, native.getvalue())
```

Stop the app, run the probe through the new package environment, then restart
the app:

```bash
zmx run streamcontroller.mirabox-output-hardening $'\003'
sed 's#  main.py "$@" *$#  "$@"#' /tmp/streamcontroller-mirabox-output-hardening/bin/.streamcontroller-wrapped | bash -s -- /tmp/n3_output_hardening_probe.py
zmx run streamcontroller.mirabox-output-hardening-app /tmp/streamcontroller-mirabox-output-hardening/bin/streamcontroller -b
```

Expected: the panel reaches `10:55:01`; buttons and dials work after StreamController restarts.

- [ ] **Step 5: Push the feature branch**

Fast-forward `/home/joe/Development/StreamController-pr1` to the verified commit, then run:

```bash
git push fork feat/mirabox-support
```

Expected: GitHub PR #2 points at the verified output-hardening commit.

- [ ] **Step 6: Update the dotfiles pin**

Replace the `rev` and fixed-output hash in `_streamcontroller.nix`, and update its comment to mention quality-100 StreamDock encoding, canonical HID reports, and keepalive priority. Preserve the unrelated staged `machine.nix` change.

Run:

```bash
output_commit="$(git rev-parse HEAD)"
nix store prefetch-file --json "https://github.com/joegoldin/StreamController/archive/${output_commit}.tar.gz"
```

Use the returned hash, then evaluate or build the package from the updated module.

- [ ] **Step 7: Rewrite and audit the PR description**

Read the current PR body. Add the two observed `:55:00` freezes, the physical-power-cycle boundary, the quality-100 probe reaching `10:55:01`, canonical HIDAPI report framing, and keepalive scheduling. Credit Phaeilo, Steve Murr, 4ndv, rigor789, and HIDAPI in the research section.

Apply the avoid-AI-writing skill in rewrite mode with technical voice. Run a second audit for em dashes, inflated claims, repetitive structure, vague attribution, and leaked citation tokens. Then update PR #2 and read the published body back once.
