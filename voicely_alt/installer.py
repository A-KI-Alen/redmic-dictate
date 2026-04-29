from __future__ import annotations

import json
import platform
import shutil
import urllib.request
import zipfile
from pathlib import Path

from .paths import models_dir, temp_dir, whispercpp_dir


RELEASE_API_URL = "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest"
MODEL_URLS = {
    "tiny": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
    "base": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
    "small": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
}


class SetupError(RuntimeError):
    pass


def model_path(model: str) -> Path:
    return models_dir() / f"ggml-{model}.bin"


def ensure_model(model: str) -> Path:
    if model not in MODEL_URLS:
        raise SetupError(f"Unknown local model '{model}'. Expected one of: {', '.join(MODEL_URLS)}")

    target = model_path(model)
    if target.exists() and target.stat().st_size > 0:
        return target

    download_file(MODEL_URLS[model], target)
    return target


def install_whispercpp(prefer_blas: bool = False) -> Path:
    existing = find_whisper_executable("server")
    if existing is not None:
        return existing

    if platform.system().lower() != "windows":
        raise SetupError(
            "Automatic whisper.cpp installation is currently implemented for Windows. "
            "Install whisper.cpp manually and place whisper-server on PATH or in ~/.voicely_alt/runtime."
        )

    release = _latest_release()
    asset = _select_windows_asset(release, prefer_blas=prefer_blas)
    if asset is None:
        raise SetupError("Could not find a Windows x64 whisper.cpp release asset.")

    archive = temp_dir() / asset["name"]
    download_file(asset["browser_download_url"], archive)

    target_dir = whispercpp_dir()
    with zipfile.ZipFile(archive, "r") as zip_file:
        zip_file.extractall(target_dir)

    executable = find_whisper_executable("server")
    if executable is None:
        raise SetupError("Downloaded whisper.cpp, but whisper-server.exe was not found.")
    return executable


def find_whisper_executable(kind: str) -> Path | None:
    names_by_kind = {
        "server": ["whisper-server.exe", "server.exe", "whisper-server"],
        "cli": ["whisper-cli.exe", "main.exe", "whisper-cli", "main"],
    }
    names = names_by_kind[kind]

    for root in [whispercpp_dir(), Path.cwd()]:
        for name in names:
            direct = root / name
            if direct.exists():
                return direct
        for candidate in root.rglob("*"):
            if candidate.is_file() and candidate.name in names:
                return candidate

    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)

    return None


def download_file(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".download")
    request = urllib.request.Request(url, headers={"User-Agent": "voicely-alt"})
    with urllib.request.urlopen(request, timeout=120) as response:
        with tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    tmp.replace(target)


def _latest_release() -> dict:
    request = urllib.request.Request(RELEASE_API_URL, headers={"User-Agent": "voicely-alt"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _select_windows_asset(release: dict, prefer_blas: bool) -> dict | None:
    assets = release.get("assets", [])
    names = [asset.get("name", "") for asset in assets]

    preferred_names = []
    if prefer_blas:
        preferred_names.append("whisper-blas-bin-x64.zip")
    preferred_names.append("whisper-bin-x64.zip")
    preferred_names.append("whisper-blas-bin-x64.zip")

    for preferred in preferred_names:
        for asset in assets:
            if asset.get("name") == preferred:
                return asset

    for asset, name in zip(assets, names, strict=False):
        lowered = name.lower()
        if "win" in lowered and "x64" in lowered and lowered.endswith(".zip"):
            return asset
    return None

