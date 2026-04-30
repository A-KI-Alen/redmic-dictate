from __future__ import annotations

import unittest

from voicely_alt.config import AppConfig
from voicely_alt.text_safety import strip_prompt_leak


class TextSafetyTests(unittest.TestCase):
    def test_strip_prompt_leak_removes_repeated_localized_prompt_prefix(self) -> None:
        prompt = AppConfig().transcription_prompt
        leaked = (
            "Dies ist ein deutsches Diktat. Transkribiere ausschlie\u00dflich\n"
            "  auf Deutsch. Schreibe keine englischen W\u00f6rter, au\u00dfer sie wurden klar gesprochen.\n"
            "  Fachbegriffe: RedMic Dictate, Windows, Alt, Shift, Zwischenablage, Transkription,\n"
            "  Mikrofon, Codex, OpenAI. Dies ist ein deutsches Diktat. Transkribiere ausschlie\u00dflich\n"
            "  auf Deutsch. Schreibe keine englischen W\u00f6rter, au\u00dfer sie wurden klar gesprochen.\n"
            "  Fachbegriffe: RedMic Dictate, Windows, Alt, Shift, Zwischenablage, Transkription,\n"
            "  Mikrofon, Codex, OpenAI. Das ist mein eigentlicher Text."
        )

        cleaned = strip_prompt_leak(leaked, prompt)

        self.assertEqual(cleaned, "Das ist mein eigentlicher Text.")

    def test_strip_prompt_leak_keeps_normal_text(self) -> None:
        text = "Das ist ein normaler diktierter Satz."

        self.assertEqual(strip_prompt_leak(text, AppConfig().transcription_prompt), text)

    def test_strip_prompt_leak_removes_prompt_suffix(self) -> None:
        prompt = AppConfig().transcription_prompt
        text = f"Das ist der echte Text. {prompt}"

        self.assertEqual(strip_prompt_leak(text, prompt), "Das ist der echte Text")


if __name__ == "__main__":
    unittest.main()
