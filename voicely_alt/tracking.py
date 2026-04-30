from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import AppConfig
from .paths import logs_dir


LOG = logging.getLogger(__name__)


class EventTracker:
    """Local-only JSONL event tracker for diagnosing dictation runs."""

    def __init__(self, config: AppConfig, root: Path | None = None):
        self.config = config
        self.root = root or logs_dir()
        self._lock = threading.RLock()
        self._cleanup_done = False

    def record(self, event: str, session_id: int | None = None, **data: Any) -> None:
        if not self.config.tracking_enabled:
            return

        try:
            self._cleanup_old_logs_once()
            payload: dict[str, Any] = {
                "ts": _now().isoformat(timespec="milliseconds"),
                "event": event,
            }
            if session_id is not None:
                payload["session_id"] = session_id
            payload.update(_json_safe(data))

            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                path = self._events_path(_now().date())
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
        except Exception:
            LOG.debug("Could not write tracking event", exc_info=True)

    def transcript_fields(self, text: str, prefix: str = "transcript") -> dict[str, Any]:
        stripped = text.strip()
        fields: dict[str, Any] = {
            f"{prefix}_chars": len(stripped),
            f"{prefix}_words": len(stripped.split()) if stripped else 0,
            f"{prefix}_sha256_12": hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:12]
            if stripped
            else "",
        }
        if self.config.tracking_include_transcript_text:
            fields[f"{prefix}_text"] = stripped
        elif self.config.tracking_transcript_preview_chars > 0:
            limit = max(0, int(self.config.tracking_transcript_preview_chars))
            fields[f"{prefix}_preview"] = stripped[:limit]
        return fields

    def _events_path(self, day: date) -> Path:
        return self.root / f"events-{day.isoformat()}.jsonl"

    def _cleanup_old_logs_once(self) -> None:
        with self._lock:
            if self._cleanup_done:
                return
            self._cleanup_done = True

        retention = max(1, int(self.config.tracking_retention_days))
        cutoff = _now().date() - timedelta(days=retention)
        for path in self.root.glob("events-*.jsonl"):
            day = _parse_events_day(path)
            if day is not None and day < cutoff:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    LOG.debug("Could not remove old tracking file: %s", path, exc_info=True)


class NullTracker:
    def record(self, event: str, session_id: int | None = None, **data: Any) -> None:
        del event, session_id, data

    def transcript_fields(self, text: str, prefix: str = "transcript") -> dict[str, Any]:
        stripped = text.strip()
        return {
            f"{prefix}_chars": len(stripped),
            f"{prefix}_words": len(stripped.split()) if stripped else 0,
        }


def load_events(hours: int = 24, root: Path | None = None) -> list[dict[str, Any]]:
    end = _now()
    start = end - timedelta(hours=max(1, int(hours)))
    return load_events_between(start, end, root=root)


def load_events_between(
    start: datetime,
    end: datetime,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    target = root or logs_dir()
    events: list[dict[str, Any]] = []
    for day in _days_between(start.date(), end.date()):
        path = target / f"events-{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                    timestamp = datetime.fromisoformat(str(event.get("ts", "")))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=start.tzinfo)
                    if not (start <= timestamp <= end):
                        continue
                except Exception:
                    continue
                events.append(event)
    return sorted(events, key=lambda item: str(item.get("ts", "")))


def build_diagnostics_report(events: Iterable[dict[str, Any]], hours: int = 24) -> str:
    items = list(events)
    by_event = Counter(str(item.get("event", "")) for item in items)
    sessions = {item.get("session_id") for item in items if item.get("session_id") is not None}
    errors = [
        item
        for item in items
        if "error" in str(item.get("event", "")) or item.get("level") == "error"
    ]
    transcriptions = [
        item for item in items if item.get("event") in {"audio_transcribed", "transcription_failed"}
    ]
    slow = [
        item
        for item in transcriptions
        if int(float(item.get("duration_ms", 0) or 0)) >= 5000
    ]
    outputs = [item for item in items if item.get("event") == "output_written"]
    total_chars = sum(int(item.get("transcript_chars", 0) or 0) for item in outputs)

    lines = [
        f"# RedMic diagnostics, last {hours}h",
        "",
        f"- events: {len(items)}",
        f"- sessions: {len(sessions)}",
        f"- started: {by_event['session_started']}",
        f"- finished: {by_event['session_finished']}",
        f"- cancelled: {by_event['session_cancelled']}",
        f"- hard aborts: {by_event['session_hard_aborted']}",
        f"- errors: {len(errors)}",
        f"- output chars: {total_chars}",
        f"- chunks queued: {by_event['chunk_queued']}",
        f"- fast chunks done: {by_event['chunk_fast_completed']}",
        f"- quality chunks queued: {by_event['quality_chunk_queued']}",
        f"- quality chunks done: {by_event['quality_chunk_completed']}",
        f"- quality chunks skipped: {by_event['quality_chunk_skipped']}",
        f"- slow transcriptions >=5s: {len(slow)}",
    ]

    if errors:
        lines.extend(["", "## Problems"])
        for item in errors[-10:]:
            lines.append(_event_line(item))

    if slow:
        lines.extend(["", "## Slow Transcriptions"])
        for item in sorted(slow, key=lambda value: int(float(value.get("duration_ms", 0) or 0)), reverse=True)[:10]:
            lines.append(_event_line(item))

    if items:
        lines.extend(["", "## Recent Events"])
        for item in items[-15:]:
            lines.append(_event_line(item))

    return "\n".join(lines) + "\n"


def write_diagnostics_report(config: AppConfig, hours: int = 24) -> Path:
    del config
    target = logs_dir() / f"diagnostics-last-{hours}h.md"
    report = build_diagnostics_report(load_events(hours), hours=hours)
    target.write_text(report, encoding="utf-8")
    return target


def _event_line(item: dict[str, Any]) -> str:
    event = item.get("event", "")
    session_id = item.get("session_id", "-")
    timestamp = item.get("ts", "")
    details = []
    for key in (
        "state",
        "message",
        "mode",
        "outcome",
        "duration_ms",
        "chunks_done",
        "chunks_total",
        "transcript_chars",
        "error",
    ):
        if key in item:
            details.append(f"{key}={item[key]}")
    suffix = ", ".join(details)
    return f"- {timestamp} session={session_id} event={event}" + (f" ({suffix})" if suffix else "")


def _days_between(start: date, end: date) -> Iterable[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _parse_events_day(path: Path) -> date | None:
    try:
        name = path.stem
        if not name.startswith("events-"):
            return None
        return date.fromisoformat(name.removeprefix("events-"))
    except ValueError:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _now() -> datetime:
    return datetime.now().astimezone()
