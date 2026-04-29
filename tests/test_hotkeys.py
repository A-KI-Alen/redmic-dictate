from __future__ import annotations

import time
import unittest

from voicely_alt.config import AppConfig
from voicely_alt.hotkeys import KeyboardHotkeyManager, _is_key_down_event, _normalize_hotkey


class FakeKeyboard:
    def __init__(self):
        self.hooks = []
        self.hotkeys = []
        self.removed = []
        self.pressed = set()

    def hook_key(self, key, callback, suppress=False):
        handle = object()
        self.hooks.append((handle, key, callback, suppress))
        return handle

    def add_hotkey(self, hotkey, callback, suppress=False, trigger_on_release=False):
        handle = object()
        self.hotkeys.append((handle, hotkey, callback, suppress, trigger_on_release))
        return handle

    def remove_hotkey(self, handle):
        self.removed.append(handle)

    def is_pressed(self, key):
        return key in self.pressed


class FakeKeyEvent:
    def __init__(self, event_type: str):
        self.event_type = event_type


class HotkeyTests(unittest.TestCase):
    def test_normalize_windows_hotkey_alias(self) -> None:
        self.assertEqual(_normalize_hotkey("win+alt+space"), "windows+alt+space")

    def test_plain_space_stays_plain(self) -> None:
        self.assertEqual(_normalize_hotkey("space"), "space")

    def test_key_down_detection(self) -> None:
        self.assertTrue(_is_key_down_event(FakeKeyEvent("down")))
        self.assertFalse(_is_key_down_event(FakeKeyEvent("up")))

    def test_recording_controls_use_suppressed_key_hooks(self) -> None:
        keyboard = FakeKeyboard()
        manager = KeyboardHotkeyManager(AppConfig(hard_abort_window_ms=0))
        manager._keyboard = keyboard

        stop_calls = []
        cancel_calls = []
        manager._on_stop = lambda: stop_calls.append("stop")
        manager._on_cancel = lambda: cancel_calls.append("cancel")

        manager.enable_recording_controls()

        self.assertEqual(len(keyboard.hooks), 2)
        self.assertEqual(keyboard.hooks[0][1:], ("space", keyboard.hooks[0][2], True))
        self.assertEqual(keyboard.hooks[1][1:], ("esc", keyboard.hooks[1][2], True))

        stop_callback = keyboard.hooks[0][2]
        cancel_callback = keyboard.hooks[1][2]
        self.assertFalse(stop_callback(FakeKeyEvent("up")))
        self.assertFalse(stop_callback(FakeKeyEvent("down")))
        self.assertFalse(cancel_callback(FakeKeyEvent("down")))

        deadline = time.monotonic() + 1.0
        while not stop_calls and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(stop_calls, ["stop"])
        self.assertEqual(cancel_calls, ["cancel"])

    def test_recording_controls_are_removed(self) -> None:
        keyboard = FakeKeyboard()
        manager = KeyboardHotkeyManager(AppConfig())
        manager._keyboard = keyboard
        manager._on_stop = lambda: None
        manager._on_cancel = lambda: None

        manager.enable_recording_controls()
        manager.disable_recording_controls(force=True)

        self.assertEqual(keyboard.removed, [keyboard.hooks[0][0], keyboard.hooks[1][0]])


if __name__ == "__main__":
    unittest.main()
