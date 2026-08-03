# StreamDock Lock Display Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the MiraBox N3 display after lock/unlock without interrupting its working button and dial input transport.

**Architecture:** Add a same-handle `reinit_display()` capability from `ScreenSaver` through `BetterDeck` and `StreamDockDeck` to `StreamDockDevice`. Queue it after page image writes so it repaints the restored cache. Keep the existing `reinit_panel()` USB recovery authoritative for suspend/disconnect and able to preempt a stalled display write.

**Tech Stack:** Python 3.14, `unittest`, in-tree StreamDock HID transport, StreamController `BetterDeck` adapter.

## Global Constraints

- Automatic lock handling must not send the StreamDock `HAN` command.
- Lock-only recovery must not close, reset, reopen, or replace the HID input transport.
- Existing suspend detection, USB reset, recovery ownership, controller reconciliation, and callback registration behavior must remain intact.
- The active/default page must be restored before display-only recovery repaints cached images.
- Unsupported deck types must treat display-only recovery as a no-op.

---

### Task 1: Add same-handle lock display recovery

**Files:**
- Modify: `tests/test_lock_screen_panel.py`
- Modify: `tests/test_streamdock.py`
- Modify: `tests/test_streamdock_reconnect.py`
- Modify: `src/backend/DeckManagement/DeckController.py`
- Modify: `src/backend/DeckManagement/Subclasses/ScreenSaver.py`
- Modify: `src/backend/DeckManagement/BetterDeck.py`
- Modify: `src/backend/DeckManagement/StreamDock/device.py`
- Modify: `src/backend/DeckManagement/StreamDockDeck.py`

**Interfaces:**
- Consumes: `ScreenSaver.hide()`, `MediaPlayerThread`'s task pipeline, `StreamDockDevice._init_display()`, `StreamDockDeck._reinit_lock`, and `BetterDeck`'s optional-capability forwarding pattern.
- Produces: a page-scoped after-image task queue, `StreamDockDevice.reinit_display() -> bool`, `StreamDockDeck.reinit_display() -> bool`, and `BetterDeck.reinit_display() -> bool`.

- [ ] **Step 1: Write failing unlock and after-image ordering regressions**

Update the lock-screen fakes and replace the current no-reinitialization test with:

```python
class _Deck:
    def __init__(self):
        self.sleep_calls = 0
        self.reinit_calls = 0
        self.display_reinit_calls = 0
        self.events = []

    def sleep_panel(self):
        self.sleep_calls += 1

    def reinit_panel(self):
        self.reinit_calls += 1

    def reinit_display(self):
        self.display_reinit_calls += 1
        self.events.append("reinit_display")
        return True


class _DeckController:
    def __init__(self, deck):
        self.deck = deck
        self.active_page = object()
        self.loaded_pages = []

    def clear(self):
        pass

    # Provide a fake media player which records and later runs post tasks.


def test_screensaver_hide_queues_display_reinit_after_page_restore(self):
    deck = _Deck()
    controller = _DeckController(deck)
    screen_saver = ScreenSaver(controller)
    screen_saver.showing = True

    try:
        screen_saver.hide()
    finally:
        if screen_saver.timer:
            screen_saver.timer.cancel()

    self.assertFalse(screen_saver.showing)
    self.assertEqual(controller.loaded_pages, [(controller.active_page, True)])
    self.assertEqual(deck.events, ["load_page", "queue_post_task"])
    self.assertEqual(deck.display_reinit_calls, 0)
    controller.media_player.run_post_tasks()
    self.assertEqual(deck.display_reinit_calls, 1)
    self.assertEqual(deck.reinit_calls, 0)
```

Add a `MediaPlayerThread` regression which queues a page task that adds an image
write, followed by a post task. Assert the display reinitialization snapshot
contains the newly written image. Also cover the default-page branch.

- [ ] **Step 2: Write failing transport-preservation tests**

Extend `StubHID` in `tests/test_streamdock.py` with call counters:

```python
self.open_calls = 0
self.close_calls = 0
self.wakeups = 0
self.mode = None

def open(self, path):
    self.open_calls += 1
    self.opened = True
    self.closed = False
    return True

def close(self):
    self.close_calls += 1
    self.closed = True

def wakeup_screen(self):
    self.wakeups += 1
```

Add and register this smoke test before `test_refresh_worker_lifecycle`:

```python
def test_display_reinit_preserves_input_transport():
    print("display-only reinitialization:")
    deck, dev, stub = make_deck("StreamDockN3", pid=0x1003)
    check(not dev.reinit_display(), "closed HID transport is rejected")
    stub.open(dev.path)
    stub.open_calls = 0
    dev._last_brightness = 42
    dev._last_images = {1: b"CACHED"}

    check(dev.reinit_display(), "display reinitialization succeeds")
    check(dev.hid is stub, "display reinitialization keeps the HID transport")
    check(stub.open_calls == 0, "display reinitialization does not reopen HID")
    check(stub.close_calls == 0, "display reinitialization does not close HID")
    check(stub.sleeps == 0, "display reinitialization never sends panel sleep")
    check(stub.mode == StreamDockHID.MODE_SOFTWARE, "display returns to software mode")
    check(stub.wakeups == 1, "display is explicitly woken")
    check(stub.brightness == 42, "cached brightness is restored")
    check((b"CACHED", 1) in stub.images, "cached key image is repainted")
    check(stub.refreshes == 1, "repaint is committed to the panel")
```

- [ ] **Step 3: Write failing adapter and recovery-interleaving tests**

Give `_RecoveringDevice` in `tests/test_streamdock_reconnect.py` a display call counter and method:

```python
self.display_calls = 0

def reinit_display(self):
    self.display_calls += 1
    return True
```

Add these tests to `StreamDockReconnectTests`:

```python
def test_display_reinit_preserves_input_delivery(self):
    device = _RecoveringDevice()
    deck = StreamDockDeck(device)
    key_events = []
    deck.set_key_callback(lambda _deck, key, pressed: key_events.append((key, pressed)))

    self.assertTrue(deck.reinit_display())

    self.assertEqual(device.display_calls, 1)
    device.input_callback(device, SimpleNamespace(type="key", index=0, pressed=True))
    self.assertEqual(key_events, [(0, True)])

def test_display_reinit_is_suppressed_during_full_recovery(self):
    device = _RecoveringDevice()
    deck = StreamDockDeck(device)

    try:
        self.assertTrue(deck.reinit_panel())
        self.assertTrue(device.entered.wait(timeout=1))
        self.assertFalse(deck.reinit_display())
    finally:
        device.release.set()

    self.assertEqual(device.display_calls, 0)

def test_better_deck_forwards_display_reinit(self):
    wrapped = _RecoveringDevice()
    deck = object.__new__(BetterDeck)
    deck.deck = wrapped

    self.assertTrue(deck.reinit_display())
    self.assertEqual(wrapped.display_calls, 1)
```

Add a blocking display fake and prove that `reinit_panel()` reaches the device
while `reinit_display()` is blocked. Send key events before and after the full
recovery begins and assert both reach the registered callback. This prevents a
new display operation from blocking the established suspend recovery path.

- [ ] **Step 4: Run the targeted tests and verify RED**

Run from a configured StreamController Python environment:

```bash
python3 tests/test_lock_screen_panel.py
python3 tests/test_streamdock.py
python3 tests/test_streamdock_reconnect.py
```

Expected: the lock-screen test fails because `ScreenSaver.hide()` does not call `reinit_display`; the StreamDock tests fail because the new `reinit_display()` methods do not exist.

- [ ] **Step 5: Implement the same-handle device operation**

Add directly before `StreamDockDevice.sleep_panel()`:

```python
def reinit_display(self) -> bool:
    """Reinitialize and repaint the display on the current HID transport."""
    if not self.is_open:
        return False
    try:
        self._init_display()
        return True
    except Exception as e:
        log.error(f"StreamDock {self.serial_number} display re-init failed: {e}")
        return False
```

This intentionally delegates to `_init_display()` and never calls `close()`, `_usbdevfs_reset()`, `_refresh_path()`, or `open()`.

- [ ] **Step 6: Keep full recovery authoritative without blocking on display output**

Add before `reinit_panel()`:

```python
def reinit_display(self) -> bool:
    """Reinitialize display output without interrupting the input transport."""
    if self._reinit_lock.locked():
        return False
    try:
        return bool(self.device.reinit_display())
    except Exception as e:
        log.error(f"StreamDock display re-init failed: {e}")
        return False
```

Leave the existing full recovery worker independent of display output:

```python
def _run():
    try:
        self.device.reinit_panel()
        self._mark_dirty()
    except Exception as e:
        log.error(f"StreamDock reinit_panel failed: {e}")
    finally:
        self._reinit_lock.release()
```

Do not change how `_reinit_lock` is acquired, exposed, or released. Do not add a
lock which could make close/reset/open wait behind an indefinitely blocked HID
write.

- [ ] **Step 7: Add the after-image task hook, then queue optional recovery**

Add a page-scoped `post_tasks` collection to `MediaPlayerThread`, include it in
the pending-work check, and run those tasks after ordinary tasks, image tasks,
and the touchscreen task. Clear post tasks when page loading clears stale media
work.

Add next to `BetterDeck.reinit_panel()`:

```python
def reinit_display(self) -> bool:
    """Reinitialize display output without resetting a working input transport."""
    fn = getattr(self.deck, "reinit_display", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            pass
    return False
```

At the end of `ScreenSaver.hide()`, after page loading and timer setup, add:

```python
# Some single-screen StreamDock panels can keep accepting input while
# ignoring display writes after lock. Reinitialize display output on the
# existing HID handle after the restored page has populated its image cache.
reinit_display = getattr(self.deck_controller.deck, "reinit_display", None)
if callable(reinit_display):
    self.deck_controller.media_player.add_post_task(reinit_display)
```

Do not call `sleep_panel()` or `reinit_panel()` from lock handling.

- [ ] **Step 8: Run targeted tests and verify GREEN**

Run:

```bash
python3 tests/test_lock_screen_panel.py
python3 tests/test_streamdock.py
python3 tests/test_streamdock_reconnect.py
```

Expected: all lock-screen and reconnect unit tests pass, and all StreamDock smoke checks pass with no failure output.

- [ ] **Step 9: Verify the complete change and commit**

Run:

```bash
python3 -m compileall -q src/backend/DeckManagement tests
git diff --check
git status --short
```

Review the diff to confirm the lock path contains no `HAN`, `sleep_panel()`, `reinit_panel()`, HID close, or USB reset call. Then commit only the implementation and regression tests:

```bash
git add src/backend/DeckManagement/BetterDeck.py \
  src/backend/DeckManagement/DeckController.py \
  src/backend/DeckManagement/StreamDock/device.py \
  src/backend/DeckManagement/StreamDockDeck.py \
  src/backend/DeckManagement/Subclasses/ScreenSaver.py \
  tests/test_lock_screen_panel.py \
  tests/test_streamdock.py \
  tests/test_streamdock_reconnect.py
git commit -m "fix(streamdock): restore display after unlock"
```
