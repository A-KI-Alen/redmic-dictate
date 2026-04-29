from __future__ import annotations

import unittest

from voicely_alt.audio import pcm16_rms


class AudioTests(unittest.TestCase):
    def test_silent_pcm_has_zero_rms(self) -> None:
        self.assertEqual(pcm16_rms(b"\x00\x00" * 1000), 0.0)

    def test_non_silent_pcm_has_positive_rms(self) -> None:
        self.assertGreater(pcm16_rms((1000).to_bytes(2, "little", signed=True) * 1000), 0.0)


if __name__ == "__main__":
    unittest.main()
