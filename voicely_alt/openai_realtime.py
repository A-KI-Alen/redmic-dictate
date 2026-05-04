from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .audio import resample_pcm16_mono
from .config import AppConfig


LOG = logging.getLogger(__name__)


class RealtimeUnavailableError(RuntimeError):
    pass


class AudioStreamSource(Protocol):
    def read_stream_chunk(self) -> bytes: ...
    def actual_sample_rate(self) -> int: ...


@dataclass(slots=True)
class RealtimeTranscriptResult:
    transcript: str
    delivered_text: str = ""
    error: str = ""

    @property
    def delivered_any(self) -> bool:
        return bool(self.delivered_text.strip())

    @property
    def delivered_chars(self) -> int:
        return len(self.delivered_text.strip())


RealtimeTextCallback = Callable[[str], bool | None]
RealtimeProgressCallback = Callable[[int, int], None]


class OpenAIRealtimeTranscriptionSession:
    def __init__(
        self,
        config: AppConfig,
        audio_source: AudioStreamSource,
        on_text: RealtimeTextCallback | None = None,
        on_progress: RealtimeProgressCallback | None = None,
    ):
        self.config = config
        self.audio_source = audio_source
        self.on_text = on_text
        self.on_progress = on_progress
        self._ws = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._configured_event = threading.Event()
        self._lock = threading.RLock()
        self._sender_thread: threading.Thread | None = None
        self._receiver_thread: threading.Thread | None = None
        self._uncommitted_bytes = 0
        self._commits_sent = 0
        self._commit_order: list[str] = []
        self._completed: dict[str, str] = {}
        self._completed_empty: set[str] = set()
        self._completed_unknown: list[str] = []
        self._delivered: set[str] = set()
        self._delivered_texts: list[str] = []
        self._last_commit_at = 0.0
        self._error = ""

    def start(self) -> None:
        api_key = os.environ.get(self.config.openai_api_key_env, "").strip()
        if not api_key:
            raise RealtimeUnavailableError(
                f"{self.config.openai_api_key_env} is not set; using local fallback."
            )

        try:
            import websocket
        except Exception as exc:
            raise RealtimeUnavailableError(
                "websocket-client is not installed; using local fallback."
            ) from exc

        url = _realtime_url(self.config)
        timeout = max(1.0, float(self.config.openai_realtime_connect_timeout_seconds))
        try:
            self._ws = websocket.create_connection(
                url,
                header=[f"Authorization: Bearer {api_key}"],
                timeout=timeout,
            )
            self._ws.settimeout(0.5)
        except Exception as exc:
            self.close()
            raise RealtimeUnavailableError("Could not connect to OpenAI Realtime API.") from exc

        self._stop_event.clear()
        self._ready_event.clear()
        self._configured_event.clear()
        self._receiver_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._receiver_thread.start()
        try:
            self._send(_session_update_payload(self.config))
        except Exception as exc:
            self.close()
            raise RealtimeUnavailableError("Could not configure OpenAI Realtime transcription.") from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._configured_event.wait(timeout=0.05):
                break
            reason = self.failed_reason()
            if reason:
                self.close()
                raise RealtimeUnavailableError(reason)
        else:
            self.close()
            raise RealtimeUnavailableError("OpenAI Realtime transcription session was not accepted.")

        self._sender_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._sender_thread.start()

    def stop(self) -> RealtimeTranscriptResult:
        self._stop_event.set()
        sender = self._sender_thread
        if sender is not None and sender.is_alive():
            sender.join(timeout=2.5)

        deadline = time.monotonic() + max(0.5, float(self.config.openai_realtime_finish_timeout_seconds))
        while time.monotonic() < deadline:
            with self._lock:
                completed, expected = self._progress_counts_locked()
                pending = len(self._commit_order) < self._commits_sent or completed < len(self._commit_order)
                error = self._error
            self._notify_progress(completed, expected)
            if error or not pending:
                break
            time.sleep(0.1)

        self.close()
        return self.result()

    def cancel(self) -> None:
        self._stop_event.set()
        self.close()

    def failed_reason(self) -> str:
        with self._lock:
            return self._error

    def result(self) -> RealtimeTranscriptResult:
        with self._lock:
            transcript = self._assembled_transcript_locked()
            return RealtimeTranscriptResult(
                transcript=transcript,
                delivered_text=" ".join(self._delivered_texts).strip(),
                error=self._error,
            )

    def close(self) -> None:
        self._stop_event.set()
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                LOG.debug("Could not close OpenAI Realtime socket", exc_info=True)
        receiver = self._receiver_thread
        if receiver is not None and receiver.is_alive() and receiver is not threading.current_thread():
            receiver.join(timeout=1.0)
        self._receiver_thread = None
        self._sender_thread = None

    def _send_loop(self) -> None:
        interval = max(0.04, int(self.config.openai_realtime_send_interval_ms) / 1000)
        commit_seconds = max(0.6, float(self.config.openai_realtime_commit_seconds))
        self._last_commit_at = time.monotonic()
        while not self._stop_event.wait(interval):
            if not self._send_new_audio():
                continue
            if time.monotonic() - self._last_commit_at >= commit_seconds:
                self._commit_if_needed()

        self._send_new_audio()
        self._commit_if_needed(force=True)

    def _receive_loop(self) -> None:
        while True:
            ws = self._ws
            if ws is None:
                return
            try:
                raw = ws.recv()
            except Exception as exc:
                if _is_socket_timeout(exc):
                    continue
                if self._ws is not None:
                    self._set_error(f"OpenAI Realtime connection failed: {exc}")
                return
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                LOG.debug("Ignoring non-JSON Realtime event: %r", raw)
                continue
            self._handle_event(event)

    def _send_new_audio(self) -> bool:
        data = self.audio_source.read_stream_chunk()
        if not data:
            return False
        source_rate = self.audio_source.actual_sample_rate()
        target_rate = int(self.config.openai_realtime_audio_rate)
        converted = resample_pcm16_mono(data, source_rate, target_rate)
        if not converted:
            return False
        self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(converted).decode("ascii"),
            }
        )
        with self._lock:
            self._uncommitted_bytes += len(converted)
        return True

    def _commit_if_needed(self, force: bool = False) -> None:
        target_rate = int(self.config.openai_realtime_audio_rate)
        min_audio_bytes = int(target_rate * 2 * 0.35)
        with self._lock:
            if self._uncommitted_bytes <= 0:
                return
            if self._uncommitted_bytes < min_audio_bytes and not force:
                return
            self._uncommitted_bytes = 0
            self._commits_sent += 1
            self._last_commit_at = time.monotonic()
        try:
            self._send({"type": "input_audio_buffer.commit", "event_id": _event_id("commit")})
        except Exception as exc:
            with self._lock:
                self._commits_sent = max(0, self._commits_sent - 1)
            self._set_error(f"Could not commit OpenAI Realtime audio buffer: {exc}")
            if force:
                return
            LOG.debug("Could not commit OpenAI Realtime audio buffer", exc_info=True)

    def _send(self, payload: dict[str, object]) -> None:
        ws = self._ws
        if ws is None:
            raise RealtimeUnavailableError("OpenAI Realtime socket is closed.")
        ws.send(json.dumps(payload, ensure_ascii=False))

    def _handle_event(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        if event_type in {"session.created", "transcription_session.created"}:
            self._ready_event.set()
            return
        if event_type in {"session.updated", "transcription_session.updated"}:
            self._ready_event.set()
            self._configured_event.set()
            return
        if event_type == "input_audio_buffer.committed":
            item_id = str(event.get("item_id", "")).strip()
            if item_id:
                with self._lock:
                    if item_id not in self._commit_order:
                        self._commit_order.append(item_id)
                    completed, expected = self._progress_counts_locked()
                self._notify_progress(completed, expected)
                self._deliver_ready_texts()
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            item_id = str(event.get("item_id", "")).strip()
            transcript = str(event.get("transcript", "")).strip()
            with self._lock:
                if transcript:
                    if item_id:
                        self._completed[item_id] = transcript
                    else:
                        self._completed_unknown.append(transcript)
                elif item_id:
                    self._completed_empty.add(item_id)
                completed, expected = self._progress_counts_locked()
            self._notify_progress(completed, expected)
            if transcript:
                self._deliver_ready_texts()
            return
        if event_type == "conversation.item.input_audio_transcription.failed":
            error = event.get("error")
            self._set_error(_error_message(error, "OpenAI transcription failed."))
            return
        if event_type == "error":
            error = event.get("error")
            self._set_error(_error_message(error, "OpenAI Realtime error."))

    def _deliver_ready_texts(self) -> None:
        callbacks: list[str] = []
        with self._lock:
            for item_id in self._commit_order:
                if item_id in self._delivered:
                    continue
                text = self._completed.get(item_id, "").strip()
                if not text:
                    break
                self._delivered.add(item_id)
                callbacks.append(text)
            if self._completed_unknown:
                callbacks.extend(self._completed_unknown)
                self._completed_unknown = []

        if self.on_text is None:
            return
        for text in callbacks:
            try:
                accepted = self.on_text(text)
                if accepted is not False:
                    with self._lock:
                        self._delivered_texts.append(text)
            except Exception:
                LOG.debug("Realtime text callback failed", exc_info=True)

    def _assembled_transcript_locked(self) -> str:
        parts = [
            self._completed[item_id].strip()
            for item_id in self._commit_order
            if self._completed.get(item_id, "").strip()
        ]
        parts.extend(text.strip() for text in self._completed_unknown if text.strip())
        return " ".join(parts).strip()

    def _set_error(self, message: str) -> None:
        with self._lock:
            if not self._error:
                self._error = message
        LOG.warning(message)

    def _progress_counts_locked(self) -> tuple[int, int]:
        completed = 0
        for item_id in self._commit_order:
            if item_id in self._completed or item_id in self._completed_empty:
                completed += 1
        completed += len(self._completed_unknown)
        return completed, self._commits_sent

    def _notify_progress(self, completed: int, expected: int) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(completed, expected)
        except Exception:
            LOG.debug("Realtime progress callback failed", exc_info=True)


def is_openai_realtime_enabled(config: AppConfig) -> bool:
    return str(config.backend).lower() in {"openai_realtime", "hybrid_openai_realtime"}


def _realtime_url(config: AppConfig) -> str:
    return _append_query_params(
        config.openai_realtime_url,
        {"intent": "transcription"},
        remove={"model"},
    )


def _append_query_params(url: str, params: dict[str, str], remove: set[str] | None = None) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key in remove or set():
        query.pop(key, None)
    for key, value in params.items():
        query.setdefault(key, value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _session_update_payload(config: AppConfig) -> dict[str, object]:
    noise_reduction: object
    reduction = str(config.openai_realtime_noise_reduction).strip()
    noise_reduction = {"type": reduction} if reduction and reduction.lower() != "off" else None
    transcription: dict[str, object] = {
        "model": config.openai_realtime_transcription_model,
        "language": config.language,
    }
    prompt = str(config.openai_realtime_prompt).strip()
    if prompt:
        transcription["prompt"] = prompt
    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": int(config.openai_realtime_audio_rate),
                    },
                    "noise_reduction": noise_reduction,
                    "transcription": transcription,
                    "turn_detection": None,
                }
            },
        },
    }


def _event_id(prefix: str) -> str:
    return f"redmic_{prefix}_{uuid.uuid4().hex[:16]}"


def _error_message(error: object, fallback: str) -> str:
    if isinstance(error, dict):
        message = str(error.get("message", "")).strip()
        code = str(error.get("code", "")).strip()
        if message and code:
            return f"{message} ({code})"
        if message:
            return message
    return fallback


def _is_socket_timeout(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    return "timeout" in name or isinstance(exc, TimeoutError)
