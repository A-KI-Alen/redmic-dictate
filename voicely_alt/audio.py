from __future__ import annotations

import math
import sys
import wave
from array import array
from pathlib import Path


def pcm16_rms(data: bytes) -> float:
    if len(data) < 2:
        return 0.0

    usable = data[: len(data) - (len(data) % 2)]
    samples = array("h")
    samples.frombytes(usable)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0

    total = sum(sample * sample for sample in samples)
    return math.sqrt(total / len(samples))


def wav_rms(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getsampwidth() != 2:
            return 0.0
        return pcm16_rms(wav_file.readframes(wav_file.getnframes()))


def is_silent_wav(path: Path, threshold: int) -> bool:
    return wav_rms(path) < threshold

