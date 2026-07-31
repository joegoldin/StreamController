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
from src.backend.DeckManagement.DeckController import MediaPlayerThread
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


class _PostTaskMediaPlayer:
    def __init__(self, deck):
        self.deck = deck
        self.post_tasks = []

    def add_post_task(self, method, *args, **kwargs):
        self.deck.events.append("queue_post_task")
        self.post_tasks.append((method, args, kwargs))

    def run_post_tasks(self):
        for method, args, kwargs in self.post_tasks:
            method(*args, **kwargs)


class _DeckController:
    def __init__(self, deck):
        self.deck = deck
        self.active_page = object()
        self.loaded_pages = []
        self.default_page_calls = 0
        self.media_player = _PostTaskMediaPlayer(deck)

    def clear(self):
        pass

    def load_page(self, page, allow_reload=False):
        self.loaded_pages.append((page, allow_reload))
        self.deck.events.append("load_page")

    def load_default_page(self):
        self.default_page_calls += 1
        self.deck.events.append("load_default_page")


class _CachingDeck:
    def __init__(self):
        self.images = {}
        self.display_snapshots = []

    def is_touch(self):
        return False

    def set_key_image(self, key, image):
        self.images[key] = bytes(image)

    def reinit_display(self):
        self.display_snapshots.append(dict(self.images))
        return True


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
        self.assertEqual(deck.events, ["load_page", "queue_post_task", "reinit_display"])
        self.assertEqual(deck.display_reinit_calls, 1)
        self.assertEqual(deck.reinit_calls, 0)

    def test_screensaver_hide_queues_display_reinit_after_default_page_restore(self):
        deck = _Deck()
        controller = _DeckController(deck)
        controller.active_page = None
        screen_saver = ScreenSaver(controller)
        screen_saver.showing = True

        try:
            screen_saver.hide()
        finally:
            if screen_saver.timer:
                screen_saver.timer.cancel()

        self.assertEqual(controller.default_page_calls, 1)
        self.assertEqual(deck.events, ["load_default_page", "queue_post_task"])
        controller.media_player.run_post_tasks()
        self.assertEqual(deck.display_reinit_calls, 1)
        self.assertEqual(deck.reinit_calls, 0)

    def test_post_task_observes_images_queued_by_page_update(self):
        deck = _CachingDeck()
        page = object()
        controller = SimpleNamespace(
            deck=deck,
            active_page=page,
            serial_number=lambda: "TEST",
        )
        settings_manager = SimpleNamespace(get_app_settings=lambda: {})

        with patch.object(gl, "settings_manager", settings_manager):
            media_player = MediaPlayerThread(controller)

        media_player.add_task(
            lambda: media_player.add_image_task(0, b"RESTORED")
        )
        media_player.add_post_task(deck.reinit_display)
        media_player.perform_media_player_tasks()

        self.assertEqual(deck.display_snapshots, [{0: b"RESTORED"}])


if __name__ == "__main__":
    unittest.main()
