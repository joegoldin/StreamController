"""Regression tests for StreamDock hotplug reconciliation."""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import globals  # DeckManager expects application globals to be initialized first.
import src.backend.DeckManagement.DeckManager as deck_manager_module


class _Deck:
    def __init__(self, deck_id, connected=True):
        self._deck_id = deck_id
        self._connected = connected
        self.closed = False

    def id(self):
        return self._deck_id

    def connected(self):
        return self._connected

    def close(self):
        self.closed = True


class _Controller:
    def __init__(self, deck):
        self.deck = deck


class _DeviceManager:
    def enumerate(self):
        return []


class StreamDockReconnectTests(unittest.TestCase):
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
