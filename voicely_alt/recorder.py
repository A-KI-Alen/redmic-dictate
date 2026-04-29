from __future__ import annotations

import tempfile
import threading
import time
import wave
import os
from pathlib import Path

from .audio import pcm16_rms
from .config import AppConfig
from .paths import temp_dir


class RecordingError(RuntimeError):
    pass


class EmptyRecordingError(RecordingError):
    pass


class AudioRecorder:
    def __init__(self, config: AppConfig):
        self.config = config
        self._stream = None
        self._frames = bytearray()
        self._lock = threading.RLock()
        self._started_at = 0.0
        self._actual_sample_rate = config.sample_rate
        self._latest_level = 0.0
        self._latest_level_at = 0.0

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise RecordingError("Recording is already active.")

            self._frames = bytearray()
            self._started_at = time.monotonic()
            self._actual_sample_rate = self.config.sample_rate
            self._latest_level = 0.0
            self._latest_level_at = time.monotonic()

            try:
                self._stream = self._open_stream(self._actual_sample_rate)
            except Exception:
                fallback_rate = self._default_sample_rate()
                self._actual_sample_rate = fallback_rate
                self._stream = self._open_stream(fallback_rate)

            self._stream.start()

    def stop(self) -> Path:
        with self._lock:
            if self._stream is None:
                raise RecordingError("Recording is not active.")
            self._close_stream()

            if not self._frames:
                raise EmptyRecordingError("No audio was captured.")

            if pcm16_rms(bytes(self._frames)) < self.config.silence_rms_threshold:
                raise EmptyRecordingError("Captured audio is too quiet to transcribe.")

            if time.monotonic() - self._started_at > self.config.max_recording_seconds:
                raise RecordingError("Recording exceeded the configured maximum duration.")

            return self._write_wav(bytes(self._frames))

    def pop_chunk(self) -> Path | None:
        with self._lock:
            if self._stream is None or not self._frames:
                return None
            frames = bytes(self._frames)
            self._frames = bytearray()
            if pcm16_rms(frames) < self.config.silence_rms_threshold:
                return None
            return self._write_wav(frames)

    def stop_if_audio(self) -> Path | None:
        with self._lock:
            if self._stream is None:
                return None
            self._close_stream()
            if not self._frames:
                return None
            frames = bytes(self._frames)
            self._frames = bytearray()
            if pcm16_rms(frames) < self.config.silence_rms_threshold:
                return None
            return self._write_wav(frames)

    def cancel(self) -> None:
        with self._lock:
            self._close_stream()
            self._frames = bytearray()
            self._latest_level = 0.0

    def current_level(self) -> float:
        with self._lock:
            level = float(getattr(self, "_latest_level", 0.0))
            updated_at = float(getattr(self, "_latest_level_at", 0.0))
            age = max(0.0, time.monotonic() - updated_at)
            if age > 0.8:
                return 0.0
            return max(0.0, min(1.0, level * max(0.0, 1.0 - age / 0.8)))

    def _open_stream(self, sample_rate: int):
        import sounddevice as sd

        return sd.RawInputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )

    def _default_sample_rate(self) -> int:
        import sounddevice as sd

        device = sd.query_devices(kind="input")
        return int(device.get("default_samplerate") or 48000)

    def _callback(self, indata, frames, time_info, status) -> None:
        del frames, time_info, status
        data = bytes(indata)
        rms = pcm16_rms(data)
        level = min(1.0, (rms / 1800.0) ** 0.75)
        with self._lock:
            self._frames.extend(data)
            previous = float(getattr(self, "_latest_level", 0.0))
            self._latest_level = max(level, previous * 0.72)
            self._latest_level_at = time.monotonic()

    def _close_stream(self) -> None:
        if self._stream is None:
            return
        stream = self._stream
        self._stream = None
        try:
            stream.stop()
        finally:
            stream.close()

    def _write_wav(self, frames: bytes) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix="redmic_dictate_",
            suffix=".wav",
            dir=temp_dir(),
        )
        os.close(descriptor)
        output = Path(name)

        with wave.open(str(output), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._actual_sample_rate)
            wav_file.writeframes(frames)

        return output
