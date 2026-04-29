from __future__ import annotations

import unittest

from voicely_alt.config import AppConfig
from voicely_alt.llm import OllamaTranscriptCleaner, _clean_model_output
from voicely_alt.state import OutputMode


class LlmCleanupTests(unittest.TestCase):
    def test_cleanup_defaults_to_clipboard_only(self) -> None:
        cleaner = OllamaTranscriptCleaner(AppConfig())

        self.assertTrue(cleaner.will_process(OutputMode.CLIPBOARD, live_chunk=False))
        self.assertFalse(cleaner.will_process(OutputMode.LIVE_PASTE, live_chunk=True))
        self.assertFalse(cleaner.will_process(OutputMode.LIVE_PASTE, live_chunk=False))

    def test_clean_model_output_removes_common_wrappers(self) -> None:
        raw = 'Korrigierter Text:\n"Jetzt sollte eine rote Leiste erscheinen."'

        self.assertEqual(
            _clean_model_output(raw),
            "Jetzt sollte eine rote Leiste erscheinen.",
        )


if __name__ == "__main__":
    unittest.main()
