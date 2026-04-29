from __future__ import annotations

import time
import unittest

from voicely_alt.config import AppConfig
from voicely_alt.hotkeys import KeyboardHotkeyManager, _normalize_hotkey


class FakeKeyboard:
    def __init__(self):
        self.hooks = []
        self.hotkeys = []
        self.removed = []
        self.blocked = []
        self.pressed = set()

    def hook_key(self, key, callback, suppress=False):
        handle = object()
        self.hooks.append((handle, key, callback, suppress))
        return handle

    def block_key(self, key):
        handle = ("block", key)
        self.blocked.append(key)

        def remove():
            self.removed.append(handle)

        return remove

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

    def test_alt_y_start_hook_suppresses_y(self) -> None:
        keyboard = FakeKeyboard()
        manager = KeyboardHotkeyManager(AppConfig())
        manager._keyboard = keyboard
        live_calls = []
        clipboard_calls = []

        handles = manager._register_start_hotkeys(
            lambda: live_calls.append("live"),
            lambda: clipboard_calls.append("clipboard"),
        )

        self.assertEqual(handles, [keyboard.hooks[0][0]])
        self.assertEqual(keyboard.hooks[0][1:], ("y", keyboard.hooks[0][2], True))

        callback = keyboard.hooks[0][2]
        self.assertTrue(callback(FakeKeyEvent("down")))

        keyboard.pressed.add("alt")
        self.assertFalse(callback(FakeKeyEvent("down")))
        self.assertFalse(callback(FakeKeyEvent("up")))
        self.assertEqual(live_calls, ["live"])

        keyboard.pressed.add("shift")
        self.assertFalse(callback(FakeKeyEvent("down")))
        self.assertFalse(callback(FakeKeyEvent("up")))
        self.assertEqual(clipboard_calls, ["clipboard"])

    def test_recording_controls_block_keys_and_poll_for_stop(self) -> None:
        keyboard = FakeKeyboard()
        manager = KeyboardHotkeyManager(AppConfig(hard_abort_window_ms=0))
        manager._keyboard = keyboard

        stop_calls = []
        cancel_calls = []
        manager._on_stop = lambda: stop_calls.append("stop")
        manager._on_cancel = lambda: cancel_calls.append("cancel")

        manager.enable_recording_controls()

        self.assertEqual(keyboard.blocked, ["space", "esc"])

        keyboard.pressed.add("space")
        deadline = time.monotonic() + 1.0
        while not stop_calls and time.monotonic() < deadline:
            time.sleep(0.01)
        keyboard.pressed.clear()

        keyboard.pressed.add("esc")
        deadline = time.monotonic() + 1.0
        while not cancel_calls and time.monotonic() < deadline:
            time.sleep(0.01)
        keyboard.pressed.clear()

        manager.disable_recording_controls(force=True)

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

        self.assertEqual(keyboard.removed, [("block", "space"), ("block", "esc")])


if __name__ == "__main__":
    unittest.main()
