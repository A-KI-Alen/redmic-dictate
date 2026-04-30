from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .benchmark import benchmark_models, record_sample
from .chunking import ChunkPipeline, ChunkResult, copy_audio_file, unlink_audio
from .config import AppConfig
from .focus import FocusTarget, capture_focus_target
from .openai_realtime import (
    OpenAIRealtimeTranscriptionSession,
    RealtimeTranscriptResult,
    RealtimeUnavailableError,
    is_openai_realtime_enabled,
)
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


class Tracker(Protocol):
    def record(self, event: str, session_id: int | None = None, **data: object) -> None: ...
    def transcript_fields(self, text: str, prefix: str = "transcript") -> dict[str, object]: ...


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
        tracker: Tracker | None = None,
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
        self.tracker = tracker
        self.background = background
        self.state = DictationState.IDLE
        self.output_mode = OutputMode.LIVE_PASTE
        self.last_error = ""
        self._lock = threading.RLock()
        self._live_stop_event: threading.Event | None = None
        self._live_thread: threading.Thread | None = None
        self._focus_target: FocusTarget | None = None
        self._session_id = 0
        self._session_started_at: dict[int, float] = {}
        self._progressive_pasted_chunks: set[int] = set()
        self._realtime_session: OpenAIRealtimeTranscriptionSession | None = None
        self._level_stop_event: threading.Event | None = None
        self._level_thread: threading.Thread | None = None
        self.chunks = ChunkPipeline(
            config=config,
            recorder=recorder,
            fast_transcriber=transcriber,
            quality_transcriber=quality_transcriber,
            session_active=self._session_active,
            tracker=tracker,
            fast_result_callback=self._on_fast_chunk_completed,
        )

    def start_recording(self, mode: OutputMode = OutputMode.LIVE_PASTE) -> bool:
        with self._lock:
            if self.state not in {DictationState.IDLE, DictationState.ERROR}:
                return False
            try:
                self._session_id += 1
                session_id = self._session_id
                self._session_started_at[session_id] = time.monotonic()
                self._progressive_pasted_chunks = set()
                self._focus_target = capture_focus_target()
                self.output_mode = mode
                self._track(
                    "session_started",
                    session_id,
                    mode=mode.value,
                    model=self.config.resolved_model(),
                    quality_model=self.config.quality_model if self.config.quality_chunking else "",
                    background_chunking=self.config.background_chunking,
                    background_chunk_seconds=self.config.background_chunk_seconds,
                    quality_chunk_seconds=self.config.quality_chunk_seconds,
                )
                self.recorder.start()
                self._track("recording_started", session_id, mode=mode.value)
                self.chunks.reset()
                self._start_level_worker(session_id)
                if self.controls is not None:
                    self.controls.enable_recording_controls()
                realtime_started = self._try_start_realtime(session_id, mode)
                if realtime_started:
                    if mode == OutputMode.CLIPBOARD:
                        self._set_state(
                            DictationState.RECORDING,
                            "OpenAI Mini Clipboard-Aufnahme laeuft",
                        )
                    else:
                        self._set_state(
                            DictationState.RECORDING,
                            "OpenAI Mini Live-Diktat laeuft",
                        )
                elif mode == OutputMode.LIVE_PASTE:
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
                chunked = False
                realtime_session = self._realtime_session
                self._realtime_session = None
                if realtime_session is not None:
                    final_audio = self.recorder.stop_if_audio()
                    if mode == OutputMode.CLIPBOARD:
                        self._set_state(DictationState.TRANSCRIBING, "OpenAI Mini fuer Zwischenablage laeuft")
                    else:
                        self._set_state(DictationState.TRANSCRIBING, "OpenAI Mini wird abgeschlossen")
                    target = self._finish_realtime_recording
                    args = (final_audio, mode, session_id, realtime_session)
                elif self.chunks.fast_active():
                    chunked = True
                    self.chunks.request_stop()
                    final_audio = self.recorder.stop_if_audio()
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
                self._track(
                    "recording_stop_requested",
                    session_id,
                    mode=mode.value,
                    final_audio=final_audio.name if final_audio is not None else "",
                    elapsed_ms=self._session_elapsed_ms(session_id),
                    chunked=chunked,
                )
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
                session_id = self._session_id
                self._session_id += 1
                self._stop_live_worker(wait=False)
                self._cancel_realtime_session()
                self.chunks.stop_fast(wait=False)
                self.chunks.stop_quality(wait=False, close_backend=True)
                self._stop_level_worker(wait=False)
                if self.controls is not None:
                    self.controls.disable_recording_controls(force=True)
                self.recorder.cancel()
                self.chunks.clear(delete_audio=True)
                self._track(
                    "session_cancelled",
                    session_id,
                    elapsed_ms=self._session_elapsed_ms(session_id),
                )
                self._session_started_at.pop(session_id, None)
                self._set_state(DictationState.IDLE, "Aufnahme abgebrochen")
                return True
            except Exception as exc:
                self._set_error(exc)
                return False

    def hard_abort(self) -> bool:
        with self._lock:
            session_id = self._session_id
            self._session_id += 1
            self._stop_live_worker(wait=False)
            self._cancel_realtime_session()
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
            self._track(
                "session_hard_aborted",
                session_id,
                elapsed_ms=self._session_elapsed_ms(session_id),
            )
            self._session_started_at.pop(session_id, None)

        self._close_processing_backends()
        self._set_state(DictationState.IDLE, "Hart abgebrochen")
        return True

    def benchmark(self, record_seconds: int = 8) -> bool:
        with self._lock:
            if self.state not in {DictationState.IDLE, DictationState.ERROR}:
                return False
            self._set_state(DictationState.BENCHMARKING, "Benchmark-Aufnahme laeuft")
            self._track("benchmark_started", None, record_seconds=record_seconds)

        def run() -> None:
            try:
                sample = record_sample(self.config, record_seconds, benchmark_sample_path())
                self._set_state(DictationState.BENCHMARKING, "Modelle werden gemessen")
                selected, _ = benchmark_models(self.config, sample)
                self._track("benchmark_finished", None, selected_model=selected)
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
            self._cancel_realtime_session()
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
            self._track("app_shutdown", None, active_session_id=self._session_id)
            self._set_state(DictationState.IDLE, "Beendet")

    def _try_start_realtime(self, session_id: int, mode: OutputMode) -> bool:
        if not is_openai_realtime_enabled(self.config):
            return False
        if not hasattr(self.recorder, "read_stream_chunk") or not hasattr(self.recorder, "actual_sample_rate"):
            self._track(
                "openai_realtime_fallback",
                session_id,
                reason="recorder_has_no_stream_tap",
            )
            return False

        realtime = OpenAIRealtimeTranscriptionSession(
            self.config,
            self.recorder,  # type: ignore[arg-type]
            on_text=lambda text: self._on_realtime_text(text, mode, session_id)
            if mode == OutputMode.LIVE_PASTE
            else None,
        )
        try:
            realtime.start()
        except RealtimeUnavailableError as exc:
            self._track(
                "openai_realtime_fallback",
                session_id,
                reason=str(exc),
                fallback_backend=self.config.cloud_fallback,
            )
            LOG.info("OpenAI Realtime unavailable; using local fallback: %s", exc)
            return False
        except Exception as exc:
            self._track(
                "openai_realtime_fallback",
                session_id,
                reason=str(exc),
                fallback_backend=self.config.cloud_fallback,
            )
            LOG.warning("OpenAI Realtime start failed; using local fallback", exc_info=True)
            return False

        self._realtime_session = realtime
        self._track(
            "openai_realtime_started",
            session_id,
            mode=mode.value,
            session_model=self.config.openai_realtime_session_model,
            transcription_model=self.config.openai_realtime_transcription_model,
            commit_seconds=self.config.openai_realtime_commit_seconds,
        )
        return True

    def _cancel_realtime_session(self) -> None:
        realtime = self._realtime_session
        self._realtime_session = None
        if realtime is not None:
            try:
                realtime.cancel()
            except Exception:
                LOG.debug("Failed to cancel OpenAI Realtime session", exc_info=True)

    def _on_realtime_text(self, text: str, mode: OutputMode, session_id: int) -> bool:
        if mode != OutputMode.LIVE_PASTE:
            return False
        with self._lock:
            if not self._session_active(session_id) or self.state != DictationState.RECORDING:
                return False
        return self._process_and_output_transcript(
            text,
            OutputMode.LIVE_PASTE,
            live_chunk=True,
            session_id=session_id,
        )

    def _finish_realtime_recording(
        self,
        final_audio: Path | None,
        mode: OutputMode,
        session_id: int,
        realtime: OpenAIRealtimeTranscriptionSession,
    ) -> None:
        started_at = time.perf_counter()
        try:
            result = realtime.stop()
            self._track(
                "openai_realtime_finished",
                session_id,
                mode=mode.value,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                delivered_chars=result.delivered_chars,
                error=result.error,
                **self._transcript_fields(result.transcript),
            )
            if not self._session_active(session_id):
                if final_audio is not None:
                    unlink_audio(final_audio)
                return
            if result.error and not result.transcript:
                self._fallback_after_realtime(final_audio, mode, session_id, result)
                final_audio = None
                return
            if not result.transcript:
                self._fallback_after_realtime(final_audio, mode, session_id, result)
                final_audio = None
                return

            if mode == OutputMode.CLIPBOARD:
                self._process_and_output_transcript(result.transcript, mode, live_chunk=False, session_id=session_id)
            elif result.delivered_any:
                missing_text = _missing_realtime_suffix(result.transcript, result.delivered_text)
                if missing_text:
                    self._process_and_output_transcript(
                        missing_text,
                        mode,
                        live_chunk=False,
                        session_id=session_id,
                    )
                self.paste_target.copy_text(result.transcript)
                self._track(
                    "output_written",
                    session_id,
                    mode=mode.value,
                    live_chunk=False,
                    destination="clipboard_full_transcript",
                    **self._transcript_fields(result.transcript),
                )
            else:
                self._process_and_output_transcript(result.transcript, mode, live_chunk=False, session_id=session_id)

            if self._session_active(session_id):
                self._finish_session(session_id, "finished", mode, transcript=result.transcript)
                if mode == OutputMode.CLIPBOARD:
                    self._set_state(DictationState.IDLE, "Text in Zwischenablage")
                else:
                    self._set_state(DictationState.IDLE, "Text eingefuegt und in Zwischenablage")
        except Exception as exc:
            if self._session_active(session_id):
                self._set_error(exc)
        finally:
            if final_audio is not None:
                unlink_audio(final_audio)
            if self._session_active(session_id):
                self._disable_recording_controls()

    def _fallback_after_realtime(
        self,
        final_audio: Path | None,
        mode: OutputMode,
        session_id: int,
        result: RealtimeTranscriptResult,
    ) -> None:
        if final_audio is None:
            self._finish_session(session_id, "no_text", mode, transcript=result.transcript)
            self._set_state(DictationState.IDLE, "Kein Text erkannt")
            return

        self._track(
            "openai_realtime_local_fallback_started",
            session_id,
            mode=mode.value,
            reason=result.error or "empty_realtime_transcript",
            delivered_chars=result.delivered_chars,
        )
        self._set_state(DictationState.TRANSCRIBING, "Lokaler Fallback laeuft")
        if result.delivered_any and mode == OutputMode.LIVE_PASTE:
            transcript = self._transcribe_audio_path(final_audio, session_id)
            if transcript:
                self.paste_target.copy_text(transcript)
                self._track(
                    "output_written",
                    session_id,
                    mode=mode.value,
                    live_chunk=False,
                    destination="clipboard_local_fallback_full_transcript",
                    **self._transcript_fields(transcript),
                )
                self._finish_session(session_id, "fallback_clipboard", mode, transcript=transcript)
                self._set_state(DictationState.IDLE, "Lokaler Fallback in Zwischenablage")
            else:
                self._finish_session(session_id, "no_text", mode)
                self._set_state(DictationState.IDLE, "Kein Text erkannt")
            return

        wrote = self._transcribe_and_output(final_audio, mode, live_chunk=False, session_id=session_id)
        self._finish_session(session_id, "fallback_finished" if wrote else "no_text", mode)
        if mode == OutputMode.CLIPBOARD:
            self._set_state(DictationState.IDLE, "Text in Zwischenablage")
        else:
            self._set_state(DictationState.IDLE, "Text eingefuegt und in Zwischenablage")

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
            wrote = True
            if final_audio is not None:
                wrote = self._transcribe_and_output(
                    final_audio,
                    OutputMode.LIVE_PASTE,
                    live_chunk=False,
                    session_id=session_id,
                )
            if self._session_active(session_id):
                self._finish_session(session_id, "finished" if wrote else "no_text", OutputMode.LIVE_PASTE)
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
            wrote = self._transcribe_and_output(audio_path, mode, live_chunk=False, session_id=session_id)
            if self._session_active(session_id):
                self._finish_session(session_id, "finished" if wrote else "no_text", mode)
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
        quality_guard_audio: Path | None = None
        progressive_final_audio: Path | None = None
        try:
            self._track(
                "chunked_final_started",
                session_id,
                mode=mode.value,
                final_audio=final_audio.name if final_audio is not None else "",
            )
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
            fast_done, fast_total = self.chunks.fast_progress()
            self._track(
                "fast_chunks_drained",
                session_id,
                chunks_done=fast_done,
                chunks_total=fast_total,
            )
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
            quality_done, quality_total = self.chunks.quality_progress()
            self._track(
                "quality_wait_finished",
                session_id,
                chunks_done=quality_done,
                chunks_total=quality_total,
                still_active=quality_still_active,
            )
            self.chunks.stop_quality(wait=False, close_backend=quality_still_active)
            if not self._session_active(session_id):
                if final_audio is not None:
                    unlink_audio(final_audio)
                return

            pasted_indexes = self._progressive_pasted_indexes(mode)
            if pasted_indexes and final_audio is not None:
                progressive_final_audio = copy_audio_file(final_audio)
            quality_guard_audio = self._build_quality_guard_audio(final_audio, session_id)
            transcript = self.chunks.assemble_transcript(
                final_audio=final_audio,
                transcribe_audio=lambda audio_path: self._transcribe_audio_path(audio_path, session_id),
                progress=lambda done, total: self._set_state(
                    DictationState.TRANSCRIBING,
                    f"Verarbeite {done}/{total} Teile",
                ),
            )
            self._track(
                "chunked_final_assembled",
                session_id,
                mode=mode.value,
                **self._transcript_fields(transcript),
            )
            if not self._session_active(session_id):
                if quality_guard_audio is not None:
                    unlink_audio(quality_guard_audio)
                    quality_guard_audio = None
                return
            if not transcript:
                if quality_guard_audio is not None:
                    unlink_audio(quality_guard_audio)
                    quality_guard_audio = None
                if progressive_final_audio is not None:
                    unlink_audio(progressive_final_audio)
                    progressive_final_audio = None
                self._finish_session(session_id, "no_text", mode)
                self._set_state(DictationState.IDLE, "Kein Text erkannt")
                return

            if pasted_indexes and mode == OutputMode.LIVE_PASTE:
                missing_transcript = self.chunks.assemble_transcript(
                    final_audio=progressive_final_audio,
                    transcribe_audio=lambda audio_path: self._transcribe_audio_path(audio_path, session_id),
                    progress=lambda done, total: self._set_state(
                        DictationState.TRANSCRIBING,
                        f"Ergaenze {done}/{total} Teile",
                    ),
                    skip_indexes=pasted_indexes,
                )
                progressive_final_audio = None
                if missing_transcript:
                    self._process_and_output_transcript(
                        missing_transcript,
                        mode,
                        live_chunk=False,
                        session_id=session_id,
                    )
                self.paste_target.copy_text(transcript)
                self._track(
                    "output_written",
                    session_id,
                    mode=mode.value,
                    live_chunk=False,
                    destination="clipboard_full_transcript",
                    **self._transcript_fields(transcript),
                )
            else:
                self._process_and_output_transcript(
                    transcript,
                    mode,
                    live_chunk=False,
                    session_id=session_id,
                )
            if self._session_active(session_id):
                should_run_quality_guard = self._should_run_quality_guard(
                    quality_guard_audio,
                    transcript,
                    session_id,
                )
                self._finish_session(session_id, "finished", mode, transcript=transcript)
                if should_run_quality_guard and quality_guard_audio is not None:
                    self._start_quality_guard(quality_guard_audio, transcript, mode, session_id)
                    quality_guard_audio = None
                elif quality_guard_audio is not None:
                    unlink_audio(quality_guard_audio)
                    quality_guard_audio = None
                if mode == OutputMode.CLIPBOARD:
                    self._set_state(DictationState.IDLE, "Text in Zwischenablage")
                else:
                    self._set_state(DictationState.IDLE, "Text eingefuegt und in Zwischenablage")
        except Exception as exc:
            if self._session_active(session_id):
                self._set_error(exc)
        finally:
            if quality_guard_audio is not None:
                unlink_audio(quality_guard_audio)
            if progressive_final_audio is not None:
                unlink_audio(progressive_final_audio)
            self.chunks.clear(delete_audio=True)
            if self._session_active(session_id):
                self._disable_recording_controls()

    def _transcribe_and_output(
        self,
        audio_path: Path,
        mode: OutputMode,
        live_chunk: bool,
        session_id: int | None = None,
    ) -> bool:
        session_id = self._session_id if session_id is None else session_id
        try:
            if not self._session_active(session_id):
                unlink_audio(audio_path)
                return False
            if live_chunk:
                self._publish_recording_status("Text wird verarbeitet")
            transcript = self._transcribe_audio_path(audio_path, session_id)
            return self._process_and_output_transcript(transcript, mode, live_chunk, session_id)
        finally:
            if live_chunk and self._session_active(session_id):
                self._publish_recording_status("Live-Diktat laeuft")

    def _transcribe_audio_path(self, audio_path: Path, session_id: int) -> str:
        started_at = time.perf_counter()
        transcript = ""
        try:
            if not self._session_active(session_id):
                return ""
            transcript = self.transcriber.transcribe(audio_path).strip()
            self._track(
                "audio_transcribed",
                session_id,
                audio_file=audio_path.name,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                **self._transcript_fields(transcript),
            )
            return transcript
        except Exception as exc:
            self._track(
                "transcription_failed",
                session_id,
                audio_file=audio_path.name,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                error=str(exc),
            )
            raise
        finally:
            unlink_audio(audio_path)

    def _on_fast_chunk_completed(self, result: ChunkResult, session_id: int) -> None:
        if not self.config.progressive_live_paste:
            return
        if not result.text.strip():
            return
        with self._lock:
            if not self._session_active(session_id):
                return
            if self.state != DictationState.RECORDING:
                return
            if self.output_mode != OutputMode.LIVE_PASTE:
                return
            if result.index in self._progressive_pasted_chunks:
                return

        if self._process_and_output_transcript(
            result.text,
            OutputMode.LIVE_PASTE,
            live_chunk=True,
            session_id=session_id,
        ):
            with self._lock:
                self._progressive_pasted_chunks.add(result.index)
            self._track(
                "progressive_chunk_pasted",
                session_id,
                chunk_index=result.index,
                **self._transcript_fields(result.text),
            )

    def _progressive_pasted_indexes(self, mode: OutputMode) -> set[int]:
        if mode != OutputMode.LIVE_PASTE or not self.config.progressive_live_paste:
            return set()
        with self._lock:
            return set(self._progressive_pasted_chunks)

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
        self._track(
            "transcript_ready",
            session_id,
            mode=mode.value,
            live_chunk=live_chunk,
            **self._transcript_fields(transcript),
        )
        if (
            self.text_processor is not None
            and self.text_processor.will_process(mode, live_chunk)
        ):
            self._set_state(DictationState.TRANSCRIBING, "Text wird lokal nachkorrigiert")
            cleanup_started_at = time.perf_counter()
            transcript = self.text_processor.process(transcript, mode, live_chunk).strip()
            self._track(
                "cleanup_completed",
                session_id,
                mode=mode.value,
                duration_ms=int((time.perf_counter() - cleanup_started_at) * 1000),
                **self._transcript_fields(transcript),
            )
            if not self._session_active(session_id):
                return False
            if not transcript:
                if not live_chunk:
                    self._set_state(DictationState.IDLE, "Kein Text erkannt")
                return False

        if mode == OutputMode.CLIPBOARD:
            self._set_state(DictationState.PASTING, "Text wird in Zwischenablage gelegt")
            self.paste_target.copy_text(transcript)
            destination = "clipboard"
        else:
            text = _format_live_text(transcript) if live_chunk else transcript
            self._restore_focus_target()
            if not self._session_active(session_id):
                return False
            self.paste_target.paste_text(text)
            destination = "active_field"
        self._track(
            "output_written",
            session_id,
            mode=mode.value,
            live_chunk=live_chunk,
            destination=destination,
            **self._transcript_fields(transcript),
        )
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

    def _build_quality_guard_audio(self, final_audio: Path | None, session_id: int) -> Path | None:
        if not self.config.quality_guard_enabled or self.quality_transcriber is None:
            return None
        try:
            audio_path = self.chunks.build_quality_guard_audio(final_audio)
            if audio_path is not None:
                self._track("quality_guard_audio_built", session_id, audio_file=audio_path.name)
            return audio_path
        except Exception as exc:
            self._track("quality_guard_audio_error", session_id, error=str(exc))
            LOG.debug("Could not build quality guard audio", exc_info=True)
            return None

    def _should_run_quality_guard(
        self,
        audio_path: Path | None,
        transcript: str,
        session_id: int,
    ) -> bool:
        if audio_path is None or self.quality_transcriber is None:
            return False
        if not self.config.quality_guard_enabled:
            return False
        elapsed_seconds = self._session_elapsed_ms(session_id) / 1000
        if elapsed_seconds < max(0, int(self.config.quality_guard_min_recording_seconds)):
            self._track(
                "quality_guard_skipped",
                session_id,
                reason="recording_too_short",
                elapsed_ms=self._session_elapsed_ms(session_id),
            )
            return False

        covered, total, ratio = self.chunks.quality_coverage()
        threshold = max(0.0, min(1.0, float(self.config.quality_guard_min_coverage)))
        should_run = bool(total and ratio < threshold)
        self._track(
            "quality_guard_decision",
            session_id,
            covered_chunks=covered,
            total_chunks=total,
            coverage_ratio=round(ratio, 3),
            threshold=threshold,
            will_run=should_run,
            **self._transcript_fields(transcript),
        )
        return should_run

    def _start_quality_guard(
        self,
        audio_path: Path,
        source_transcript: str,
        mode: OutputMode,
        session_id: int,
    ) -> None:
        args = (audio_path, source_transcript, mode, session_id)
        if self.background:
            threading.Thread(target=self._run_quality_guard, args=args, daemon=True).start()
        else:
            self._run_quality_guard(*args)

    def _run_quality_guard(
        self,
        audio_path: Path,
        source_transcript: str,
        mode: OutputMode,
        session_id: int,
    ) -> None:
        started_at = time.perf_counter()
        try:
            if not self._session_active(session_id) or self.quality_transcriber is None:
                return
            self._publish_idle_status("Qualitaetslauf im Hintergrund")
            self._track("quality_guard_started", session_id, mode=mode.value, audio_file=audio_path.name)
            improved = self.quality_transcriber.transcribe(audio_path).strip()
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            if not self._session_active(session_id):
                return
            if not self._usable_quality_guard_text(source_transcript, improved):
                self._track(
                    "quality_guard_rejected",
                    session_id,
                    mode=mode.value,
                    duration_ms=duration_ms,
                    source_chars=len(source_transcript.strip()),
                    **self._transcript_fields(improved, prefix="quality_transcript"),
                )
                return

            self.paste_target.copy_text(improved)
            self._track(
                "quality_guard_completed",
                session_id,
                mode=mode.value,
                duration_ms=duration_ms,
                **self._transcript_fields(improved, prefix="quality_transcript"),
            )
            self._publish_idle_status("Qualitaetsversion in Zwischenablage")
        except Exception as exc:
            self._track(
                "quality_guard_error",
                session_id,
                mode=mode.value,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                error=str(exc),
            )
            LOG.warning("Quality guard failed; keeping fast transcript", exc_info=True)
        finally:
            unlink_audio(audio_path)

    def _usable_quality_guard_text(self, source: str, improved: str) -> bool:
        source = source.strip()
        improved = improved.strip()
        if not improved:
            return False
        if improved == source:
            return False
        min_ratio = max(0.0, float(self.config.quality_guard_min_text_ratio))
        if source and len(improved) < max(12, int(len(source) * min_ratio)):
            return False
        return True

    def _publish_idle_status(self, message: str) -> None:
        with self._lock:
            if self.state != DictationState.IDLE:
                return
        LOG.info("%s: %s", DictationState.IDLE.value, message)
        if self.status_callback is not None:
            self.status_callback(DictationState.IDLE, message)

    def _session_elapsed_ms(self, session_id: int) -> int:
        started_at = self._session_started_at.get(session_id)
        if started_at is None:
            return 0
        return int((time.monotonic() - started_at) * 1000)

    def _finish_session(
        self,
        session_id: int,
        outcome: str,
        mode: OutputMode,
        transcript: str = "",
    ) -> None:
        self._track(
            "session_finished",
            session_id,
            outcome=outcome,
            mode=mode.value,
            elapsed_ms=self._session_elapsed_ms(session_id),
            **self._transcript_fields(transcript),
        )
        self._session_started_at.pop(session_id, None)

    def _transcript_fields(self, text: str, prefix: str = "transcript") -> dict[str, object]:
        if self.tracker is None:
            stripped = text.strip()
            return {
                f"{prefix}_chars": len(stripped),
                f"{prefix}_words": len(stripped.split()) if stripped else 0,
            }
        return self.tracker.transcript_fields(text, prefix=prefix)

    def _track(self, event: str, session_id: int | None = None, **data: object) -> None:
        if self.tracker is None:
            return
        try:
            self.tracker.record(event, session_id, **data)
        except Exception:
            LOG.debug("Tracking failed for event %s", event, exc_info=True)

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
            session_id = self._session_id
        LOG.info("%s: %s", state.value, message)
        self._track("state_changed", session_id, state=state.value, message=message)
        if self.status_callback is not None:
            self.status_callback(state, message)

    def _set_error(self, exc: Exception) -> None:
        session_id = self._session_id
        self._track(
            "session_error",
            session_id,
            error=str(exc),
            elapsed_ms=self._session_elapsed_ms(session_id),
        )
        self._session_started_at.pop(session_id, None)
        self._stop_live_worker(wait=False)
        self._cancel_realtime_session()
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


def _missing_realtime_suffix(transcript: str, delivered_text: str) -> str:
    full = " ".join(transcript.split())
    delivered = " ".join(delivered_text.split())
    if not full or not delivered:
        return full
    if full.startswith(delivered):
        return full[len(delivered) :].strip()
    return ""
