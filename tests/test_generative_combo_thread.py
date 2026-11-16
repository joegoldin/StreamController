"""Plugin device observers may repopulate rows from worker threads."""
import threading
import time
import unittest
from types import SimpleNamespace

from gi.repository import GLib
from GtkHelper.GenerativeUI.ComboRow import ComboRow


class GenerativeComboThreadTests(unittest.TestCase):
    def test_worker_populate_marshals_entire_model_and_signal_transaction(self):
        calls = []
        errors = []

        def record(name, result=None):
            def call(*args):
                calls.append((name, threading.current_thread()))
                return result
            return call

        row = object.__new__(ComboRow)
        row._widget = SimpleNamespace(
            convert_item_list=record("convert", ["one", "two"]),
            set_model=record("set_model"),
            model=SimpleNamespace(remove_all=record("clear"), append=record("append")),
            set_selected_item=record("select", "two"),
        )
        row.disconnect_signals = record("disconnect")
        row.connect_signals = record("connect")
        row._handle_value_changed = record("changed")

        def populate():
            try:
                row.populate(["one", "two"], "two")
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=populate, daemon=True)
        worker.start()
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 3
        while worker.is_alive() and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            worker.join(0.001)
        self.assertFalse(worker.is_alive(), "populate deadlocked")
        self.assertEqual(errors, [])
        self.assertTrue(calls)
        self.assertEqual(calls[0][0], "disconnect")
        self.assertEqual(calls[-1][0], "connect")
        self.assertTrue(all(thread is threading.main_thread() for _, thread in calls), calls)
