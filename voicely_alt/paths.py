from __future__ import annotations

from pathlib import Path


APP_DIR_NAME = ".redmic_dictate"
LEGACY_APP_DIR_NAME = ".voicely_alt"


def app_dir() -> Path:
    path = Path.home() / APP_DIR_NAME
    legacy_path = Path.home() / LEGACY_APP_DIR_NAME
    if not path.exists() and legacy_path.exists():
        try:
            legacy_path.rename(path)
        except OSError:
            return legacy_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_dir() / "config.toml"


def runtime_dir() -> Path:
    path = app_dir() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def whispercpp_dir() -> Path:
    path = runtime_dir() / "whisper.cpp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = app_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir() -> Path:
    path = app_dir() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def llm_dir() -> Path:
    path = app_dir() / "llm"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ollama_dir() -> Path:
    path = llm_dir() / "ollama"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ollama_models_dir() -> Path:
    path = llm_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def benchmark_sample_path() -> Path:
    return app_dir() / "benchmark_sample.wav"
