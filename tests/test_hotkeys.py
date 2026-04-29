from __future__ import annotations

import unittest

from voicely_alt.hotkeys import _normalize_hotkey


class HotkeyTests(unittest.TestCase):
    def test_normalize_windows_hotkey_alias(self) -> None:
        self.assertEqual(_normalize_hotkey("win+alt+space"), "windows+alt+space")

    def test_plain_space_stays_plain(self) -> None:
        self.assertEqual(_normalize_hotkey("space"), "space")


if __name__ == "__main__":
    unittest.main()
