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
        self.assertEqual(config.backend, "openai_realtime")
        self.assertEqual(config.language, "de")
        self.assertIn("deutsches Diktat", config.transcription_prompt)
        self.assertTrue(config.whisper_no_fallback)
        self.assertTrue(config.whisper_suppress_non_speech)
        self.assertEqual(config.whisper_server_max_age_seconds, 14400)
        self.assertTrue(config.keep_transcript_clipboard)
        self.assertEqual(config.cloud_fallback, "local_whispercpp")
        self.assertEqual(config.openai_api_key_env, "OPENAI_API_KEY")
        self.assertEqual(config.openai_realtime_session_model, "gpt-realtime")
        self.assertEqual(config.openai_realtime_transcription_model, "gpt-4o-mini-transcribe")
        self.assertEqual(config.openai_realtime_fallback_model, "gpt-4o-transcribe")
        self.assertEqual(config.openai_realtime_audio_rate, 24000)
        self.assertEqual(config.openai_realtime_commit_seconds, 3.0)
        self.assertEqual(config.openai_realtime_finish_timeout_seconds, 7.0)
        self.assertEqual(config.port, 18080)
        self.assertTrue(config.recording_overlay)
        self.assertEqual(config.overlay_size, 72)
        self.assertTrue(config.taskbar_recording_overlay)
        self.assertEqual(config.taskbar_overlay_height, 22)
        self.assertFalse(config.live_streaming)
        self.assertTrue(config.progressive_live_paste)
        self.assertTrue(config.background_chunking)
        self.assertEqual(config.background_chunk_seconds, 5)
        self.assertTrue(config.quality_chunking)
        self.assertEqual(config.quality_model, "small")
        self.assertEqual(config.quality_threads, "6")
        self.assertEqual(config.quality_chunk_seconds, 10)
        self.assertEqual(config.quality_max_fast_backlog, 1)
        self.assertEqual(config.quality_wait_after_stop_seconds, 7.0)
        self.assertTrue(config.quality_guard_enabled)
        self.assertEqual(config.quality_guard_min_recording_seconds, 20)
        self.assertEqual(config.quality_guard_min_coverage, 0.50)
        self.assertEqual(config.quality_guard_min_text_ratio, 0.40)
        self.assertEqual(config.transcript_cleanup, "clipboard")
        self.assertEqual(config.cleanup_backend, "ollama")
        self.assertEqual(config.cleanup_model, "llama3.2:3b")
        self.assertTrue(config.tracking_enabled)
        self.assertEqual(config.tracking_retention_days, 14)
        self.assertFalse(config.tracking_include_transcript_text)
        self.assertEqual(config.tracking_transcript_preview_chars, 0)

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

    def test_load_or_create_persists_new_default_keys(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('selected_model = "base"\n', encoding="utf-8")

            loaded = AppConfig.load_or_create(path)
            written = path.read_text(encoding="utf-8")

        self.assertEqual(loaded.selected_model, "base")
        self.assertIn("tracking_enabled = true", written)
        self.assertIn('selected_model = "base"', written)

    def test_config_load_accepts_utf8_bom(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('\ufeffselected_model = "base"\n', encoding="utf-8")

            loaded = AppConfig.load(path)

        self.assertEqual(loaded.selected_model, "base")


if __name__ == "__main__":
    unittest.main()
