from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import AppConfig
from .paths import logs_dir, ollama_dir, ollama_models_dir
from .state import OutputMode


LOG = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


class OllamaServerManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._log_handle = None

    @property
    def base_url(self) -> str:
        return f"http://{self.config.cleanup_host}:{self.config.cleanup_port}"

    def ensure_running(self) -> None:
        if self.is_running():
            return

        executable = find_ollama_executable()
        if executable is None:
            raise OllamaError(
                "Ollama was not found. Run scripts\\setup_llm.ps1 or install Ollama."
            )

        self._start_process(executable)
        self._wait_until_ready()

    def is_running(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=1.0) as response:
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

    def pull_model(self, model: str) -> None:
        self.ensure_running()
        executable = find_ollama_executable()
        if executable is None:
            raise OllamaError("Ollama was not found.")
        env = _ollama_env(self.config)
        subprocess.run(
            [str(executable), "pull", model],
            check=True,
            env=env,
            creationflags=_creationflags(),
        )

    def _start_process(self, executable: Path) -> None:
        log_file = logs_dir() / "ollama-server.log"
        self._log_handle = log_file.open("ab")
        env = _ollama_env(self.config)

        LOG.info("Starting Ollama server: %s serve", executable)
        self.process = subprocess.Popen(
            [str(executable), "serve"],
            cwd=executable.parent,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=_creationflags(),
        )

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise OllamaError("Ollama exited during startup.")
            if self.is_running():
                return
            time.sleep(0.25)
        raise OllamaError("Timed out waiting for Ollama to start.")


class OllamaTranscriptCleaner:
    def __init__(self, config: AppConfig, server: OllamaServerManager | None = None):
        self.config = config
        self.server = server or OllamaServerManager(config)

    def will_process(self, mode: OutputMode, live_chunk: bool) -> bool:
        setting = str(self.config.transcript_cleanup).strip().lower()
        if setting in {"", "off", "false", "none"}:
            return False
        if self.config.cleanup_backend != "ollama":
            return False
        if live_chunk:
            return False
        if mode == OutputMode.CLIPBOARD:
            return setting in {"clipboard", "final", "all"}
        if mode == OutputMode.LIVE_PASTE:
            return setting in {"final", "all"}
        return False

    def process(self, text: str, mode: OutputMode, live_chunk: bool) -> str:
        if not self.will_process(mode, live_chunk):
            return text

        source = text.strip()
        if not source:
            return text

        try:
            self.server.ensure_running()
            cleaned = self._chat(source)
            if not cleaned:
                return text
            if len(cleaned) > max(len(source) * 3, len(source) + 250):
                LOG.warning("Ignoring LLM cleanup because response is unexpectedly long")
                return text
            return cleaned
        except Exception:
            LOG.warning("Local LLM cleanup failed; using raw transcript", exc_info=True)
            return text

    def close(self) -> None:
        self.server.stop()

    def _chat(self, text: str) -> str:
        payload = {
            "model": self.config.cleanup_model,
            "stream": False,
            "keep_alive": self.config.cleanup_keep_alive,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": text},
            ],
            "options": {
                "temperature": 0,
                "top_p": 0.2,
                "num_predict": max(80, min(800, len(text) // 2 + 120)),
            },
        }
        request = urllib.request.Request(
            self.server.base_url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "redmic-dictate",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(5, int(self.config.cleanup_timeout_seconds)),
            ) as response:
                parsed = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise OllamaError("Could not reach local Ollama server.") from exc

        message = parsed.get("message", {})
        if not isinstance(message, dict):
            raise OllamaError(f"Unexpected Ollama response shape: {parsed!r}")
        content = message.get("content", "")
        if not isinstance(content, str):
            raise OllamaError(f"Unexpected Ollama response content: {parsed!r}")
        return _clean_model_output(content)

    def _system_prompt(self) -> str:
        context = self.config.cleanup_context.strip()
        return (
            "Du bist eine vorsichtige Nachkorrektur fuer deutsche Sprachdiktate. "
            "Korrigiere nur offensichtliche Spracherkennungsfehler, Rechtschreibung, "
            "Gross-/Kleinschreibung und Zeichensetzung. Erhalte Bedeutung, Reihenfolge "
            "und Ich-Perspektive. Erfinde keine Namen, Fakten oder Zusatzinformationen. "
            "Antworte ausschliesslich mit dem korrigierten Text, ohne Anfuehrungszeichen "
            "und ohne Kommentar."
            + (f" Typische Begriffe im Diktat: {context}." if context else "")
        )


def find_ollama_executable() -> Path | None:
    local = ollama_dir() / "ollama.exe"
    if local.exists():
        return local

    found = shutil.which("ollama")
    if found:
        return Path(found)

    if os.name == "nt":
        found_exe = shutil.which("ollama.exe")
        if found_exe:
            return Path(found_exe)
    return None


def pull_ollama_model(config: AppConfig, model: str) -> None:
    OllamaServerManager(config).pull_model(model)


def _ollama_env(config: AppConfig) -> dict[str, str]:
    env = os.environ.copy()
    env["OLLAMA_HOST"] = f"{config.cleanup_host}:{config.cleanup_port}"
    env["OLLAMA_MODELS"] = str(ollama_models_dir())
    return env


def _creationflags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _clean_model_output(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) > 1:
        quoted = [_strip_wrapping_quotes(line) for line in lines]
        changed = [line for line in quoted if line and not _looks_like_label(line)]
        if changed:
            cleaned = changed[-1]
    for prefix in (
        "Korrigierter Text:",
        "Korrektur:",
        "Text:",
        "Antwort:",
        "Result:",
        "Output:",
    ):
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix) :].strip()
    return _strip_wrapping_quotes(cleaned).strip()


def _strip_wrapping_quotes(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1].strip()
    return stripped


def _looks_like_label(text: str) -> bool:
    lowered = text.lower().rstrip(":")
    return lowered in {"kontext", "roher text", "rohtext", "korrigierter text", "roter text"}
