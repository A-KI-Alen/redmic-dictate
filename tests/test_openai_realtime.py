from __future__ import annotations

import unittest

from voicely_alt.config import AppConfig
from voicely_alt.openai_realtime import (
    OpenAIRealtimeTranscriptionSession,
    _realtime_url,
    _session_update_payload,
)


class FakeAudioSource:
    def read_stream_chunk(self) -> bytes:
        return b""

    def actual_sample_rate(self) -> int:
        return 16000


class OpenAIRealtimeTests(unittest.TestCase):
    def test_session_update_uses_transcription_mini_model(self) -> None:
        payload = _session_update_payload(AppConfig())

        session = payload["session"]
        self.assertEqual(session["type"], "transcription")
        audio_input = session["audio"]["input"]
        self.assertEqual(audio_input["format"]["rate"], 24000)
        self.assertEqual(audio_input["transcription"]["model"], "gpt-4o-mini-transcribe")
        self.assertEqual(audio_input["transcription"]["language"], "de")
        self.assertNotIn("prompt", audio_input["transcription"])
        self.assertIsNone(audio_input["turn_detection"])

    def test_session_update_can_use_explicit_realtime_prompt(self) -> None:
        payload = _session_update_payload(AppConfig(openai_realtime_prompt="Nur Deutsch."))

        transcription = payload["session"]["audio"]["input"]["transcription"]
        self.assertEqual(transcription["prompt"], "Nur Deutsch.")

    def test_realtime_url_requests_transcription_intent(self) -> None:
        url = _realtime_url(
            AppConfig(
                openai_realtime_url="wss://api.openai.com/v1/realtime?foo=bar&model=gpt-realtime",
                openai_realtime_session_model="gpt-realtime",
            )
        )

        self.assertIn("foo=bar", url)
        self.assertIn("intent=transcription", url)
        self.assertNotIn("model=", url)

    def test_completed_items_are_delivered_in_commit_order(self) -> None:
        delivered = []
        realtime = OpenAIRealtimeTranscriptionSession(
            AppConfig(),
            FakeAudioSource(),
            on_text=delivered.append,
        )

        realtime._handle_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "b",
                "transcript": "zweiter Teil",
            }
        )
        realtime._handle_event(
            {
                "type": "input_audio_buffer.committed",
                "item_id": "a",
            }
        )
        realtime._handle_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "a",
                "transcript": "erster Teil",
            }
        )
        realtime._handle_event(
            {
                "type": "input_audio_buffer.committed",
                "item_id": "b",
            }
        )

        result = realtime.result()
        self.assertEqual(delivered, ["erster Teil", "zweiter Teil"])
        self.assertEqual(result.transcript, "erster Teil zweiter Teil")
        self.assertTrue(result.delivered_any)

    def test_progress_counts_empty_and_completed_segments(self) -> None:
        progress = []
        realtime = OpenAIRealtimeTranscriptionSession(
            AppConfig(),
            FakeAudioSource(),
            on_progress=lambda done, total: progress.append((done, total)),
        )
        realtime._commits_sent = 2

        realtime._handle_event({"type": "input_audio_buffer.committed", "item_id": "a"})
        realtime._handle_event({"type": "input_audio_buffer.committed", "item_id": "b"})
        realtime._handle_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "a",
                "transcript": "",
            }
        )
        realtime._handle_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "b",
                "transcript": "Text",
            }
        )

        self.assertEqual(progress[-1], (2, 2))


if __name__ == "__main__":
    unittest.main()
