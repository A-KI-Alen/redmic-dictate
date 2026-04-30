from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import config_path


@dataclass(slots=True)
class AppConfig:
    start_hotkey: str = "alt+y"
    live_hotkey: str = "alt+y"
    clipboard_hotkey: str = "alt+shift+y"
    stop_hotkey: str = "space"
    cancel_hotkey: str = "esc"
    hard_abort_hotkey: str = "space+esc"
    hard_abort_window_ms: int = 250
    start_debounce_ms: int = 1200
    backend: str = "openai_realtime"
    language: str = "de"
    transcription_prompt: str = (
        "Dies ist ein deutsches Diktat. Transkribiere ausschliesslich auf Deutsch. "
        "Schreibe keine englischen Woerter, ausser sie wurden klar gesprochen. "
        "Fachbegriffe: RedMic Dictate, Windows, Alt, Shift, Zwischenablage, "
        "Transkription, Mikrofon, Codex, OpenAI."
    )
    whisper_no_fallback: bool = True
    whisper_suppress_non_speech: bool = True
    whisper_server_max_age_seconds: int = 14400
    model: str = "auto"
    selected_model: str = ""
    threads: str = "auto"
    paste_method: str = "clipboard"
    keep_transcript_clipboard: bool = True
    cloud_fallback: str = "local_whispercpp"
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime"
    openai_realtime_session_model: str = "gpt-realtime"
    openai_realtime_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_realtime_fallback_model: str = "gpt-4o-transcribe"
    openai_realtime_audio_rate: int = 24000
    openai_realtime_commit_seconds: float = 3.0
    openai_realtime_finish_timeout_seconds: float = 7.0
    openai_realtime_connect_timeout_seconds: float = 6.0
    openai_realtime_send_interval_ms: int = 120
    openai_realtime_noise_reduction: str = "near_field"
    openai_realtime_mini_transcribe_eur_per_minute: float = 0.0028
    openai_realtime_transcribe_eur_per_minute: float = 0.0056
    openai_usage_admin_key_env: str = "OPENAI_ADMIN_KEY"
    openai_usage_project_id: str = ""
    openai_usage_api_key_id: str = ""
    openai_usage_poll_delay_seconds: float = 20.0
    openai_usage_poll_attempts: int = 3
    openai_cost_usd_to_eur_rate: float = 0.93
    host: str = "127.0.0.1"
    port: int = 18080
    sample_rate: int = 16000
    max_recording_seconds: int = 300
    silence_rms_threshold: int = 60
    live_streaming: bool = False
    live_chunk_seconds: int = 4
    progressive_live_paste: bool = True
    background_chunking: bool = True
    background_chunk_seconds: int = 5
    quality_chunking: bool = True
    quality_model: str = "small"
    quality_threads: str = "6"
    quality_chunk_seconds: int = 10
    quality_max_fast_backlog: int = 1
    quality_wait_after_stop_seconds: float = 7.0
    quality_guard_enabled: bool = True
    quality_guard_min_recording_seconds: int = 20
    quality_guard_min_coverage: float = 0.50
    quality_guard_min_text_ratio: float = 0.40
    paste_restore_delay_ms: int = 300
    beep_feedback: bool = False
    tray_notifications: bool = True
    recording_overlay: bool = True
    overlay_size: int = 72
    taskbar_recording_overlay: bool = True
    taskbar_overlay_height: int = 22
    taskbar_overlay_alpha: float = 0.90
    transcript_cleanup: str = "clipboard"
    cleanup_backend: str = "ollama"
    cleanup_model: str = "llama3.2:3b"
    cleanup_host: str = "127.0.0.1"
    cleanup_port: int = 11434
    cleanup_timeout_seconds: int = 180
    cleanup_keep_alive: str = "30m"
    cleanup_context: str = (
        "RedMic Dictate, Windows, Alt, Shift, Y, Taskleiste, rote Leiste, "
        "Zwischenablage, Transkription, Mikrofon, Mauszeiger, Hotkey, Codex, OpenAI"
    )
    tracking_enabled: bool = True
    tracking_retention_days: int = 14
    tracking_include_transcript_text: bool = False
    tracking_transcript_preview_chars: int = 0

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        target = path or config_path()
        if not target.exists():
            return cls()

        raw = tomllib.loads(target.read_text(encoding="utf-8-sig"))

        allowed = cls.__dataclass_fields__.keys()
        values = {key: raw[key] for key in raw.keys() if key in allowed}
        return cls(**values)

    @classmethod
    def load_or_create(cls, path: Path | None = None) -> "AppConfig":
        target = path or config_path()
        existed = target.exists()
        config = cls.load(target)
        if not existed or _missing_config_keys(target, cls.__dataclass_fields__.keys()):
            config.save(target)
        return config

    def save(self, path: Path | None = None) -> None:
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_toml(), encoding="utf-8")

    def to_toml(self) -> str:
        lines = []
        for key, value in asdict(self).items():
            lines.append(f"{key} = {_toml_value(value)}")
        return "\n".join(lines) + "\n"

    def resolved_threads(self) -> int:
        if isinstance(self.threads, int):
            return max(1, self.threads)
        if str(self.threads).lower() != "auto":
            try:
                return max(1, int(self.threads))
            except ValueError:
                return 4

        cpu_count = os.cpu_count() or 4
        return max(1, min(6, cpu_count - 2 if cpu_count > 3 else cpu_count))

    def resolved_model(self) -> str:
        if self.model != "auto":
            return self.model
        return self.selected_model or "base"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(str(value))


def _missing_config_keys(path: Path, expected: Any) -> bool:
    if not path.exists():
        return True
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        return any(key not in raw for key in expected)
    except Exception:
        return False
