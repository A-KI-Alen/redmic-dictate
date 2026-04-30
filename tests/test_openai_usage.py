from __future__ import annotations

import unittest

from voicely_alt.config import AppConfig
from voicely_alt.openai_usage import (
    estimate_transcription_cost_eur,
    parse_audio_transcription_usage,
    transcription_rate_eur_per_minute,
)


class OpenAIUsageTests(unittest.TestCase):
    def test_parse_audio_transcription_usage_sums_matching_model(self) -> None:
        config = AppConfig()
        payload = {
            "data": [
                {
                    "results": [
                        {
                            "model": "gpt-4o-mini-transcribe",
                            "seconds": 12.5,
                            "num_model_requests": 2,
                        },
                        {
                            "model": "gpt-4o-transcribe",
                            "seconds": 30,
                            "num_model_requests": 1,
                        },
                    ]
                },
                {
                    "result": [
                        {
                            "model": "gpt-4o-mini-transcribe",
                            "seconds": 7.5,
                            "num_model_requests": 1,
                        }
                    ]
                },
            ]
        }

        usage = parse_audio_transcription_usage(payload, config, "gpt-4o-mini-transcribe")

        self.assertEqual(usage.seconds, 20.0)
        self.assertEqual(usage.requests, 3)
        self.assertAlmostEqual(usage.cost_eur, 20.0 * 0.0028 / 60.0)
        self.assertEqual(usage.usage_label(), "20.0s, 3 Req.")

    def test_estimate_uses_configured_mini_and_full_rates(self) -> None:
        config = AppConfig(
            openai_realtime_mini_transcribe_eur_per_minute=0.003,
            openai_realtime_transcribe_eur_per_minute=0.006,
        )

        self.assertEqual(transcription_rate_eur_per_minute(config, "gpt-4o-mini-transcribe"), 0.003)
        self.assertEqual(transcription_rate_eur_per_minute(config, "gpt-4o-transcribe"), 0.006)
        self.assertAlmostEqual(
            estimate_transcription_cost_eur(config, 30.0, "gpt-4o-mini-transcribe"),
            0.0015,
        )


if __name__ == "__main__":
    unittest.main()
