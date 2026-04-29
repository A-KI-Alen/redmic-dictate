from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .benchmark import benchmark_models, record_sample
from .config import AppConfig
from .paths import benchmark_sample_path
from .state import DictationState, OutputMode


LOG = logging.getLogger(__name__)


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> Path: ...
    def stop_if_audio(self) -> Path | None: ...
    def pop_chunk(self) -> Path | None: ...
    def cancel(self) -> None: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class OutputTarget(Protocol):
    def paste_text(self, text: str) -> None: ...
    def copy_text(self, text: str) -> None: ...


class RecordingControls(Protocol):
    def enable_recording_controls(self) -> None: ...
    def disable_recording_controls(self) -> None: ...


StatusCallback = Callable[[DictationState, str], None]


class DictationController:
    def __init__(
        self,
        config: AppConfig,
        recorder: Recorder,
        transcriber: Transcriber,
        paste_target: OutputTarget,
        controls: RecordingControls | None = None,
        status_callback: StatusCallback | None = None,
        background: bool = True,
    ):
        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.paste_target = paste_target
        self.controls = controls
        self.status_callback = status_callback
        self.background = background
        self.state = DictationState.IDLE
        self.output_mode = OutputMode.LIVE_PASTE
        self.last_error = ""
        self._lock = threading.RLock()
        self._live_stop_event: threading.Event | None = None
        self._live_thread: threading.Thread | None = None

    def start_recording(self, mode: OutputMode = OutputMode.LIVE_PASTE) -> bool:
        with self._lock:
            if self.state not in {DictationState.IDLE, DictationState.ERROR}:
                return False
            try:
                self.output_mode = mode
                self.recorder.start()
                if self.controls is not None:
                    self.controls.enable_recording_controls()
                if mode == OutputMode.LIVE_PASTE:
                    self._start_live_worker()
                    self._set_state(DictationState.RECORDING, "Live-Diktat laeuft")
                else:
                    self._set_state(DictationState.RECORDING, "Clipboard-Aufnahme laeuft")
                return True
            except Exception as exc:
                self._set_error(exc)
                return False

    def start_live_recording(self) -> bool:
        return self.start_recording(OutputMode.LIVE_PASTE)

    def start_clipboard_recording(self) -> bool:
        return self.start_recording(OutputMode.CLIPBOARD)

    def stop_recording(self) -> bool:
        with self._lock:
            if self.state != DictationState.RECORDING:
                return False
            mode = self.output_mode
            try:
                if self.controls is not None:
                    self.controls.disable_recording_controls()

                if mode == OutputMode.LIVE_PASTE:
                    final_audio = self.recorder.stop_if_audio()
                    self._set_state(DictationState.TRANSCRIBING, "Live-Diktat wird abgeschlossen")
                    target = self._finish_live_recording
                    args = (final_audio,)
                else:
                    final_audio = self.recorder.stop()
                    self._set_state(DictationState.TRANSCRIBING, "Transkription fuer Zwischenablage laeuft")
                    target = self._transcribe_final
                    args = (final_audio, OutputMode.CLIPBOARD)
            except Exception as exc:
                self._set_error(exc)
                return False

        if self.background:
            threading.Thread(target=target, args=args, daemon=True).start()
        else:
            target(*args)
        return True

    def cancel_recording(self) -> bool:
        with self._lock:
            if self.state != DictationState.RECORDING:
                return False
            try:
                self._stop_live_worker(wait=False)
                if self.controls is not None:
                    self.controls.disable_recording_controls()
                self.recorder.cancel()
                self._set_state(DictationState.IDLE, "Aufnahme abgebrochen")
                return True
            except Exception as exc:
                self._set_error(exc)
                return False

    def benchmark(self, record_seconds: int = 8) -> bool:
        with self._lock:
            if self.state not in {DictationState.IDLE, DictationState.ERROR}:
                return False
            self._set_state(DictationState.BENCHMARKING, "Benchmark-Aufnahme laeuft")

        def run() -> None:
            try:
                sample = record_sample(self.config, record_seconds, benchmark_sample_path())
                self._set_state(DictationState.BENCHMARKING, "Modelle werden gemessen")
                selected, _ = benchmark_models(self.config, sample)
                self._set_state(DictationState.IDLE, f"Lokales Modell ausgewaehlt: {selected}")
            except Exception as exc:
                self._set_error(exc)

        if self.background:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()
        return True

    def shutdown(self) -> None:
        with self._lock:
            self._stop_live_worker(wait=False)
            if self.controls is not None:
                self.controls.disable_recording_controls()
            if self.state == DictationState.RECORDING:
                try:
                    self.recorder.cancel()
                except Exception:
                    LOG.debug("Failed to cancel active recording during shutdown", exc_info=True)
            close = getattr(self.transcriber, "close", None)
            if callable(close):
                close()
            self._set_state(DictationState.IDLE, "Beendet")

    def _start_live_worker(self) -> None:
        self._stop_live_worker(wait=True)
        self._live_stop_event = threading.Event()
        self._live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self._live_thread.start()

    def _stop_live_worker(self, wait: bool) -> None:
        event = self._live_stop_event
        thread = self._live_thread
        if event is not None:
            event.set()
        if wait and thread is not None and thread.is_alive():
            thread.join(timeout=max(2, self.config.live_chunk_seconds + 2))
        self._live_stop_event = None
        self._live_thread = None

    def _live_loop(self) -> None:
        event = self._live_stop_event
        if event is None:
            return

        while not event.wait(max(1, self.config.live_chunk_seconds)):
            try:
                chunk = self.recorder.pop_chunk()
                if chunk is not None:
                    self._transcribe_and_output(chunk, OutputMode.LIVE_PASTE, live_chunk=True)
            except Exception as exc:
                self._set_error(exc)
                event.set()
                try:
                    self.recorder.cancel()
                except Exception:
                    LOG.debug("Failed to cancel recording after live error", exc_info=True)
                return

    def _finish_live_recording(self, final_audio: Path | None) -> None:
        try:
            self._stop_live_worker(wait=True)
            if final_audio is not None:
                self._transcribe_and_output(final_audio, OutputMode.LIVE_PASTE, live_chunk=False)
            self._set_state(DictationState.IDLE, "Live-Diktat beendet")
        except Exception as exc:
            self._set_error(exc)

    def _transcribe_final(self, audio_path: Path, mode: OutputMode) -> None:
        try:
            self._transcribe_and_output(audio_path, mode, live_chunk=False)
            if mode == OutputMode.CLIPBOARD:
                self._set_state(DictationState.IDLE, "Text in Zwischenablage")
            else:
                self._set_state(DictationState.IDLE, "Text eingefuegt")
        except Exception as exc:
            self._set_error(exc)

    def _transcribe_and_output(self, audio_path: Path, mode: OutputMode, live_chunk: bool) -> None:
        try:
            transcript = self.transcriber.transcribe(audio_path).strip()
            if not transcript:
                if not live_chunk:
                    self._set_state(DictationState.IDLE, "Kein Text erkannt")
                return

            if mode == OutputMode.CLIPBOARD:
                self._set_state(DictationState.PASTING, "Text wird in Zwischenablage gelegt")
                self.paste_target.copy_text(transcript)
            else:
                text = _format_live_text(transcript) if live_chunk else transcript
                self.paste_target.paste_text(text)
        finally:
            try:
                audio_path.unlink(missing_ok=True)
            except Exception:
                LOG.debug("Failed to remove temporary audio file: %s", audio_path, exc_info=True)

    def _set_state(self, state: DictationState, message: str) -> None:
        with self._lock:
            self.state = state
            if state != DictationState.ERROR:
                self.last_error = ""
        LOG.info("%s: %s", state.value, message)
        if self.status_callback is not None:
            self.status_callback(state, message)

    def _set_error(self, exc: Exception) -> None:
        self.last_error = str(exc)
        LOG.exception("Dictation error")
        self._set_state(DictationState.ERROR, str(exc))


def _format_live_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.endswith((" ", "\n")):
        return stripped
    return stripped + " "
