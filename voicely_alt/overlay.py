from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

from .config import AppConfig
from .paths import logs_dir


LOG = logging.getLogger(__name__)


class RecordingOverlay:
    def __init__(self, config: AppConfig):
        self.config = config
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()

    def show(self) -> None:
        if not self.config.recording_overlay:
            return
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

    def hide(self) -> None:
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
