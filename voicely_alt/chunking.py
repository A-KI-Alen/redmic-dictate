from __future__ import annotations

import logging
import os
import queue
import shutil
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import AppConfig
from .paths import temp_dir


LOG = logging.getLogger(__name__)


class Recorder(Protocol):
    def pop_chunk(self) -> Path | None: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class Tracker(Protocol):
    def record(self, event: str, session_id: int | None = None, **data: object) -> None: ...
    def transcript_fields(self, text: str, prefix: str = "transcript") -> dict[str, object]: ...


SessionActive = Callable[[int], bool]
ProgressCallback = Callable[[int, int], None]
TranscribeAudio = Callable[[Path], str]


@dataclass(slots=True)
class ChunkResult:
    index: int
    text: str = ""
    audio_path: Path | None = None


@dataclass(slots=True)
class QualityWork:
    start_index: int
    end_index: int
    audio_path: Path


@dataclass(slots=True)
class QualityResult:
    start_index: int
    end_index: int
    text: str = ""


class ChunkPipeline:
    """Runs fast background transcription and optional quality replacement.

    The pipeline protects the fast path: quality work is queued only after the
    matching fast chunks have completed, and it is skipped when the fast queue
    has any configured backlog.
    """

    def __init__(
        self,
        config: AppConfig,
        recorder: Recorder,
        fast_transcriber: Transcriber,
        session_active: SessionActive,
        quality_transcriber: Transcriber | None = None,
        tracker: Tracker | None = None,
    ):
        self.config = config
        self.recorder = recorder
        self.fast_transcriber = fast_transcriber
        self.quality_transcriber = quality_transcriber
        self.session_active = session_active
        self.tracker = tracker

        self._stop_event: threading.Event | None = None
        self._producer_thread: threading.Thread | None = None
        self._fast_thread: threading.Thread | None = None
        self._fast_queue: queue.Queue[ChunkResult] | None = None
        self._quality_queue: queue.Queue[QualityWork] | None = None
        self._quality_thread: threading.Thread | None = None

        self._lock = threading.RLock()
        self._fast_results: list[ChunkResult] = []
        self._quality_results: list[QualityResult] = []
        self._quality_pending_chunks: list[tuple[int, Path]] = []
        self._quality_accept_session_id: int | None = None
        self._active_session_id: int | None = None
        self._next_index = 0
        self._fast_in_progress = 0
        self._quality_in_progress = 0

    def start(self, session_id: int) -> None:
        self.stop_fast(wait=True)
        self.stop_quality(wait=False, close_backend=False)
        self._active_session_id = session_id
        self._stop_event = threading.Event()
        self._fast_queue = queue.Queue()
        self._producer_thread = threading.Thread(target=self._producer_loop, args=(session_id,), daemon=True)
        self._fast_thread = threading.Thread(target=self._fast_loop, args=(session_id,), daemon=True)

        if self.quality_transcriber is not None and self.config.quality_chunking:
            self._quality_queue = queue.Queue()
            self._quality_accept_session_id = session_id
            self._quality_thread = threading.Thread(target=self._quality_loop, args=(session_id,), daemon=True)

        self._producer_thread.start()
        self._fast_thread.start()
        if self._quality_thread is not None:
            self._quality_thread.start()
        self._track(
            "chunk_pipeline_started",
            session_id,
            background_chunk_seconds=self.config.background_chunk_seconds,
            quality_chunking=bool(self.quality_transcriber is not None and self.config.quality_chunking),
            quality_chunk_seconds=self.config.quality_chunk_seconds,
        )

    def request_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    def stop_fast(self, wait: bool) -> None:
        event = self._stop_event
        producer = self._producer_thread
        transcriber = self._fast_thread
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
            self._stop_event = None
            self._producer_thread = None
            self._fast_thread = None
            self._fast_queue = None

    def stop_quality(self, wait: bool, close_backend: bool) -> None:
        event = self._stop_event
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

    def fast_active(self) -> bool:
        producer = self._producer_thread
        transcriber = self._fast_thread
        return bool(
            (producer is not None and producer.is_alive())
            or (transcriber is not None and transcriber.is_alive())
            or (self._fast_queue is not None and not self._fast_queue.empty())
        )

    def quality_active(self) -> bool:
        thread = self._quality_thread
        return bool(
            (thread is not None and thread.is_alive())
            or (self._quality_queue is not None and not self._quality_queue.empty())
        )

    def fast_progress(self) -> tuple[int, int]:
        with self._lock:
            done = len(self._fast_results)
            in_progress = self._fast_in_progress
        queued = self._fast_queue.qsize() if self._fast_queue is not None else 0
        return done, done + in_progress + queued

    def quality_progress(self) -> tuple[int, int]:
        with self._lock:
            done = len(self._quality_results)
            in_progress = self._quality_in_progress
        queued = self._quality_queue.qsize() if self._quality_queue is not None else 0
        return done, done + in_progress + queued

    def reset(self) -> None:
        with self._lock:
            self._next_index = 0
            self._fast_results = []
            self._quality_results = []
            self._quality_pending_chunks = []
            self._fast_in_progress = 0
            self._quality_in_progress = 0

    def clear(self, delete_audio: bool) -> None:
        with self._lock:
            fast_results = self._fast_results
            pending_quality = self._quality_pending_chunks
            self._fast_results = []
            self._quality_results = []
            self._quality_pending_chunks = []
            self._active_session_id = None
            self._next_index = 0
            self._fast_in_progress = 0
            self._quality_in_progress = 0

        if not delete_audio:
            return
        for result in fast_results:
            if result.audio_path is not None:
                unlink_audio(result.audio_path)
        for _, path in pending_quality:
            unlink_audio(path)
        if self._quality_queue is not None:
            _drain_quality_queue(self._quality_queue)

    def fast_results(self) -> list[ChunkResult]:
        with self._lock:
            return sorted(self._fast_results, key=lambda result: result.index)

    def quality_results(self) -> list[QualityResult]:
        with self._lock:
            return sorted(self._quality_results, key=lambda result: result.start_index)

    def store_fast_result(self, result: ChunkResult) -> None:
        with self._lock:
            self._fast_results.append(result)

    def store_quality_result(self, result: QualityResult) -> None:
        if not result.text:
            return
        with self._lock:
            self._quality_results.append(result)

    def maybe_queue_quality_chunk(self, index: int, audio_path: Path) -> None:
        if self.quality_transcriber is None or not self.config.quality_chunking:
            return
        if self._quality_queue is None:
            return

        quality_copy: Path | None = None
        group: list[tuple[int, Path]] | None = None
        try:
            quality_copy = _copy_audio_file(audio_path)
            with self._lock:
                self._quality_pending_chunks.append((index, quality_copy))
                group_size = self._quality_group_size()
                if len(self._quality_pending_chunks) >= group_size:
                    group = self._quality_pending_chunks[:group_size]
                    del self._quality_pending_chunks[:group_size]

            if group is None:
                return

            fast_backlog = self._fast_queue_depth()
            if fast_backlog > int(self.config.quality_max_fast_backlog):
                LOG.info(
                    "Skipping quality chunks %s-%s because fast backlog is %s",
                    group[0][0],
                    group[-1][0],
                    fast_backlog,
                )
                self._track(
                    "quality_chunk_skipped",
                    self._active_session_id,
                    start_index=group[0][0],
                    end_index=group[-1][0],
                    reason="fast_backlog",
                    fast_backlog=fast_backlog,
                )
                for _, path in group:
                    unlink_audio(path)
                return

            group_audio = _combine_wav_files([path for _, path in group])
            for _, path in group:
                unlink_audio(path)
            fast_backlog = self._fast_queue_depth()
            if fast_backlog > int(self.config.quality_max_fast_backlog):
                LOG.info(
                    "Dropping quality chunks %s-%s because fast backlog rose to %s",
                    group[0][0],
                    group[-1][0],
                    fast_backlog,
                )
                self._track(
                    "quality_chunk_skipped",
                    self._active_session_id,
                    start_index=group[0][0],
                    end_index=group[-1][0],
                    reason="fast_backlog_after_combine",
                    fast_backlog=fast_backlog,
                )
                unlink_audio(group_audio)
                return
            self._track(
                "quality_chunk_queued",
                self._active_session_id,
                start_index=group[0][0],
                end_index=group[-1][0],
                audio_file=group_audio.name,
            )
            self._queue_quality_work(
                QualityWork(
                    start_index=group[0][0],
                    end_index=group[-1][0],
                    audio_path=group_audio,
                )
            )
        except Exception:
            LOG.exception("Could not queue quality chunk")
            self._track(
                "quality_chunk_error",
                self._active_session_id,
                error="Could not queue quality chunk",
            )
            if quality_copy is not None:
                unlink_audio(quality_copy)
            if group is not None:
                for _, path in group:
                    unlink_audio(path)

    def assemble_transcript(
        self,
        final_audio: Path | None,
        transcribe_audio: TranscribeAudio,
        progress: ProgressCallback,
    ) -> str:
        parts: list[str] = []
        results = self.fast_results()
        quality_results = self.quality_results()
        total_parts = len(results) + (1 if final_audio is not None else 0)
        done_parts = 0
        if total_parts:
            progress(0, total_parts)

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
                progress(done_parts, total_parts)
                continue

            result = base_by_index[index]
            if result.text:
                parts.append(result.text)
            elif result.audio_path is not None:
                progress(done_parts + 1, total_parts)
                parts.append(transcribe_audio(result.audio_path))
            done_parts += 1
            position += 1
            progress(done_parts, total_parts)

        if final_audio is not None:
            progress(done_parts + 1, total_parts)
            parts.append(transcribe_audio(final_audio))
            done_parts += 1
            progress(done_parts, total_parts)

        return join_transcript_parts(parts)

    def _producer_loop(self, session_id: int) -> None:
        event = self._stop_event
        if event is None:
            return

        interval = max(5, int(self.config.background_chunk_seconds))
        next_at = time.monotonic() + interval
        while True:
            if event.wait(max(0.0, next_at - time.monotonic())):
                return
            next_at += interval
            if not self.session_active(session_id):
                return
            audio_path: Path | None = None
            try:
                audio_path = self.recorder.pop_chunk()
                if audio_path is None:
                    continue
                index = self._next_chunk_index()
                LOG.info("Queued audio chunk %s for pre-transcription", index)
                self._track(
                    "chunk_queued",
                    session_id,
                    chunk_index=index,
                    audio_file=audio_path.name,
                )
                self._queue_fast_result(ChunkResult(index=index, audio_path=audio_path))
            except Exception:
                LOG.exception("Background chunk capture failed")
                self._track("chunk_capture_error", session_id, error="Background chunk capture failed")
                if audio_path is not None:
                    unlink_audio(audio_path)

    def _fast_loop(self, session_id: int) -> None:
        event = self._stop_event
        work_queue = self._fast_queue
        if event is None or work_queue is None:
            return

        while self.session_active(session_id):
            try:
                result = work_queue.get(timeout=0.1)
            except queue.Empty:
                if event.is_set():
                    return
                continue

            self._mark_fast_in_progress(1)
            try:
                if result.audio_path is None:
                    self.store_fast_result(result)
                    continue
                LOG.info("Pre-transcribing audio chunk %s", result.index)
                started_at = time.perf_counter()
                text = self.fast_transcriber.transcribe(result.audio_path).strip()
                if not self.session_active(session_id):
                    unlink_audio(result.audio_path)
                    return
                self.store_fast_result(ChunkResult(index=result.index, text=text))
                self._track(
                    "chunk_fast_completed",
                    session_id,
                    chunk_index=result.index,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                    **self._transcript_fields(text),
                )
                self.maybe_queue_quality_chunk(result.index, result.audio_path)
                unlink_audio(result.audio_path)
            except Exception:
                LOG.exception("Background chunk transcription failed")
                self._track(
                    "chunk_fast_error",
                    session_id,
                    chunk_index=result.index,
                    error="Background chunk transcription failed",
                )
                if self.session_active(session_id):
                    self.store_fast_result(result)
                elif result.audio_path is not None:
                    unlink_audio(result.audio_path)
            finally:
                self._mark_fast_in_progress(-1)
                work_queue.task_done()

    def _quality_loop(self, session_id: int) -> None:
        event = self._stop_event
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
                started_at = time.perf_counter()
                text = transcriber.transcribe(work.audio_path).strip()
                if not self._quality_session_active(session_id):
                    return
                self.store_quality_result(
                    QualityResult(
                        start_index=work.start_index,
                        end_index=work.end_index,
                        text=text,
                    )
                )
                self._track(
                    "quality_chunk_completed",
                    session_id,
                    start_index=work.start_index,
                    end_index=work.end_index,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                    **self._transcript_fields(text),
                )
            except Exception:
                LOG.exception("Quality chunk transcription failed")
                self._track(
                    "quality_chunk_error",
                    session_id,
                    start_index=work.start_index,
                    end_index=work.end_index,
                    error="Quality chunk transcription failed",
                )
            finally:
                self._mark_quality_in_progress(-1)
                unlink_audio(work.audio_path)
                work_queue.task_done()

    def _next_chunk_index(self) -> int:
        with self._lock:
            index = self._next_index
            self._next_index += 1
            return index

    def _queue_fast_result(self, result: ChunkResult) -> None:
        work_queue = self._fast_queue
        if work_queue is None:
            if result.audio_path is not None:
                unlink_audio(result.audio_path)
            return
        work_queue.put(result)

    def _queue_quality_work(self, work: QualityWork) -> None:
        work_queue = self._quality_queue
        if work_queue is None:
            unlink_audio(work.audio_path)
            return
        work_queue.put(work)

    def _mark_fast_in_progress(self, delta: int) -> None:
        with self._lock:
            self._fast_in_progress = max(0, self._fast_in_progress + delta)

    def _mark_quality_in_progress(self, delta: int) -> None:
        with self._lock:
            self._quality_in_progress = max(0, self._quality_in_progress + delta)

    def _quality_group_size(self) -> int:
        fast_seconds = max(1, int(self.config.background_chunk_seconds))
        quality_seconds = max(fast_seconds, int(self.config.quality_chunk_seconds))
        return max(1, (quality_seconds + fast_seconds - 1) // fast_seconds)

    def _fast_queue_depth(self) -> int:
        work_queue = self._fast_queue
        if work_queue is None:
            return 0
        return work_queue.qsize()

    def _quality_session_active(self, session_id: int) -> bool:
        with self._lock:
            return self.session_active(session_id) and self._quality_accept_session_id == session_id

    def _transcript_fields(self, text: str, prefix: str = "transcript") -> dict[str, object]:
        if self.tracker is None:
            stripped = text.strip()
            return {
                f"{prefix}_chars": len(stripped),
                f"{prefix}_words": len(stripped.split()) if stripped else 0,
            }
        return self.tracker.transcript_fields(text, prefix=prefix)

    def _track(self, event: str, session_id: int | None, **data: object) -> None:
        if self.tracker is None:
            return
        try:
            self.tracker.record(event, session_id, **data)
        except Exception:
            LOG.debug("Tracking failed for event %s", event, exc_info=True)


def join_transcript_parts(parts: list[str]) -> str:
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
        unlink_audio(output)
        raise


def _drain_quality_queue(work_queue: queue.Queue[QualityWork]) -> None:
    while True:
        try:
            work = work_queue.get_nowait()
        except queue.Empty:
            break
        unlink_audio(work.audio_path)
        work_queue.task_done()


def unlink_audio(audio_path: Path) -> None:
    try:
        audio_path.unlink(missing_ok=True)
    except Exception:
        LOG.debug("Failed to remove temporary audio file: %s", audio_path, exc_info=True)
