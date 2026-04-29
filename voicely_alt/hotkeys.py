from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .config import AppConfig


LOG = logging.getLogger(__name__)


class HotkeyError(RuntimeError):
    pass


class KeyboardHotkeyManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self._keyboard = None
        self._start_handles: list[object] = []
        self._recording_handles: list[object] = []
        self._lock = threading.RLock()
        self._on_stop: Callable[[], None] | None = None
        self._on_cancel: Callable[[], None] | None = None

    def start(
        self,
        on_live_start: Callable[[], None],
        on_clipboard_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        try:
            import keyboard
        except Exception as exc:
            raise HotkeyError("The 'keyboard' package is required for global hotkeys.") from exc

        with self._lock:
            self._keyboard = keyboard
            self._on_stop = on_stop
            self._on_cancel = on_cancel
            self._start_handles = [
                keyboard.add_hotkey(
                    _normalize_hotkey(self.config.live_hotkey),
                    lambda: self._safe_call(on_live_start),
                    suppress=True,
                    trigger_on_release=False,
                ),
                keyboard.add_hotkey(
                    _normalize_hotkey(self.config.clipboard_hotkey),
                    lambda: self._safe_call(on_clipboard_start),
                    suppress=True,
                    trigger_on_release=False,
                ),
            ]
            LOG.info("Registered live hotkey: %s", self.config.live_hotkey)
            LOG.info("Registered clipboard hotkey: %s", self.config.clipboard_hotkey)

    def enable_recording_controls(self) -> None:
        with self._lock:
            if self._keyboard is None or self._recording_handles:
                return
            if self._on_stop is None or self._on_cancel is None:
                return

            self._recording_handles = [
                self._keyboard.add_hotkey(
                    _normalize_hotkey(self.config.stop_hotkey),
                    lambda: self._safe_call(self._on_stop),
                    suppress=True,
                    trigger_on_release=False,
                ),
                self._keyboard.add_hotkey(
                    _normalize_hotkey(self.config.cancel_hotkey),
                    lambda: self._safe_call(self._on_cancel),
                    suppress=True,
                    trigger_on_release=False,
                ),
            ]
            LOG.info(
                "Registered recording controls: stop=%s cancel=%s",
                self.config.stop_hotkey,
                self.config.cancel_hotkey,
            )

    def disable_recording_controls(self) -> None:
        with self._lock:
            if self._keyboard is None:
                return
            for handle in self._recording_handles:
                try:
                    self._keyboard.remove_hotkey(handle)
                except Exception:
                    LOG.debug("Failed to remove recording hotkey", exc_info=True)
            self._recording_handles = []

    def stop(self) -> None:
        with self._lock:
            self.disable_recording_controls()
            if self._keyboard is not None:
                for handle in self._start_handles:
                    try:
                        self._keyboard.remove_hotkey(handle)
                    except Exception:
                        LOG.debug("Failed to remove start hotkey", exc_info=True)
            self._start_handles = []

    def wait(self) -> None:
        if self._keyboard is None:
            raise HotkeyError("Hotkeys are not running.")
        self._keyboard.wait()

    def _safe_call(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            LOG.exception("Hotkey callback failed")


def _normalize_hotkey(hotkey: str) -> str:
    parts = []
    for part in hotkey.lower().replace(" ", "").split("+"):
        if part in {"win", "super", "meta"}:
            parts.append("windows")
        elif part == "cmd":
            parts.append("command")
        else:
            parts.append(part)
    return "+".join(parts)
