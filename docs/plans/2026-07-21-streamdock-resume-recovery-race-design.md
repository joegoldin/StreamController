# StreamDock Resume Recovery Race Design

## Problem

After the host resumes from suspend, the MiraBox N3 can keep delivering button
input while its display remains frozen on the pre-suspend image. The live logs
captured the failing order:

1. The reader marks the old HID handle disconnected.
2. `DeckManager.reconnect_disconnected_decks()` starts the StreamDock's
   asynchronous USB reset and reopen.
3. The same method immediately calls `connect_new_decks()`.
4. Fast-replug reconciliation sees the recovering controller as disconnected,
   removes it, and closes it while its reset thread is settling.
5. The reset thread then fails to reopen the controller, leaving the firmware's
   frozen-display latch uncleared.

Normal hotplug reconciliation must continue replacing genuinely stale
controllers, including when Linux reuses the same HID path.

## Design

Model panel recovery as an explicit, thread-safe controller state.

- `StreamDockDeck.reinit_panel()` acquires its recovery lock synchronously,
  before spawning the background reset thread. It returns whether a recovery
  was started. A concurrent request is rejected as an existing single-flight
  recovery.
- The background thread always releases the lock in `finally`, including reset
  failures. If thread startup itself fails, the caller releases the lock and
  reports the error.
- `StreamDockDeck.reinitializing()` exposes the lock state. `BetterDeck`
  forwards the capability and defaults to `False` for other deck types.
- `DeckManager._remove_disconnected_decks()` skips a disconnected controller
  while `reinitializing()` is true. It still removes all other disconnected
  controllers exactly as it does today.
- The existing ten-second resume fallback remains authoritative. Once recovery
  finishes, a still-disconnected controller is removed and enumerated again;
  a successfully reopened controller stays in place with its cached images and
  callbacks intact.

This state check belongs in the shared stale-controller cleanup rather than
only in the resume caller. That also protects recovery from a concurrent udev
connect event invoking `connect_new_decks()` during the reset.

## Runtime Flow

On resume, the old controller is marked disconnected and starts recovery. Its
recovery state becomes visible before `reinit_panel()` returns. Immediate deck
enumeration may run, but stale cleanup preserves that controller while the USB
reset, HID reopen, software-mode handshake, and cached-image repaint complete.
The device's successful `open()` clears its disconnected state. If it does not,
the existing timeout removes and re-enumerates it after recovery has had time to
finish.

Lock-screen recovery uses the same single-flight state. An unlock arriving
while resume recovery is active does not start a competing reset; the active
recovery owns the device until completion.

## Error Handling

- A duplicate recovery request is a benign no-op and reports that it did not
  start another thread.
- A failure to start the worker cannot leave the recovery lock stuck.
- Device reset or reopen failures retain the existing device-level logs and are
  handled by the delayed DeckManager fallback.
- Non-StreamDock controllers are unaffected because their forwarded recovery
  state is always false.

## Tests

Regression coverage will prove both sides of the lifecycle boundary:

1. Recovery state is visible immediately after `reinit_panel()` returns, stays
   true while the reset worker is blocked, suppresses a duplicate recovery, and
   clears after completion.
2. Reconciliation preserves a disconnected controller that is actively
   recovering and does not create a duplicate replacement.
3. Existing fast-replug behavior still removes and closes a disconnected
   controller that is not recovering, then adds the replacement even when its
   HID path is reused.
4. The full StreamDock transport/device suite remains green.

Hardware acceptance requires one suspend/resume cycle with the N3 attached:
the display must repaint without a physical replug or application restart, and
button/dial input must continue working.

## Non-goals

This change does not alter USB reset commands, HID packet encoding, suspend
detection, screen-saver policy, or generic device enumeration. It also does not
attempt automatic recovery for unrelated random freezes; existing stalled-write
telemetry remains in place for those failures.
