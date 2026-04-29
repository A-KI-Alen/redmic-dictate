from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .audio import is_silent_wav
from .config import AppConfig
from .installer import ensure_model, find_whisper_executable, model_path
from .paths import logs_dir


LOG = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    pass


class WhisperCppServerManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._log_handle = None

    @property
    def base_url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    def ensure_running(self) -> None:
        if self.is_running():
            return

        executable = find_whisper_executable("server")
        if executable is None:
            raise TranscriptionError(
                "whisper-server was not found. Run: python -m voicely_alt setup --model base"
            )

        model = self.config.resolved_model()
        model_file = model_path(model)
        if not model_file.exists():
            raise TranscriptionError(
                f"Local model '{model}' is missing. Run: python -m voicely_alt setup --model {model}"
            )

        self._start_process(executable, model_file)
        self._wait_until_ready()

    def is_running(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/", timeout=0.5) as response:
                return 200 <= response.status < 500
        except Exception:
            return False

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _start_process(self, executable: Path, model_file: Path) -> None:
        log_file = logs_dir() / "whisper-server.log"
        self._log_handle = log_file.open("ab")

        args = [
            str(executable),
            "-m",
            str(model_file),
            "-l",
            self.config.language,
            "-t",
            str(self.config.resolved_threads()),
            "--host",
            self.config.host,
            "--port",
            str(self.config.port),
            "-nt",
        ]

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        LOG.info("Starting whisper.cpp server: %s", " ".join(args))
        self.process = subprocess.Popen(
            args,
            cwd=executable.parent,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise TranscriptionError("whisper-server exited during startup.")
            if self.is_running():
                return
            time.sleep(0.25)
        raise TranscriptionError("Timed out waiting for whisper-server to start.")


class WhisperCppTranscriber:
    def __init__(self, config: AppConfig, server: WhisperCppServerManager | None = None):
        self.config = config
        self.server = server or WhisperCppServerManager(config)

    def transcribe(self, audio_path: Path) -> str:
        if is_silent_wav(audio_path, self.config.silence_rms_threshold):
            LOG.info("Skipping transcription because audio is below silence threshold")
            return ""

        self.server.ensure_running()
        response = _post_multipart(
            self.server.base_url + "/inference",
            fields={
                "temperature": "0.0",
                "response_format": "json",
            },
            files={"file": audio_path},
        )
        return _extract_text(response)

    def close(self) -> None:
        self.server.stop()


def prepare_local_runtime(config: AppConfig, model: str) -> None:
    del config
    ensure_model(model)


def _post_multipart(url: str, fields: dict[str, str], files: dict[str, Path]) -> bytes:
    boundary = "----voicely-alt-" + uuid.uuid4().hex
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for name, path in files.items():
        data = path.read_bytes()
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{path.name}"\r\n'
            ).encode("utf-8")
        )
        body.extend(b"Content-Type: audio/wav\r\n\r\n")
        body.extend(data)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "voicely-alt",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TranscriptionError(f"whisper.cpp returned HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise TranscriptionError("Could not reach local whisper.cpp server.") from exc


def _extract_text(response: bytes) -> str:
    text = response.decode("utf-8", errors="replace").strip()
    if not text:
        return ""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    for key in ("text", "transcript", "transcription"):
        value = parsed.get(key)
        if isinstance(value, str):
            return value.strip()

    raise TranscriptionError(f"Unexpected whisper.cpp response shape: {parsed!r}")
