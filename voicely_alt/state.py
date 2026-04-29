from __future__ import annotations

from enum import Enum


class DictationState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    PASTING = "pasting"
    BENCHMARKING = "benchmarking"
    ERROR = "error"


class OutputMode(str, Enum):
    LIVE_PASTE = "live_paste"
    CLIPBOARD = "clipboard"
