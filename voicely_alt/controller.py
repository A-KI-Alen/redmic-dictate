from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .benchmark import benchmark_models, record_sample
from .chunking import ChunkPipeline, unlink_audio
from .config import AppConfig
from .focus import FocusTarget, capture_focus_target
from .paths import benchmark_sample_path
from .state import DictationState, OutputMode


LOG = logging.getLogger(__name__)


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> Path: ...
    def stop_if_audio(self) -> Path | None: ...
    def pop_chunk(self) -> Path | None: ...
    def current_level(self) -> float: ...
    def cancel(self) -> None: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class TextProcessor(Protocol):
    def will_process(self, mode: OutputMode, live_chunk: bool) -> bool: ...
    def process(self, text: str, mode: OutputMode, live_chunk: bool) -> str: ...
    def close(self) -> None: ...


class OutputTarget(Protocol):
    def paste_text(self, text: str) -> None: ...
    def copy_text(self, text: str) -> None: ...


class RecordingControls(Protocol):
    def enable_recording_controls(self) -> None: ...
    def disable_recording_controls(self, force: bool = False) -> None: ...


StatusCallback = Callable[[DictationState, str], None]
LevelCallback = Callable[[float], None]


class DictationController:
    def __init__(
        self,
        config: AppConfig,
        recorder: Recorder,
        transcriber: Transcriber,
        paste_target: OutputTarget,
        quality_transcriber: Transcriber | None = None,
        text_processor: TextProcessor | None = None,
        controls: RecordingControls | None = None,
        status_callback: StatusCallback | None = None,
        level_callback: LevelCallback | None = None,
        background: bool = True,
    ):
        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.quality_transcriber = quality_transcriber
        self.paste_target = paste_target
        self.text_processor = text_processor
        self.controls = controls
        self.status_callback = status_callback
        self.level_callback = level_callback
        self.background = background
        self.state = DictationState.IDLE
        self.output_mode = OutputMode.LIVE_PASTE
        self.last_error = ""
        self._lock = threading.RLock()
        self._live_stop_event: threading.Event | None = None
        self._live_thread: threading.Thread | None = None
        self._focus_target: FocusTarget | None = None
        self._session_id = 0
        self._level_stop_event: threading.Event | None = None
        self._level_thread: threading.Thread | None = None
        self.chunks = ChunkPipeline(
            config=config,
            recorder=recorder,
            fast_transcriber=transcriber,
            quality_transcriber=quality_transcriber,
            session_active=self._session_active,
        )

    def start_recording(self, mode: OutputMode = OutputMode.LIVE_PASTE) -> bool:
        with self._lock:
            if self.state not in {DictationState.IDLE, DictationState.ERROR}:
                return False
            try:
                self._session_id += 1
                session_id = self._session_id
                self._focus_target = capture_focus_target()
                self.output_mode = mode
                self.recorder.start()
                self.chunks.reset()
                self._start_level_worker(session_id)
                if self.controls is not None:
                    self.controls.enable_recording_controls()
                if mode == OutputMode.LIVE_PASTE:
                    if self.config.live_streaming:
                        self._start_live_worker(session_id)
                    elif self.config.background_chunking:
                        self.chunks.start(session_id)
                    self._set_state(DictationState.RECORDING, "Live-Diktat laeuft")
                else:
                    if self.config.background_chunking:
                        self.chunks.start(session_id)
                    self._set_state(DictationState.RECORDING, "Clipboard-Aufnahme laeuft")
                self._restore_focus_target()
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
            session_id = self._session_id
            try:
                self._stop_level_worker(wait=False)
                if self.chunks.fast_active():
                    self.chunks.request_stop()
                    final_audio = self.recorder.stop_if_audio()
                    self.chunks.stop_quality(wait=False, close_backend=True)
                    if mode == OutputMode.CLIPBOARD:
                        self._set_state(DictationState.TRANSCRIBING, "Transkription fuer Zwischenablage laeuft")
                    else:
                        self._set_state(DictationState.TRANSCRIBING, "Transkription laeuft")
                    target = self._transcribe_final_with_chunks
                    args = (final_audio, mode, session_id)
                elif mode == OutputMode.LIVE_PASTE and self.config.live_streaming:
                    final_audio = self.recorder.stop_if_audio()
                    self._set_state(DictationState.TRANSCRIBING, "Live-Diktat wird abgeschlossen")
                    target = self._finish_live_recording
                    args = (final_audio, session_id)
                else:
                    final_audio = self.recorder.stop()
                    if mode == OutputMode.CLIPBOARD:
                        self._set_state(DictationState.TRANSCRIBING, "Transkription fuer Zwischenablage laeuft")
                    else:
                        self._set_state(DictationState.TRANSCRIBING, "Transkription laeuft")
                    target = self._transcribe_final
                    args = (final_audio, mode, session_id)
                if self.controls is not None:
                    self.controls.disable_recording_controls(force=True)
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
                self._session_id += 1
                self._stop_live_worker(wait=False)
                self.chunks.stop_fast(wait=False)
                self.chunks.stop_quality(wait=False, close_backend=True)
                self._stop_level_worker(wait=False)
                if self.controls is not None:
                    self.controls.disable_recording_controls(force=True)
                self.recorder.cancel()
                self.chunks.clear(delete_audio=True)
                self._set_state(DictationState.IDLE, "Aufnahme abgebrochen")
                return True
            except Exception as exc:
                self._set_error(exc)
                return False

    def hard_abort(self) -> bool:
        with self._lock:
            self._session_id += 1
            self._stop_live_worker(wait=False)
            self.chunks.stop_fast(wait=False)
            self.chunks.stop_quality(wait=False, close_backend=True)
            self._stop_level_worker(wait=False)
            if self.controls is not None:
                self.controls.disable_recording_controls(force=True)
            try:
                self.recorder.cancel()
            except Exception:
                LOG.debug("Failed to cancel recorder during hard abort", exc_info=True)
            self.chunks.clear(delete_audio=True)
            self.state = DictationState.IDLE
            self.last_error = ""

        self._close_processing_backends()
        self._set_state(DictationState.IDLE, "Hart abgebrochen")
        return True

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
            self.chunks.stop_fast(wait=False)
            self.chunks.stop_quality(wait=False, close_backend=True)
            self._stop_level_worker(wait=False)
            if self.controls is not None:
                self.controls.disable_recording_controls(force=True)
            if self.state == DictationState.RECORDING:
                try:
                    self.recorder.cancel()
                except Exception:
                    LOG.debug("Failed to cancel active recording during shutdown", exc_info=True)
            self._session_id += 1
            self.chunks.clear(delete_audio=True)
            self._close_processing_backends()
            self._set_state(DictationState.IDLE, "Beendet")

    def _start_live_worker(self, session_id: int) -> None:
        self._stop_live_worker(wait=True)
        self._live_stop_event = threading.Event()
        self._live_thread = threading.Thread(target=self._live_loop, args=(session_id,), daemon=True)
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

    def _start_level_worker(self, session_id: int) -> None:
        if self.level_callback is None:
            return
        self._stop_level_worker(wait=True)
        self._level_stop_event = threading.Event()
        self._level_thread = threading.Thread(target=self._level_loop, args=(session_id,), daemon=True)
        self._level_thread.start()

    def _stop_level_worker(self, wait: bool) -> None:
        event = self._level_stop_event
        thread = self._level_thread
        if event is not None:
            event.set()
        if wait and thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._level_stop_event = None
        self._level_thread = None
        if self.level_callback is not None:
            try:
                self.level_callback(0.0)
            except Exception:
                LOG.debug("Audio level callback failed", exc_info=True)

    def _level_loop(self, session_id: int) -> None:
        event = self._level_stop_event
        if event is None or self.level_callback is None:
            return

        while not event.wait(0.055):
            if not self._session_active(session_id):
                return
            try:
                self.level_callback(self.recorder.current_level())
            except Exception:
                LOG.debug("Audio level callback failed", exc_info=True)

    def _live_loop(self, session_id: int) -> None:
        event = self._live_stop_event
        if event is None:
            return

        while not event.wait(max(1, self.config.live_chunk_seconds)):
            if not self._session_active(session_id):
                return
            try:
                chunk = self.recorder.pop_chunk()
                if chunk is not None:
                    self._transcribe_and_output(chunk, OutputMode.LIVE_PASTE, live_chunk=True, session_id=session_id)
            except Exception as exc:
                self._set_error(exc)
                event.set()
                try:
                    self.recorder.cancel()
                except Exception:
                    LOG.debug("Failed to cancel recording after live error", exc_info=True)
                return

    def _finish_live_recording(self, final_audio: Path | None, session_id: int) -> None:
        try:
            self._stop_live_worker(wait=True)
            if not self._session_active(session_id):
                return
            if final_audio is not None:
                self._transcribe_and_output(final_audio, OutputMode.LIVE_PASTE, live_chunk=False, session_id=session_id)
            if self._session_active(session_id):
                self._set_state(DictationState.IDLE, "Live-Diktat beendet und in Zwischenablage")
        except Exception as exc:
            if self._session_active(session_id):
                self._set_error(exc)
        finally:
            if self._session_active(session_id):
                self._disable_recording_controls()

    def _transcribe_final(self, audio_path: Path, mode: OutputMode, session_id: int) -> None:
        try:
            if not self._session_active(session_id):
                unlink_audio(audio_path)
                return
            self._transcribe_and_output(audio_path, mode, live_chunk=False, session_id=session_id)
            if self._session_active(session_id):
                if mode == OutputMode.CLIPBOARD:
                    self._set_state(DictationState.IDLE, "Text in Zwischenablage")
                else:
                    self._set_state(DictationState.IDLE, "Text eingefuegt und in Zwischenablage")
        except Exception as exc:
            if self._session_active(session_id):
                self._set_error(exc)
        finally:
            if self._session_active(session_id):
                self._disable_recording_controls()

    def _transcribe_final_with_chunks(
        self,
        final_audio: Path | None,
        mode: OutputMode,
        session_id: int,
    ) -> None:
        try:
            if self.chunks.fast_active():
                self._set_state(DictationState.TRANSCRIBING, "Warte auf Chunk-Vorverarbeitung")
            while self.chunks.fast_active() and self._session_active(session_id):
                done, total = self.chunks.fast_progress()
                if total:
                    self._set_state(
                        DictationState.TRANSCRIBING,
                        f"Vorverarbeitet {done}/{total} Chunks",
                    )
                time.sleep(0.35)
            self.chunks.stop_fast(wait=True)
            quality_wait = max(0.0, float(self.config.quality_wait_after_stop_seconds))
            quality_deadline = time.monotonic() + quality_wait
            while (
                self.chunks.quality_active()
                and self._session_active(session_id)
                and time.monotonic() < quality_deadline
            ):
                done, total = self.chunks.quality_progress()
                if total:
                    self._set_state(
                        DictationState.TRANSCRIBING,
                        f"Qualitaet {done}/{total} Bloecke",
                    )
                time.sleep(0.25)
            quality_still_active = self.chunks.quality_active()
            self.chunks.stop_quality(wait=False, close_backend=quality_still_active)
            if not self._session_active(session_id):
                if final_audio is not None:
                    unlink_audio(final_audio)
                return

            transcript = self.chunks.assemble_transcript(
                final_audio=final_audio,
                transcribe_audio=lambda audio_path: self._transcribe_audio_path(audio_path, session_id),
                progress=lambda done, total: self._set_state(
                    DictationState.TRANSCRIBING,
                    f"Verarbeite {done}/{total} Teile",
                ),
            )
            if not self._session_active(session_id):
                return
            if not transcript:
                self._set_state(DictationState.IDLE, "Kein Text erkannt")
                return

            self._process_and_output_transcript(
                transcript,
                mode,
                live_chunk=False,
                session_id=session_id,
            )
            if self._session_active(session_id):
                if mode == OutputMode.CLIPBOARD:
                    self._set_state(DictationState.IDLE, "Text in Zwischenablage")
                else:
                    self._set_state(DictationState.IDLE, "Text eingefuegt und in Zwischenablage")
        except Exception as exc:
            if self._session_active(session_id):
                self._set_error(exc)
        finally:
            self.chunks.clear(delete_audio=True)
            if self._session_active(session_id):
                self._disable_recording_controls()

    def _transcribe_and_output(
        self,
        audio_path: Path,
        mode: OutputMode,
        live_chunk: bool,
        session_id: int | None = None,
    ) -> None:
        session_id = self._session_id if session_id is None else session_id
        try:
            if not self._session_active(session_id):
                unlink_audio(audio_path)
                return
            if live_chunk:
                self._publish_recording_status("Text wird verarbeitet")
            transcript = self._transcribe_audio_path(audio_path, session_id)
            self._process_and_output_transcript(transcript, mode, live_chunk, session_id)
        finally:
            if live_chunk and self._session_active(session_id):
                self._publish_recording_status("Live-Diktat laeuft")

    def _transcribe_audio_path(self, audio_path: Path, session_id: int) -> str:
        try:
            if not self._session_active(session_id):
                return ""
            return self.transcriber.transcribe(audio_path).strip()
        finally:
            unlink_audio(audio_path)

    def _process_and_output_transcript(
        self,
        transcript: str,
        mode: OutputMode,
        live_chunk: bool,
        session_id: int,
    ) -> bool:
        if not self._session_active(session_id):
            return False
        transcript = transcript.strip()
        if not transcript:
            if not live_chunk:
                self._set_state(DictationState.IDLE, "Kein Text erkannt")
            return False
        if (
            self.text_processor is not None
            and self.text_processor.will_process(mode, live_chunk)
        ):
            self._set_state(DictationState.TRANSCRIBING, "Text wird lokal nachkorrigiert")
            transcript = self.text_processor.process(transcript, mode, live_chunk).strip()
            if not self._session_active(session_id):
                return False
            if not transcript:
                if not live_chunk:
                    self._set_state(DictationState.IDLE, "Kein Text erkannt")
                return False

        if mode == OutputMode.CLIPBOARD:
            self._set_state(DictationState.PASTING, "Text wird in Zwischenablage gelegt")
            self.paste_target.copy_text(transcript)
        else:
            text = _format_live_text(transcript) if live_chunk else transcript
            self._restore_focus_target()
            if not self._session_active(session_id):
                return False
            self.paste_target.paste_text(text)
        return True

    def _restore_focus_target(self) -> None:
        if self._focus_target is not None:
            self._focus_target.restore()

    def _publish_recording_status(self, message: str) -> None:
        with self._lock:
            if self.state != DictationState.RECORDING:
                return
        LOG.info("%s: %s", DictationState.RECORDING.value, message)
        if self.status_callback is not None:
            self.status_callback(DictationState.RECORDING, message)

    def _session_active(self, session_id: int) -> bool:
        with self._lock:
            return session_id == self._session_id

    def _close_processing_backends(self) -> None:
        close = getattr(self.transcriber, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                LOG.debug("Failed to close transcriber backend", exc_info=True)
        quality_close = getattr(self.quality_transcriber, "close", None)
        if callable(quality_close):
            try:
                quality_close()
            except Exception:
                LOG.debug("Failed to close quality transcriber backend", exc_info=True)
        processor_close = getattr(self.text_processor, "close", None)
        if callable(processor_close):
            try:
                processor_close()
            except Exception:
                LOG.debug("Failed to close text processor backend", exc_info=True)

    def _disable_recording_controls(self) -> None:
        if self.controls is not None:
            self.controls.disable_recording_controls()

    def _set_state(self, state: DictationState, message: str) -> None:
        with self._lock:
            self.state = state
            if state != DictationState.ERROR:
                self.last_error = ""
        LOG.info("%s: %s", state.value, message)
        if self.status_callback is not None:
            self.status_callback(state, message)

    def _set_error(self, exc: Exception) -> None:
        self._stop_live_worker(wait=False)
        self.chunks.stop_fast(wait=False)
        self.chunks.stop_quality(wait=False, close_backend=True)
        self._stop_level_worker(wait=False)
        self._disable_recording_controls()
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
