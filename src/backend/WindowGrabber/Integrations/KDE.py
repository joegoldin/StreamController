"""
Author: flifloo
Year: 2025

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
any later version.

This programm comes with ABSOLUTELY NO WARRANTY!

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

KWin window tracking through one persistent, event-driven script.
"""
import json
from pathlib import Path
import tempfile
import uuid
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib
from loguru import logger as log

from src.backend.WindowGrabber.Integration import Integration
from src.backend.WindowGrabber.Window import Window

if TYPE_CHECKING:
    from src.backend.WindowGrabber.WindowGrabber import WindowGrabber


CALLBACK_XML = """<node><interface name="com.core447.StreamController.WindowWatcher">
<method name="UpdateWindows"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>"""


class KDE(Integration):
    DBUS_INTERFACE = "com.core447.StreamController.WindowWatcher"

    def __init__(self, window_grabber: "WindowGrabber"):
        super().__init__(window_grabber=window_grabber)
        self.DBUS_PATH = "/com/core447/StreamController/WindowWatcher/" + uuid.uuid4().hex
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._owner = None
        self._windows = []
        self._active_window = None
        self._generation = 0
        self._cancellable = Gio.Cancellable()
        self._script_file = None
        # A stable name also removes a script left behind by an app crash.
        self._script_name = "streamcontroller-window-watcher"
        self._retry_source = 0
        self._retry_delay = 1
        self._registration = self._bus.register_object(
            self.DBUS_PATH, Gio.DBusNodeInfo.new_for_xml(CALLBACK_XML).interfaces[0],
            self._receive_windows, None, None,
        )
        # Do not auto-start a compositor or poll a missing service. A fresh owner
        # installs a fresh script, including after a compositor restart.
        self._watch = Gio.bus_watch_name_on_connection(
            self._bus, "org.kde.KWin", Gio.BusNameWatcherFlags.NONE,
            self._name_appeared, self._name_vanished,
        )

    def _name_appeared(self, connection, name, owner):
        if not self.running:
            return
        self._reset()
        self._owner = owner
        self._retry_delay = 1
        self._install_script()

    def _name_vanished(self, connection, name):
        self._reset()
        self._owner = None

    def _reset(self, unload=False):
        self._generation += 1
        self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        if self._script_file is not None:
            if unload and self._owner:
                self._bus.call(
                    self._owner, "/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                    GLib.Variant("(s)", (self._script_name,)), None,
                    Gio.DBusCallFlags.NO_AUTO_START, 1500, None, self._unloaded,
                )
            self._script_file.unlink(missing_ok=True)
            self._script_file = None
        self._windows = []
        self._active_window = None

    @staticmethod
    def _unloaded(connection, result):
        try:
            connection.call_finish(result)
        except GLib.Error as error:
            log.debug(f"KWin watcher cleanup failed: {error.message}")

    def _call(self, path, interface, method, parameters, callback):
        generation = self._generation

        def finished(connection, result):
            try:
                reply = connection.call_finish(result)
            except GLib.Error as error:
                if self.running and generation == self._generation:
                    self._install_failed(error.message)
                return
            if self.running and generation == self._generation:
                callback(reply)

        self._bus.call(self._owner, path, interface, method, parameters, None,
                       Gio.DBusCallFlags.NO_AUTO_START, 1500, self._cancellable, finished)

    def _install_script(self):
        if not self.running or not self._owner:
            return
        self._call("/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                   GLib.Variant("(s)", (self._script_name,)), self._write_script)

    def _write_script(self, reply):
        try:
            # Unlike /tmp, the app cache is host-visible in Flatpak, so KWin
            # can read it without a flatpak-spawn helper or per-event process.
            directory = Path(GLib.get_user_cache_dir()) / "streamcontroller" / "kwin"
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", prefix="watcher-", suffix=".js", dir=directory, delete=False) as script:
                self._script_file = Path(script.name)
                script.write(self._script_source())
        except OSError as error:
            self._install_failed(str(error))
            return
        self._call("/Scripting", "org.kde.kwin.Scripting", "loadScript",
                   GLib.Variant("(ss)", (str(self._script_file), self._script_name)), self._script_loaded)

    def _script_loaded(self, reply):
        script_id = reply.unpack()[0]
        if script_id < 0:
            self._install_failed("KWin rejected the window watcher script")
            return
        self._call(f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run", None, self._script_running)

    def _script_running(self, reply):
        self._retry_delay = 1

    def _install_failed(self, reason):
        owner = self._owner
        self._reset(unload=True)
        if not self.running or not owner:
            return
        log.warning(f"KWin window watcher unavailable; retrying in {self._retry_delay}s: {reason}")
        self._retry_source = GLib.timeout_add_seconds(self._retry_delay, self._retry_install)
        self._retry_delay = min(self._retry_delay * 2, 60)

    def _retry_install(self):
        self._retry_source = 0
        self._install_script()
        return GLib.SOURCE_REMOVE

    def _script_source(self):
        destination = json.dumps(self._bus.get_unique_name())
        path = json.dumps(self.DBUS_PATH)
        interface = json.dumps(self.DBUS_INTERFACE)
        return f"""
function windowId(window) {{ return window ? window.internalId.toString() : ""; }}
function publish() {{
    const windows = workspace.windowList().filter(window => window.normalWindow).map(window => ({{
        id: windowId(window), wm_class: window.resourceClass.toString(), title: window.caption.toString()
    }}));
    callDBus({destination}, {path}, {interface}, "UpdateWindows",
             JSON.stringify(windows), windowId(workspace.activeWindow));
}}
function watch(window) {{ window.captionChanged.connect(publish); }}
workspace.windowList().forEach(watch);
workspace.windowAdded.connect(function(window) {{ watch(window); publish(); }});
workspace.windowRemoved.connect(publish);
workspace.windowActivated.connect(publish);
publish();
"""

    def _receive_windows(self, connection, sender, path, interface, method, parameters, invocation):
        # Only the compositor that owns this generation may publish window data.
        if not self.running or sender != self._owner:
            invocation.return_dbus_error(self.DBUS_INTERFACE + ".AccessDenied", "Not the current KWin owner")
            return
        try:
            serialized, active_id = parameters.unpack()
            rows = json.loads(serialized)
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in ("id", "wm_class", "title"))
                for row in rows
            ):
                raise ValueError("Invalid window snapshot")
        except (ValueError, TypeError):
            invocation.return_dbus_error(self.DBUS_INTERFACE + ".InvalidArgs", "Invalid window snapshot")
            return
        self._windows = [Window(row["wm_class"], row["title"]) for row in rows]
        active = next((window for row, window in zip(rows, self._windows) if row["id"] == active_id), None)
        previous = self._active_window
        self._active_window = active
        # Reply before page switching; rendering or plugin work must not hold a
        # compositor D-Bus call open or accumulate pending replies.
        invocation.return_value(None)
        if active is not None and active != previous:
            self.window_grabber.on_active_window_changed(active)

    def get_all_windows(self) -> list[Window]:
        return list(self._windows)

    def get_active_window(self) -> Window | None:
        return self._active_window

    def stop(self):
        if not self.running:
            return
        super().stop()
        Gio.bus_unwatch_name(self._watch)
        self._reset(unload=True)
        self._bus.unregister_object(self._registration)
