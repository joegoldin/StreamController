"""Regression tests for StreamDock hotplug reconciliation."""

import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import globals  # DeckManager expects application globals to be initialized first.
import src.backend.DeckManagement.DeckManager as deck_manager_module
from src.backend.DeckManagement.BetterDeck import BetterDeck
from src.backend.DeckManagement.StreamDockDeck import StreamDockDeck


class _Deck:
    def __init__(self, deck_id, connected=True, recovering=False):
        self._deck_id = deck_id
        self._connected = connected
        self._recovering = recovering
        self.closed = False

    def id(self):
        return self._deck_id

    def connected(self):
        return self._connected

    def reinitializing(self):
        return self._recovering

    def close(self):
        self.closed = True


class _Controller:
    def __init__(self, deck):
        self.deck = deck


class _DeviceManager:
    def enumerate(self):
        return []


class _RecoveringDevice:
    def __init__(self):
        self.model = SimpleNamespace(key_count=1, dials=0)
        self.path = b"/dev/hidraw11"
        self.vendor_id = 0x6603
        self.product_id = 0x1003
        self.serial_number = "C511D3784530"
        self.input_callback = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def reinit_panel(self):
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=2)


class StreamDockReconnectTests(unittest.TestCase):
    def test_reinit_owns_recovery_before_background_worker_starts(self):
        device = _RecoveringDevice()
        deck = StreamDockDeck(device)

        try:
            self.assertTrue(deck.reinit_panel())
            self.assertTrue(deck.reinitializing())
            self.assertTrue(device.entered.wait(timeout=1))
            self.assertFalse(deck.reinit_panel())
            self.assertEqual(device.calls, 1)
        finally:
            device.release.set()

        deadline = time.monotonic() + 1
        while deck.reinitializing() and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertFalse(deck.reinitializing())
        self.assertEqual(device.calls, 1)

    def test_better_deck_forwards_recovery_state(self):
        deck = object.__new__(BetterDeck)
        deck.deck = _Deck("/dev/hidraw11", recovering=True)

        self.assertTrue(deck.reinitializing())

        deck.deck = _Deck("/dev/hidraw11", recovering=False)
        self.assertFalse(deck.reinitializing())

    def test_connect_preserves_disconnected_controller_during_reinit(self):
        stale_deck = _Deck("/dev/hidraw11", connected=False, recovering=True)
        replacement_deck = _Deck("/dev/hidraw11")
        stale_controller = _Controller(stale_deck)

        manager = object.__new__(deck_manager_module.DeckManager)
        manager.deck_controller = [stale_controller]

        removed = []
        added = []

        def remove_controller(controller):
            removed.append(controller)
            manager.deck_controller.remove(controller)

        manager.remove_controller = remove_controller
        manager.add_newly_connected_deck = added.append

        original_device_manager = deck_manager_module.DeviceManager
        original_enumerate_stream_docks = deck_manager_module.enumerate_stream_dock_decks
        original_recursive_hasattr = deck_manager_module.recursive_hasattr
        try:
            deck_manager_module.DeviceManager = _DeviceManager
            deck_manager_module.enumerate_stream_dock_decks = lambda: [replacement_deck]
            deck_manager_module.recursive_hasattr = lambda *_args: False

            manager.connect_new_decks()
        finally:
            deck_manager_module.DeviceManager = original_device_manager
            deck_manager_module.enumerate_stream_dock_decks = original_enumerate_stream_docks
            deck_manager_module.recursive_hasattr = original_recursive_hasattr

        self.assertEqual(removed, [])
        self.assertFalse(stale_deck.closed)
        self.assertEqual(added, [])
        self.assertEqual(manager.deck_controller, [stale_controller])

    def test_connect_replaces_disconnected_controller_when_hid_path_is_reused(self):
        stale_deck = _Deck("/dev/hidraw11", connected=False)
        replacement_deck = _Deck("/dev/hidraw11")
        stale_controller = _Controller(stale_deck)

        manager = object.__new__(deck_manager_module.DeckManager)
        manager.deck_controller = [stale_controller]

        removed = []
        added = []

        def remove_controller(controller):
            removed.append(controller)
            manager.deck_controller.remove(controller)

        manager.remove_controller = remove_controller
        manager.add_newly_connected_deck = added.append

        original_device_manager = deck_manager_module.DeviceManager
        original_enumerate_stream_docks = deck_manager_module.enumerate_stream_dock_decks
        original_recursive_hasattr = deck_manager_module.recursive_hasattr
        try:
            deck_manager_module.DeviceManager = _DeviceManager
            deck_manager_module.enumerate_stream_dock_decks = lambda: [replacement_deck]
            deck_manager_module.recursive_hasattr = lambda *_args: False

            manager.connect_new_decks()
        finally:
            deck_manager_module.DeviceManager = original_device_manager
            deck_manager_module.enumerate_stream_dock_decks = original_enumerate_stream_docks
            deck_manager_module.recursive_hasattr = original_recursive_hasattr

        self.assertEqual(removed, [stale_controller])
        self.assertTrue(stale_deck.closed)
        self.assertEqual(added, [replacement_deck])


if __name__ == "__main__":
    unittest.main()
