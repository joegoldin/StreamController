"""Exercise the KDE integration against a private bus, without a compositor.

Run under dbus-run-session; the fake KWin exports the real scripting API.
"""
import json
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib

import globals
from src.backend.WindowGrabber.Integrations import KDE as kde_module
from src.backend.WindowGrabber.Window import Window


SCRIPTING_XML = """<node><interface name="org.kde.kwin.Scripting">
<method name="loadScript"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="i" direction="out"/></method>
<method name="unloadScript"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
</interface></node>"""
SCRIPT_XML = """<node><interface name="org.kde.kwin.Script"><method name="run"/></interface></node>"""


def spin_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not predicate():
        while context.pending():
            context.iteration(False)
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for asynchronous D-Bus operation")
        time.sleep(0.001)


class KDEWindowEventTests(unittest.TestCase):
    def setUp(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.grabber = SimpleNamespace(on_active_window_changed=Mock())
        self.loads = []
        self.unloads = []
        self.runs = 0
        self.fail_load = False
        self.registrations = []
        self.name = 0
        self.cli = patch.object(kde_module, "Popen", side_effect=AssertionError("No polling subprocesses"), create=True)
        self.cli_mock = self.cli.start()
        self.kde = None
        self.addCleanup(self.cleanup)
        self.registrations.append(self.bus.register_object(
            "/Scripting", Gio.DBusNodeInfo.new_for_xml(SCRIPTING_XML).interfaces[0], self.scripting, None, None))
        self.registrations.append(self.bus.register_object(
            "/Scripting/Script7", Gio.DBusNodeInfo.new_for_xml(SCRIPT_XML).interfaces[0], self.script, None, None))

    def own_kwin(self):
        self.name = Gio.bus_own_name_on_connection(self.bus, "org.kde.KWin", Gio.BusNameOwnerFlags.NONE, None, None)

    def scripting(self, connection, sender, path, interface, method, parameters, invocation):
        if method == "loadScript":
            filename, name = parameters.unpack()
            self.loads.append((filename, name, Path(filename).read_text()))
            invocation.return_value(GLib.Variant("(i)", (-1 if self.fail_load else 7,)))
        else:
            self.unloads.append(parameters.unpack()[0])
            invocation.return_value(GLib.Variant("(b)", (True,)))

    def script(self, connection, sender, path, interface, method, parameters, invocation):
        self.runs += 1
        invocation.return_value(None)

    def start(self):
        self.kde = kde_module.KDE(self.grabber)
        self.own_kwin()
        spin_until(lambda: self.runs == 1)

    def snapshot(self, windows, active):
        done = []
        self.bus.call(self.bus.get_unique_name(), self.kde.DBUS_PATH, self.kde.DBUS_INTERFACE,
                      "UpdateWindows", GLib.Variant("(ss)", (json.dumps(windows), active)), None,
                      Gio.DBusCallFlags.NONE, 1000, None,
                      lambda bus, result: done.append(bus.call_finish(result)))
        spin_until(lambda: done)

    def cleanup(self):
        if self.kde:
            self.kde.stop()
        if self.name:
            Gio.bus_unown_name(self.name)
        for registration in self.registrations:
            self.bus.unregister_object(registration)
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        self.cli.stop()

    def test_installs_one_persistent_script_and_never_spawns_kdotool(self):
        self.start()
        self.assertEqual(len(self.loads), 1)
        source = self.loads[0][2]
        for event in ("windowActivated", "windowAdded", "windowRemoved", "captionChanged"):
            self.assertIn(event, source)
        self.cli_mock.assert_not_called()

    def test_snapshot_populates_window_queries_and_title_changes(self):
        self.start()
        rows = [{"id": "a", "wm_class": "zen", "title": "Video"}]
        self.snapshot(rows, "a")
        self.assertEqual(self.kde.get_active_window(), Window("zen", "Video"))
        self.assertEqual(self.kde.get_all_windows(), [Window("zen", "Video")])
        self.snapshot(rows, "a")
        self.grabber.on_active_window_changed.assert_called_once()
        rows[0]["title"] = ""
        self.snapshot(rows, "a")
        self.assertEqual(self.kde.get_active_window(), Window("zen", ""))
        self.assertEqual(self.grabber.on_active_window_changed.call_count, 2)
        self.assertEqual(len(self.loads), 1)

    def test_owner_loss_clears_cache_and_reappearance_installs_once(self):
        self.start()
        self.snapshot([{"id": "a", "wm_class": "zen", "title": "Video"}], "a")
        Gio.bus_unown_name(self.name)
        self.name = 0
        spin_until(lambda: self.kde.get_active_window() is None)
        self.assertEqual(self.kde.get_all_windows(), [])
        self.own_kwin()
        spin_until(lambda: self.runs == 2)
        self.assertEqual(len(self.loads), 2)

    def test_absent_kwin_does_not_load_or_poll(self):
        self.kde = kde_module.KDE(self.grabber)
        done = []
        GLib.idle_add(lambda: done.append(True) and False)
        spin_until(lambda: done)
        self.assertEqual(self.kde.get_all_windows(), [])
        self.assertEqual(self.loads, [])
        self.cli_mock.assert_not_called()

    def test_stop_unloads_script_and_removes_temporary_file(self):
        self.start()
        filename, name, source = self.loads[0]
        previous_unloads = len(self.unloads)
        self.kde.stop()
        spin_until(lambda: len(self.unloads) > previous_unloads)
        self.assertEqual(self.unloads[-1], name)
        self.assertFalse(Path(filename).exists())
        self.assertEqual(self.kde.get_all_windows(), [])

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the KWin JavaScript")
    def test_generated_script_emits_actual_window_events(self):
        self.start()
        result = subprocess.run(["node", str(Path(__file__).with_name("kde_window_script.js")),
                                 self.loads[0][0]], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_load_is_backed_off_and_stop_cancels_retry(self):
        self.fail_load = True
        self.kde = kde_module.KDE(self.grabber)
        self.own_kwin()
        spin_until(lambda: self.kde._retry_source != 0)
        self.assertEqual(len(self.loads), 1)
        self.assertEqual(self.runs, 0)
        self.kde.stop()
        self.assertEqual(self.kde._retry_source, 0)


if __name__ == "__main__":
    unittest.main()
