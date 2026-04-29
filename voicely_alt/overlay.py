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

    def show(self, mode: str = "recording", message: str = "") -> None:
        if not self.config.recording_overlay:
            return
        self._write_status(mode, message)
        with self._lock:
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
        self._write_status("hidden", "")
        with self._lock:
            self._stop_process()

    def stop(self) -> None:
        self.hide()

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

    def _write_status(self, mode: str, message: str) -> None:
        try:
            payload = {
                "mode": mode,
                "message": message,
                "live_hotkey": self.config.live_hotkey,
                "clipboard_hotkey": self.config.clipboard_hotkey,
                "stop_hotkey": self.config.stop_hotkey,
                "cancel_hotkey": self.config.cancel_hotkey,
                "updated_at": time.time(),
            }
            overlay_status_path().write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            LOG.debug("Could not write overlay status", exc_info=True)
