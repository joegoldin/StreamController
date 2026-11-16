from StreamDeck.Devices.RotatedDeck import RotatedDeck


class BetterDeck(RotatedDeck):
    """Library rotation with optional StreamDock recovery and hardware hints."""

    def large_dial_indices(self):
        """Dial indices that are physically larger than the rest (UI sizing hint).

        Forwarded from the wrapped deck when it provides the hint (e.g. the
        StreamDock N3's top knob); decks without it report no large dials.
        """
        getter = getattr(self.deck, "large_dial_indices", None)
        if callable(getter):
            try:
                return {self.get_logical_dial_index(i) for i in getter()}
            except Exception:
                return set()
        return set()

    def screenless_key_indices(self):
        """Grid indices of keys with no display (input-only physical buttons).

        Forwarded from the wrapped deck when provided (e.g. the StreamDock N3's
        bottom-row buttons); decks without the hint report no screenless keys.
        """
        getter = getattr(self.deck, "screenless_key_indices", None)
        if callable(getter):
            try:
                return {self.get_logical_index(i) for i in getter()}
            except Exception:
                return set()
        return set()

    def reinit_panel(self):
        """Attempt transport recovery and repaint for decks that can wedge
        after suspend or disconnect. Forwarded to the wrapped deck when it
        supports it; a no-op for decks (Elgato/Fake) that don't need it."""
        fn = getattr(self.deck, "reinit_panel", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def reinit_display(self) -> bool:
        """Reinitialize display output without resetting a working input transport."""
        fn = getattr(self.deck, "reinit_display", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                pass
        return False

    def reinitializing(self) -> bool:
        """Whether the wrapped deck is currently recovering in place."""
        fn = getattr(self.deck, "reinitializing", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                pass
        return False

    def recovery_identity(self):
        """Stable identity of a wrapped deck while its transport path changes."""
        fn = getattr(self.deck, "recovery_identity", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
        return None

    def sleep_panel(self):
        """Forward an explicit hardware display-off request when supported.

        Automatic lock handling intentionally uses the ordinary screensaver:
        HAN can leave StreamDock N3 firmware ignoring display writes.
        """
        fn = getattr(self.deck, "sleep_panel", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
