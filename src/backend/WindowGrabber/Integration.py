"""
Author: Core447
Year: 2024

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
any later version.

This programm comes with ABSOLUTELY NO WARRANTY!

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
"""

from src.backend.WindowGrabber.Window import Window

# Import typing
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.backend.WindowGrabber.WindowGrabber import WindowGrabber

class Integration:
    def __init__(self, window_grabber: "WindowGrabber") -> None:
        self.window_grabber = window_grabber
        # Per-integration run flag. Active-window watcher threads must loop on
        # this (in addition to the global gl.threads_running) so a replaced
        # integration can be torn down individually - see stop().
        self.running = True

    def stop(self) -> None:
        """Signal this integration's active-window watcher thread to exit.

        WindowGrabber.init_integration() can run more than once (it is called
        again from onboarding, and once per app start). Each call builds a
        fresh integration with its own watcher thread, but those threads loop
        on the *global* gl.threads_running flag, so the previous one never
        stops until the whole app quits. The orphaned watchers keep polling the
        compositor forever - on KDE that means spawning kdotool (a KWin D-Bus
        script call) every 0.2s - so threads, subprocesses and bus traffic pile
        up without bound. Flipping this per-integration flag lets the old
        watcher exit as soon as it is replaced.
        """
        self.running = False

    def get_all_windows(self) -> list[Window]:
        return []

    def get_active_window(self) -> Window:
        return None