from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from math import ceil
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import AppConfig


LOG = logging.getLogger(__name__)
OPENAI_AUDIO_TRANSCRIPTION_USAGE_URL = "https://api.openai.com/v1/organization/usage/audio_transcriptions"


@dataclass(slots=True)
class OpenAITranscriptionUsage:
    seconds: float
    requests: int
    cost_eur: float

    def usage_label(self) -> str:
        seconds = f"{self.seconds:.1f}s" if self.seconds < 60 else f"{self.seconds / 60:.1f}min"
        requests = f"{self.requests} Req." if self.requests else ""
        return ", ".join(part for part in (seconds, requests) if part)


def query_openai_transcription_usage(
    config: AppConfig,
    start_epoch: float,
    end_epoch: float,
    model: str,
) -> OpenAITranscriptionUsage | None:
    admin_key = os.environ.get(config.openai_usage_admin_key_env, "").strip()
    if not admin_key:
        return None

    attempts = max(1, int(config.openai_usage_poll_attempts))
    delay = max(0.0, float(config.openai_usage_poll_delay_seconds))
    last_usage: OpenAITranscriptionUsage | None = None
    for attempt in range(attempts):
        if attempt > 0 and delay:
            time.sleep(delay)
        try:
            payload = _fetch_usage_payload(config, admin_key, start_epoch, end_epoch, model)
            usage = parse_audio_transcription_usage(payload, config, model)
        except Exception:
            LOG.debug("OpenAI usage lookup failed", exc_info=True)
            continue
        last_usage = usage
        if usage.seconds > 0 or usage.requests > 0:
            return usage
    return last_usage if last_usage and (last_usage.seconds > 0 or last_usage.requests > 0) else None


def parse_audio_transcription_usage(
    payload: dict[str, Any],
    config: AppConfig,
    model: str,
) -> OpenAITranscriptionUsage:
    expected_model = str(model).strip()
    seconds = 0.0
    requests = 0
    for bucket in payload.get("data", []):
        if not isinstance(bucket, dict):
            continue
        for result in _bucket_results(bucket):
            result_model = str(result.get("model", "")).strip()
            if expected_model and result_model and result_model != expected_model:
                continue
            seconds += _number(result.get("seconds", 0.0))
            requests += int(_number(result.get("num_model_requests", 0)))
    return OpenAITranscriptionUsage(
        seconds=seconds,
        requests=requests,
        cost_eur=estimate_transcription_cost_eur(config, seconds, model),
    )


def estimate_transcription_cost_eur(config: AppConfig, seconds: float, model: str) -> float:
    return max(0.0, float(seconds)) * transcription_rate_eur_per_minute(config, model) / 60.0


def transcription_rate_eur_per_minute(config: AppConfig, model: str) -> float:
    model_key = str(model).lower()
    if "mini" in model_key:
        return max(0.0, float(config.openai_realtime_mini_transcribe_eur_per_minute))
    return max(0.0, float(config.openai_realtime_transcribe_eur_per_minute))


def _fetch_usage_payload(
    config: AppConfig,
    admin_key: str,
    start_epoch: float,
    end_epoch: float,
    model: str,
) -> dict[str, Any]:
    start_time = max(0, int(start_epoch) - 60)
    end_time = max(start_time + 60, int(end_epoch) + 300)
    minutes = max(1, ceil((end_time - start_time) / 60))
    params: list[tuple[str, str]] = [
        ("start_time", str(start_time)),
        ("end_time", str(end_time)),
        ("bucket_width", "1m"),
        ("limit", str(min(1440, minutes + 2))),
        ("group_by", "model"),
        ("models", model),
    ]
    if config.openai_usage_project_id:
        params.append(("project_ids", config.openai_usage_project_id))
    if config.openai_usage_api_key_id:
        params.append(("api_key_ids", config.openai_usage_api_key_id))

    url = f"{OPENAI_AUDIO_TRANSCRIPTION_USAGE_URL}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {admin_key}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    with urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _bucket_results(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    value = bucket.get("results")
    if value is None:
        value = bucket.get("result")
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _number(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0
