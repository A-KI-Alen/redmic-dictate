from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .benchmark import benchmark_models, record_sample
from .config import AppConfig
from .focus import FocusTarget, capture_focus_target
from .paths import benchmark_sample_path, temp_dir
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


@dataclass(slots=True)
class _ChunkResult:
    index: int
    text: str = ""
    audio_path: Path | None = None


@dataclass(slots=True)
class _QualityWork:
    start_index: int
    end_index: int
    audio_path: Path


@dataclass(slots=True)
class _QualityResult:
    start_index: int
    end_index: int
    text: str = ""


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
        self._chunk_stop_event: threading.Event | None = None
        self._chunk_thread: threading.Thread | None = None
        self._chunk_transcribe_thread: threading.Thread | None = None
        self._chunk_queue: queue.Queue[_ChunkResult] | None = None
        self._quality_queue: queue.Queue[_QualityWork] | None = None
        self._quality_thread: threading.Thread | None = None
        self._chunk_lock = threading.RLock()
        self._chunk_results: list[_ChunkResult] = []
        self._quality_results: list[_QualityResult] = []
        self._quality_pending_chunks: list[tuple[int, Path]] = []
        self._quality_accept_session_id: int | None = None
        self._chunk_index = 0
        self._chunk_in_progress = 0
        self._quality_in_progress = 0

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
                self._reset_chunk_results()
                self._start_level_worker(session_id)
                if self.controls is not None:
                    self.controls.enable_recording_controls()
                if mode == OutputMode.LIVE_PASTE:
                    if self.config.live_streaming:
                        self._start_live_worker(session_id)
                    elif self.config.background_chunking:
                        self._start_chunk_worker(session_id)
                    self._set_state(DictationState.RECORDING, "Live-Diktat laeuft")
                else:
                    if self.config.background_chunking:
                        self._start_chunk_worker(session_id)
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
                if self._chunk_worker_active():
                    self._request_chunk_worker_stop()
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
                self._stop_chunk_worker(wait=False)
                self._stop_quality_worker(wait=False, close_backend=True)
                self._stop_level_worker(wait=False)
                if self.controls is not None:
                    self.controls.disable_recording_controls(force=True)
                self.recorder.cancel()
                self._clear_chunk_results(delete_audio=True)
                self._set_state(DictationState.IDLE, "Aufnahme abgebrochen")
                return True
            except Exception as exc:
                self._set_error(exc)
                return False

    def hard_abort(self) -> bool:
        with self._lock:
            self._session_id += 1
            self._stop_live_worker(wait=False)
            self._stop_chunk_worker(wait=False)
            self._stop_quality_worker(wait=False, close_backend=True)
            self._stop_level_worker(wait=False)
            if self.controls is not None:
                self.controls.disable_recording_controls(force=True)
            try:
                self.recorder.cancel()
            except Exception:
                LOG.debug("Failed to cancel recorder during hard abort", exc_info=True)
            self._clear_chunk_results(delete_audio=True)
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
            self._stop_chunk_worker(wait=False)
            self._stop_quality_worker(wait=False, close_backend=True)
            self._stop_level_worker(wait=False)
            if self.controls is not None:
                self.controls.disable_recording_controls(force=True)
            if self.state == DictationState.RECORDING:
                try:
                    self.recorder.cancel()
                except Exception:
                    LOG.debug("Failed to cancel active recording during shutdown", exc_info=True)
            self._session_id += 1
            self._clear_chunk_results(delete_audio=True)
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

    def _start_chunk_worker(self, session_id: int) -> None:
        self._stop_chunk_worker(wait=True)
        self._stop_quality_worker(wait=False, close_backend=False)
        self._chunk_stop_event = threading.Event()
        self._chunk_queue = queue.Queue()
        self._chunk_thread = threading.Thread(target=self._chunk_producer_loop, args=(session_id,), daemon=True)
        self._chunk_transcribe_thread = threading.Thread(
            target=self._chunk_transcribe_loop,
            args=(session_id,),
            daemon=True,
        )
        if self.quality_transcriber is not None and self.config.quality_chunking:
            self._quality_queue = queue.Queue()
            self._quality_accept_session_id = session_id
            self._quality_thread = threading.Thread(
                target=self._quality_transcribe_loop,
                args=(session_id,),
                daemon=True,
            )
        self._chunk_thread.start()
        self._chunk_transcribe_thread.start()
        if self._quality_thread is not None:
            self._quality_thread.start()

    def _request_chunk_worker_stop(self) -> None:
        if self._chunk_stop_event is not None:
            self._chunk_stop_event.set()

    def _stop_chunk_worker(self, wait: bool) -> None:
        event = self._chunk_stop_event
        producer = self._chunk_thread
        transcriber = self._chunk_transcribe_thread
        if event is not None:
            event.set()
        if wait and producer is not None and producer.is_alive() and producer is not threading.current_thread():
            producer.join(timeout=max(1, int(self.config.background_chunk_seconds) + 1))
        if wait and transcriber is not None and transcriber.is_alive() and transcriber is not threading.current_thread():
            transcriber.join(timeout=300)
        if (
            not wait
            or (
                (producer is None or not producer.is_alive())
                and (transcriber is None or not transcriber.is_alive())
            )
        ):
            self._chunk_stop_event = None
            self._chunk_thread = None
            self._chunk_transcribe_thread = None
            self._chunk_queue = None

    def _stop_quality_worker(self, wait: bool, close_backend: bool) -> None:
        event = self._chunk_stop_event
        thread = self._quality_thread
        work_queue = self._quality_queue
        self._quality_accept_session_id = None
        if event is not None:
            event.set()
        if close_backend:
            close = getattr(self.quality_transcriber, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    LOG.debug("Failed to close quality transcriber backend", exc_info=True)
        if wait and thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.5, float(self.config.quality_wait_after_stop_seconds)))
        if not wait or thread is None or not thread.is_alive():
            if work_queue is not None:
                _drain_quality_queue(work_queue)
            self._quality_thread = None
            self._quality_queue = None

    def _chunk_worker_active(self) -> bool:
        producer = self._chunk_thread
        transcriber = self._chunk_transcribe_thread
        return bool(
            (producer is not None and producer.is_alive())
            or (transcriber is not None and transcriber.is_alive())
            or (self._chunk_queue is not None and not self._chunk_queue.empty())
        )

    def _quality_worker_active(self) -> bool:
        thread = self._quality_thread
        return bool(
            (thread is not None and thread.is_alive())
            or (self._quality_queue is not None and not self._quality_queue.empty())
        )

    def _chunk_producer_loop(self, session_id: int) -> None:
        event = self._chunk_stop_event
        if event is None:
            return

        interval = max(5, int(self.config.background_chunk_seconds))
        next_at = time.monotonic() + interval
        while True:
            if event.wait(max(0.0, next_at - time.monotonic())):
                return
            next_at += interval
            if not self._session_active(session_id):
                return
            audio_path: Path | None = None
            try:
                audio_path = self.recorder.pop_chunk()
                if audio_path is None:
                    continue
                index = self._next_chunk_index()
                LOG.info("Queued audio chunk %s for pre-transcription", index)
                self._maybe_queue_quality_chunk(index, audio_path)
                self._queue_chunk_result(_ChunkResult(index=index, audio_path=audio_path))
            except Exception:
                LOG.exception("Background chunk capture failed")
                if audio_path is not None:
                    _unlink_audio(audio_path)

    def _chunk_transcribe_loop(self, session_id: int) -> None:
        event = self._chunk_stop_event
        work_queue = self._chunk_queue
        if event is None or work_queue is None:
            return

        while self._session_active(session_id):
            try:
                result = work_queue.get(timeout=0.1)
            except queue.Empty:
                if event.is_set():
                    return
                continue

            self._mark_chunk_in_progress(1)
            try:
                if result.audio_path is None:
                    self._store_chunk_result(result)
                    continue
                LOG.info("Pre-transcribing audio chunk %s", result.index)
                text = self.transcriber.transcribe(result.audio_path).strip()
                if not self._session_active(session_id):
                    _unlink_audio(result.audio_path)
                    return
                self._store_chunk_result(_ChunkResult(index=result.index, text=text))
                _unlink_audio(result.audio_path)
            except Exception:
                LOG.exception("Background chunk transcription failed")
                if self._session_active(session_id):
                    self._store_chunk_result(result)
                elif result.audio_path is not None:
                    _unlink_audio(result.audio_path)
            finally:
                self._mark_chunk_in_progress(-1)
                work_queue.task_done()

    def _quality_transcribe_loop(self, session_id: int) -> None:
        event = self._chunk_stop_event
        work_queue = self._quality_queue
        transcriber = self.quality_transcriber
        if event is None or work_queue is None or transcriber is None:
            return

        while self._quality_session_active(session_id):
            try:
                work = work_queue.get(timeout=0.1)
            except queue.Empty:
                if event.is_set():
                    return
                continue

            self._mark_quality_in_progress(1)
            try:
                LOG.info(
                    "Quality-transcribing chunks %s-%s with %s",
                    work.start_index,
                    work.end_index,
                    self.config.quality_model,
                )
                text = transcriber.transcribe(work.audio_path).strip()
                if not self._quality_session_active(session_id):
                    return
                self._store_quality_result(
                    _QualityResult(
                        start_index=work.start_index,
                        end_index=work.end_index,
                        text=text,
                    )
                )
            except Exception:
                LOG.exception("Quality chunk transcription failed")
            finally:
                self._mark_quality_in_progress(-1)
                _unlink_audio(work.audio_path)
                work_queue.task_done()

    def _maybe_queue_quality_chunk(self, index: int, audio_path: Path) -> None:
        if self.quality_transcriber is None or not self.config.quality_chunking:
            return
        if self._quality_queue is None:
            return

        quality_copy: Path | None = None
        group: list[tuple[int, Path]] | None = None
        try:
            quality_copy = _copy_audio_file(audio_path)
            with self._chunk_lock:
                self._quality_pending_chunks.append((index, quality_copy))
                group_size = self._quality_group_size()
                if len(self._quality_pending_chunks) >= group_size:
                    group = self._quality_pending_chunks[:group_size]
                    del self._quality_pending_chunks[:group_size]

            if group is None:
                return

            group_audio = _combine_wav_files([path for _, path in group])
            for _, path in group:
                _unlink_audio(path)
            self._queue_quality_work(
                _QualityWork(
                    start_index=group[0][0],
                    end_index=group[-1][0],
                    audio_path=group_audio,
                )
            )
        except Exception:
            LOG.exception("Could not queue quality chunk")
            if quality_copy is not None:
                _unlink_audio(quality_copy)
            if group is not None:
                for _, path in group:
                    _unlink_audio(path)

    def _next_chunk_index(self) -> int:
        with self._chunk_lock:
            index = self._chunk_index
            self._chunk_index += 1
            return index

    def _store_chunk_result(self, result: _ChunkResult) -> None:
        with self._chunk_lock:
            self._chunk_results.append(result)

    def _store_quality_result(self, result: _QualityResult) -> None:
        if not result.text:
            return
        with self._chunk_lock:
            self._quality_results.append(result)

    def _queue_chunk_result(self, result: _ChunkResult) -> None:
        work_queue = self._chunk_queue
        if work_queue is None:
            if result.audio_path is not None:
                _unlink_audio(result.audio_path)
            return
        work_queue.put(result)

    def _queue_quality_work(self, work: _QualityWork) -> None:
        work_queue = self._quality_queue
        if work_queue is None:
            _unlink_audio(work.audio_path)
            return
        work_queue.put(work)

    def _mark_chunk_in_progress(self, delta: int) -> None:
        with self._chunk_lock:
            self._chunk_in_progress = max(0, self._chunk_in_progress + delta)

    def _mark_quality_in_progress(self, delta: int) -> None:
        with self._chunk_lock:
            self._quality_in_progress = max(0, self._quality_in_progress + delta)

    def _chunk_progress(self) -> tuple[int, int]:
        with self._chunk_lock:
            done = len(self._chunk_results)
            in_progress = self._chunk_in_progress
        queued = self._chunk_queue.qsize() if self._chunk_queue is not None else 0
        return done, done + in_progress + queued

    def _quality_progress(self) -> tuple[int, int]:
        with self._chunk_lock:
            done = len(self._quality_results)
            in_progress = self._quality_in_progress
        queued = self._quality_queue.qsize() if self._quality_queue is not None else 0
        return done, done + in_progress + queued

    def _quality_group_size(self) -> int:
        fast_seconds = max(1, int(self.config.background_chunk_seconds))
        quality_seconds = max(fast_seconds, int(self.config.quality_chunk_seconds))
        return max(1, (quality_seconds + fast_seconds - 1) // fast_seconds)

    def _reset_chunk_results(self) -> None:
        with self._chunk_lock:
            self._chunk_index = 0
            self._chunk_results = []
            self._quality_results = []
            self._quality_pending_chunks = []
            self._chunk_in_progress = 0
            self._quality_in_progress = 0

    def _chunk_results_snapshot(self) -> list[_ChunkResult]:
        with self._chunk_lock:
            return sorted(self._chunk_results, key=lambda result: result.index)

    def _quality_results_snapshot(self) -> list[_QualityResult]:
        with self._chunk_lock:
            return sorted(self._quality_results, key=lambda result: result.start_index)

    def _clear_chunk_results(self, delete_audio: bool) -> None:
        with self._chunk_lock:
            results = self._chunk_results
            pending_quality = self._quality_pending_chunks
            self._chunk_results = []
            self._quality_results = []
            self._quality_pending_chunks = []
            self._chunk_index = 0
            self._chunk_in_progress = 0
            self._quality_in_progress = 0
        if delete_audio:
            for result in results:
                if result.audio_path is not None:
                    _unlink_audio(result.audio_path)
            for _, path in pending_quality:
                _unlink_audio(path)
            work_queue = self._quality_queue
            if work_queue is not None:
                _drain_quality_queue(work_queue)

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
                _unlink_audio(audio_path)
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
            if self._chunk_worker_active():
                self._set_state(DictationState.TRANSCRIBING, "Warte auf Chunk-Vorverarbeitung")
            while self._chunk_worker_active() and self._session_active(session_id):
                done, total = self._chunk_progress()
                if total:
                    self._set_state(
                        DictationState.TRANSCRIBING,
                        f"Vorverarbeitet {done}/{total} Chunks",
                    )
                time.sleep(0.35)
            self._stop_chunk_worker(wait=True)
            quality_wait = max(0.0, float(self.config.quality_wait_after_stop_seconds))
            quality_deadline = time.monotonic() + quality_wait
            while (
                self._quality_worker_active()
                and self._session_active(session_id)
                and time.monotonic() < quality_deadline
            ):
                done, total = self._quality_progress()
                if total:
                    self._set_state(
                        DictationState.TRANSCRIBING,
                        f"Qualitaet {done}/{total} Bloecke",
                    )
                time.sleep(0.25)
            quality_still_active = self._quality_worker_active()
            self._stop_quality_worker(wait=False, close_backend=quality_still_active)
            if not self._session_active(session_id):
                if final_audio is not None:
                    _unlink_audio(final_audio)
                return

            parts: list[str] = []
            results = self._chunk_results_snapshot()
            quality_results = self._quality_results_snapshot()
            total_parts = len(results) + (1 if final_audio is not None else 0)
            done_parts = 0
            if total_parts:
                self._set_state(DictationState.TRANSCRIBING, f"Verarbeite 0/{total_parts} Teile")

            base_by_index = {result.index: result for result in results}
            quality_by_start = {result.start_index: result for result in quality_results if result.text}
            indexes = sorted(base_by_index)
            position = 0
            while position < len(indexes):
                index = indexes[position]
                quality = quality_by_start.get(index)
                if quality is not None:
                    parts.append(quality.text)
                    while position < len(indexes) and indexes[position] <= quality.end_index:
                        position += 1
                        done_parts += 1
                    self._set_state(DictationState.TRANSCRIBING, f"Verarbeite {done_parts}/{total_parts} Teile")
                    continue

                result = base_by_index[index]
                if result.text:
                    parts.append(result.text)
                elif result.audio_path is not None:
                    self._set_state(
                        DictationState.TRANSCRIBING,
                        f"Verarbeite {done_parts + 1}/{total_parts} Teile",
                    )
                    parts.append(self._transcribe_audio_path(result.audio_path, session_id))
                done_parts += 1
                position += 1
                self._set_state(DictationState.TRANSCRIBING, f"Verarbeite {done_parts}/{total_parts} Teile")

            if final_audio is not None:
                self._set_state(
                    DictationState.TRANSCRIBING,
                    f"Verarbeite {done_parts + 1}/{total_parts} Teile",
                )
                parts.append(self._transcribe_audio_path(final_audio, session_id))
                done_parts += 1
                self._set_state(DictationState.TRANSCRIBING, f"Verarbeite {done_parts}/{total_parts} Teile")

            transcript = _join_transcript_parts(parts)
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
            self._clear_chunk_results(delete_audio=True)
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
                _unlink_audio(audio_path)
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
            _unlink_audio(audio_path)

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

    def _quality_session_active(self, session_id: int) -> bool:
        with self._lock:
            return session_id == self._session_id and self._quality_accept_session_id == session_id

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
        self._stop_chunk_worker(wait=False)
        self._stop_quality_worker(wait=False, close_backend=True)
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


def _join_transcript_parts(parts: list[str]) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()


def _copy_audio_file(audio_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix="redmic_quality_part_",
        suffix=".wav",
        dir=temp_dir(),
    )
    os.close(descriptor)
    shutil.copy2(audio_path, name)
    return Path(name)


def _combine_wav_files(paths: list[Path]) -> Path:
    if not paths:
        raise ValueError("No audio paths to combine.")

    descriptor, name = tempfile.mkstemp(
        prefix="redmic_quality_group_",
        suffix=".wav",
        dir=temp_dir(),
    )
    os.close(descriptor)

    output = Path(name)
    try:
        params = None
        frames = bytearray()
        for path in paths:
            with wave.open(str(path), "rb") as wav_file:
                current_params = wav_file.getparams()
                comparable = current_params[:3]
                if params is None:
                    params = comparable
                elif params != comparable:
                    raise ValueError("Cannot combine WAV files with different audio parameters.")
                frames.extend(wav_file.readframes(wav_file.getnframes()))

        channels, sample_width, frame_rate = params or (1, 2, 16000)
        with wave.open(str(output), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(frame_rate)
            wav_file.writeframes(bytes(frames))
        return output
    except Exception:
        _unlink_audio(output)
        raise


def _drain_quality_queue(work_queue: queue.Queue[_QualityWork]) -> None:
    while True:
        try:
            work = work_queue.get_nowait()
        except queue.Empty:
            break
        _unlink_audio(work.audio_path)
        work_queue.task_done()


def _unlink_audio(audio_path: Path) -> None:
    try:
        audio_path.unlink(missing_ok=True)
    except Exception:
        LOG.debug("Failed to remove temporary audio file: %s", audio_path, exc_info=True)
