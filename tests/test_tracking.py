from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from voicely_alt.config import AppConfig
from voicely_alt.tracking import EventTracker, build_diagnostics_report, load_events


class TrackingTests(unittest.TestCase):
    def test_tracker_redacts_transcript_text_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = EventTracker(AppConfig(), root=Path(directory))

            tracker.record(
                "output_written",
                7,
                **tracker.transcript_fields("Das ist ein geheimer Testtext."),
            )

            events = load_events(24, root=Path(directory))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["session_id"], 7)
        self.assertEqual(events[0]["transcript_words"], 5)
        self.assertIn("transcript_sha256_12", events[0])
        self.assertNotIn("transcript_text", events[0])
        self.assertNotIn("transcript_preview", events[0])

    def test_tracker_can_include_transcript_preview_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(tracking_transcript_preview_chars=8)
            tracker = EventTracker(config, root=Path(directory))

            tracker.record("output_written", 1, **tracker.transcript_fields("Hallo Welt"))

            event_file = next(Path(directory).glob("events-*.jsonl"))
            event = json.loads(event_file.read_text(encoding="utf-8").strip())

        self.assertEqual(event["transcript_preview"], "Hallo We")
        self.assertNotIn("transcript_text", event)

    def test_diagnostics_report_summarizes_problem_events(self) -> None:
        report = build_diagnostics_report(
            [
                {"event": "session_started", "session_id": 1, "ts": "2026-04-30T08:00:00+02:00"},
                {
                    "event": "audio_transcribed",
                    "session_id": 1,
                    "duration_ms": 6200,
                    "ts": "2026-04-30T08:00:06+02:00",
                },
                {
                    "event": "session_error",
                    "session_id": 1,
                    "error": "whisper unavailable",
                    "ts": "2026-04-30T08:00:07+02:00",
                },
            ],
            hours=24,
        )

        self.assertIn("sessions: 1", report)
        self.assertIn("errors: 1", report)
        self.assertIn("slow transcriptions >=5s: 1", report)


if __name__ == "__main__":
    unittest.main()
