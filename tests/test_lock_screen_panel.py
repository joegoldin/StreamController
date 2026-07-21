"""Regression tests for panel behavior during lock-screen transitions."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import globals as gl
from src.backend.DeckManagement.Subclasses.ScreenSaver import ScreenSaver
from src.backend.LockScreenManager.LockScreenManager import LockScreenManager


class _ScreenSaver:
    def __init__(self):
        self.show_calls = 0

    def show(self):
        self.show_calls += 1


class _Deck:
    def __init__(self):
        self.sleep_calls = 0
        self.reinit_calls = 0

    def sleep_panel(self):
        self.sleep_calls += 1

    def reinit_panel(self):
        self.reinit_calls += 1


class _DeckController:
    def __init__(self, deck):
        self.deck = deck
        self.active_page = object()
        self.loaded_pages = []

    def clear(self):
        pass

    def load_page(self, page, allow_reload=False):
        self.loaded_pages.append((page, allow_reload))


class LockScreenPanelTests(unittest.TestCase):
    def test_lock_uses_screensaver_without_sleeping_panel(self):
        screen_saver = _ScreenSaver()
        deck = _Deck()
        controller = SimpleNamespace(
            allow_interaction=True,
            screen_saver=screen_saver,
            deck=deck,
        )
        manager = object.__new__(LockScreenManager)
        manager.locked = False

        settings_manager = SimpleNamespace(get_app_settings=lambda: {})
        deck_manager = SimpleNamespace(deck_controller=[controller])
        with (
            patch.object(gl, "settings_manager", settings_manager),
            patch.object(gl, "deck_manager", deck_manager),
        ):
            manager.lock(True)

        self.assertFalse(controller.allow_interaction)
        self.assertEqual(screen_saver.show_calls, 1)
        self.assertEqual(deck.sleep_calls, 0)

    def test_screensaver_hide_restores_page_without_reinitializing_panel(self):
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
        self.assertEqual(deck.reinit_calls, 0)


if __name__ == "__main__":
    unittest.main()
