"""Keep StreamDock recovery extensions on the library's rotation wrapper."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from StreamDeck.Devices.RotatedDeck import RotatedDeck
from src.backend.DeckManagement.BetterDeck import BetterDeck


class RotatedStreamDockTests(unittest.TestCase):
    def test_uses_library_rotation_implementation(self):
        self.assertTrue(issubclass(BetterDeck, RotatedDeck))

    def test_screenless_keys_follow_logical_rotation(self):
        raw = SimpleNamespace(key_layout=lambda: (3, 3),
                              screenless_key_indices=lambda: {6, 7, 8})
        deck = BetterDeck(raw, 90)
        self.assertEqual(deck.screenless_key_indices(),
                         {deck.get_logical_index(i) for i in (6, 7, 8)})

    def test_display_recovery_is_forwarded_without_transport_reset(self):
        raw = SimpleNamespace(reinit_display=Mock(return_value=True),
                              reinit_panel=Mock())
        deck = BetterDeck(raw)
        self.assertTrue(deck.reinit_display())
        raw.reinit_display.assert_called_once_with()
        raw.reinit_panel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
