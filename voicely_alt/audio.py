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


def resample_pcm16_mono(data: bytes, source_rate: int, target_rate: int) -> bytes:
    if not data or source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return data

    usable = data[: len(data) - (len(data) % 2)]
    samples = array("h")
    samples.frombytes(usable)
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) < 2:
        return usable

    target_length = max(1, int(round(len(samples) * target_rate / source_rate)))
    if target_length == len(samples):
        return usable

    output = array("h")
    ratio = source_rate / target_rate
    last_index = len(samples) - 1
    for target_index in range(target_length):
        position = target_index * ratio
        left = int(position)
        if left >= last_index:
            value = samples[last_index]
        else:
            fraction = position - left
            value = int(round(samples[left] * (1.0 - fraction) + samples[left + 1] * fraction))
        output.append(max(-32768, min(32767, value)))

    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def wav_rms(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getsampwidth() != 2:
            return 0.0
        return pcm16_rms(wav_file.readframes(wav_file.getnframes()))


def is_silent_wav(path: Path, threshold: int) -> bool:
    return wav_rms(path) < threshold
