from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import json

from .config import AppConfig
from .paths import logs_dir, overlay_status_path


LOG = logging.getLogger(__name__)


class RecordingOverlay:
    def __init__(self, config: AppConfig):
        self.config = config
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._mode = "hidden"
        self._message = ""
        self._audio_level = 0.0
        self._recording_started_at = 0.0
        self._recording_seconds = 0

    def show(self, mode: str = "recording", message: str = "") -> None:
        if not self.config.recording_overlay:
            return
        with self._lock:
            if mode == "hidden":
                self._recording_started_at = 0.0
                self._recording_seconds = 0
            elif mode == "recording" and self._mode != "recording":
                self._recording_started_at = time.time()
                self._recording_seconds = 0
            elif self._mode == "recording" and mode != "recording":
                self._recording_seconds = max(0, int(time.time() - self._recording_started_at))
                self._recording_started_at = 0.0
            self._mode = mode
            self._message = message
            self._write_status_locked()
            if self._process is not None and self._process.poll() is None:
                return

            args = [
                sys.executable,
                "-m",
                "voicely_alt.overlay_window",
            ]
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            log_path = logs_dir() / "overlay.log"
            log_handle = log_path.open("ab")
            try:
                self._process = subprocess.Popen(
                    args,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
                LOG.info("Started recording overlay process: %s", self._process.pid)
            except Exception:
                LOG.exception("Could not start recording overlay process")
            finally:
                log_handle.close()

    def update(self, mode: str, message: str = "") -> None:
        self.show(mode, message)

    def hide(self) -> None:
        with self._lock:
            self._mode = "hidden"
            self._message = ""
            self._audio_level = 0.0
            self._recording_started_at = 0.0
            self._recording_seconds = 0
            self._write_status_locked()
            self._stop_process()

    def stop(self) -> None:
        self.hide()

    def set_level(self, level: float) -> None:
        if not self.config.recording_overlay:
            return
        with self._lock:
            if self._mode == "hidden":
                return
            self._audio_level = max(0.0, min(1.0, float(level)))
            self._write_status_locked()

    def _stop_process(self) -> None:
        if self._process is None:
            return
        process = self._process
        self._process = None
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

    def _write_status_locked(self) -> None:
        try:
            status_path = overlay_status_path()
            payload = {
                "mode": self._mode,
                "message": self._message,
                "live_hotkey": self.config.live_hotkey,
                "clipboard_hotkey": self.config.clipboard_hotkey,
                "stop_hotkey": self.config.stop_hotkey,
                "cancel_hotkey": self.config.cancel_hotkey,
                "hard_abort_hotkey": self.config.hard_abort_hotkey,
                "audio_level": round(self._audio_level, 4),
                "recording_started_at": self._recording_started_at,
                "recording_seconds": max(0, int(time.time() - self._recording_started_at))
                if self._recording_started_at
                else self._recording_seconds,
                "updated_at": time.time(),
            }
            tmp_path = status_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(status_path)
        except Exception:
            LOG.debug("Could not write overlay status", exc_info=True)
