# StreamDock Lock Display Recovery Design

## Problem

On the MiraBox StreamDock N3, locking the desktop can leave the display frozen
on its pre-lock image while buttons and dials continue working. Runtime logs show
the lock-screen screensaver and unlock page reload both run, with no blocked HID
write or input failure. The unchanged physical image therefore means the N3 is
acknowledging or accepting ordinary display writes without rendering them.

Commit `226ec06` stopped sending the firmware's `HAN` panel-sleep command on
lock, correctly avoiding a command known to wedge some N3 firmware. It also
removed the display recovery from `ScreenSaver.hide()`. Hardware observation
shows that avoiding `HAN` is not sufficient: the ordinary lock-screen blanking
path can still leave the display ignoring the redraw performed on unlock.

The latest suspend/reconnect changes reliably preserve input controls. Lock-only
display recovery must not close the HID handle, reset USB, replace the deck
controller, or re-register callbacks.

## Considered Approaches

### 1. Same-handle display reinitialization on unlock

Restore the N3's software-mode and display state after the active page has been
loaded, using the existing `MOD -> DIS -> LIG` initialization and cached-image
repaint on the open HID handle. This addresses the display-only failure while
leaving the working input transport untouched.

This is the selected approach.

### 2. Full USB recovery on every unlock

Reuse `reinit_panel()`, which closes the HID handle, resets the USB device,
reopens it, and repaints. That sequence is necessary for a disconnected or
post-suspend firmware latch, but it would unnecessarily interrupt working input
on every ordinary unlock.

### 3. Display watchdog

Infer a frozen panel from write acknowledgements or timeouts and recover
automatically. The observed failure still accepts writes, so there is no
reliable software-visible signal that pixels failed to change. A watchdog would
either miss this failure or reset healthy devices.

## Design

Add a distinct `reinit_display()` capability alongside the existing
`reinit_panel()` transport recovery:

- `StreamDockDevice.reinit_display()` reruns `_init_display()` on the current
  `StreamDockHID` instance. It sends software mode, wake, brightness, clear,
  cached images, and refresh. It never calls `close()`, `_usbdevfs_reset()`,
  `_refresh_path()`, or `open()`.
- `StreamDockDeck.reinit_display()` synchronously forwards the display-only
  operation unless a full `reinit_panel()` recovery already owns the device.
  Errors remain isolated and logged at the adapter boundary.
- `BetterDeck.reinit_display()` forwards the optional capability and remains a
  no-op for Elgato, fake, and remote decks.
- `MediaPlayerThread` supports a page-scoped post task which runs after its
  ordinary page tasks and pending image writes. `ScreenSaver.hide()` restores
  the active/default page, then queues `reinit_display()` there. The page writes
  therefore update the StreamDock device's image cache even when the firmware
  ignores their initial transmission; display initialization then pushes those
  current images again.

`StreamDockDeck.reinit_display()` skips the light operation when a full recovery
already owns the device. It deliberately does not hold a second adapter lock
across HID output. A HID write can stall indefinitely on a wedged endpoint, so
making `reinit_panel()` wait for a live repaint could prevent the authoritative
close/reset/open path from ever starting. If full recovery begins after a light
repaint has started, it proceeds directly to closing the old transport and USB
recovery rather than waiting at the adapter. The light operation may then fail
benignly; full recovery performs its own handshake and cached-image repaint
after reopening. Duplicate full recoveries remain rejected by the existing
recovery lock.

The low-level HID write lock continues to serialize individual display commands
with heartbeat and other output writes. Input reads and callbacks remain on
their existing reader thread and are not stopped or replaced.

## Suspend and Unlock Interaction

Suspend recovery remains unchanged. A disconnected N3 still uses
`reinit_panel()` and its recovery-ownership protections: close, USB reset,
reopen, handshake, and repaint. If unlock occurs while that recovery is active,
`reinit_display()` is a no-op because the full recovery already performs the
same display initialization after reopening. Page updates made during recovery
still populate the device image cache for that repaint.

If suspend recovery starts while the queued unlock repaint is already writing,
the adapter does not make full recovery wait for that output operation. It can
proceed immediately to the existing close/reset/open sequence, preserving that
path's established priority and input-control ownership.

This separation keeps the proven control recovery authoritative while giving a
connected, input-responsive device the lighter display-only repair it needs.

## Error Handling

- Display-only failures are logged and do not affect input callbacks or deck
  ownership.
- Unsupported decks ignore the optional capability.
- A display request during full transport recovery is skipped rather than
  competing with close/reset/reopen.
- A full transport recovery beginning during a display request proceeds without
  waiting for a potentially blocked HID write.
- A closed HID transport rejects display-only recovery instead of reporting a
  successful no-op.
- The existing delayed remove-and-reenumerate fallback remains responsible for
  failed suspend recovery; lock-only recovery does not add another fallback.

## Tests

Regression coverage will prove:

1. Unlock restores the page and queues display reinitialization after its image
   writes have updated the device cache.
2. Unlock requests display-only recovery and does not request full panel/USB
   recovery.
3. The StreamDock display-only path performs the mode/wake/brightness/repaint
   sequence without closing or reopening the HID transport.
4. A full recovery already in progress suppresses the display-only request.
5. A full recovery starting during a blocked display-only request is not
   blocked, while input events continue to reach callbacks.
6. Existing lock policy still avoids `HAN` panel sleep.
7. The StreamDock transport, reconnect, and lock-screen suites remain green.

Hardware acceptance is one lock/unlock and one suspend/lock/unlock cycle on the
attached N3. The active page must repaint after unlock, and buttons/dials must
continue working without an application restart or physical replug.

## Non-goals

This change does not alter suspend detection, USB reenumeration, recovery
ownership, the `HAN` policy, generic screensaver behavior, or device protocol
encoding. It does not attempt to detect arbitrary display freezes without a
known lifecycle transition.
