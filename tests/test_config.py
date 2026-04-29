from __future__ import annotations

import unittest
from pathlib import Path

from voicely_alt.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_default_config_matches_hotkey_plan(self) -> None:
        config = AppConfig()

        self.assertEqual(config.start_hotkey, "alt+y")
        self.assertEqual(config.live_hotkey, "alt+y")
        self.assertEqual(config.clipboard_hotkey, "alt+shift+y")
        self.assertEqual(config.stop_hotkey, "space")
        self.assertEqual(config.cancel_hotkey, "esc")
        self.assertEqual(config.hard_abort_hotkey, "space+esc")
        self.assertEqual(config.hard_abort_window_ms, 250)
        self.assertEqual(config.backend, "local_whispercpp")
        self.assertEqual(config.language, "de")
        self.assertTrue(config.keep_transcript_clipboard)
        self.assertEqual(config.cloud_fallback, "manual")
        self.assertEqual(config.port, 18080)
        self.assertTrue(config.recording_overlay)
        self.assertEqual(config.overlay_size, 72)
        self.assertTrue(config.taskbar_recording_overlay)
        self.assertEqual(config.taskbar_overlay_height, 22)
        self.assertFalse(config.live_streaming)
        self.assertTrue(config.background_chunking)
        self.assertEqual(config.background_chunk_seconds, 5)
        self.assertEqual(config.transcript_cleanup, "clipboard")
        self.assertEqual(config.cleanup_backend, "ollama")
        self.assertEqual(config.cleanup_model, "llama3.2:3b")

    def test_config_roundtrip(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            config = AppConfig(selected_model="base", port=9000, transcript_cleanup="off")

            config.save(path)
            loaded = AppConfig.load(path)

        self.assertEqual(loaded.selected_model, "base")
        self.assertEqual(loaded.port, 9000)
        self.assertEqual(loaded.transcript_cleanup, "off")
        self.assertEqual(loaded.resolved_model(), "base")


if __name__ == "__main__":
    unittest.main()
