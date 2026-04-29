from __future__ import annotations

import logging
import threading
import time
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
        self._hard_abort_handle: object | None = None
        self._lock = threading.RLock()
        self._on_stop: Callable[[], None] | None = None
        self._on_cancel: Callable[[], None] | None = None
        self._on_hard_abort: Callable[[], None] | None = None
        self._disable_generation = 0
        self._disable_pending = False
        self._stop_pending = False

    def start(
        self,
        on_live_start: Callable[[], None],
        on_clipboard_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None],
        on_hard_abort: Callable[[], None],
    ) -> None:
        try:
            import keyboard
        except Exception as exc:
            raise HotkeyError("The 'keyboard' package is required for global hotkeys.") from exc

        with self._lock:
            self._keyboard = keyboard
            self._on_stop = on_stop
            self._on_cancel = on_cancel
            self._on_hard_abort = on_hard_abort
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
            self._hard_abort_handle = keyboard.add_hotkey(
                _normalize_hotkey(self.config.hard_abort_hotkey),
                lambda: self._safe_call(on_hard_abort),
                suppress=True,
                trigger_on_release=False,
            )
            LOG.info("Registered live hotkey: %s", self.config.live_hotkey)
            LOG.info("Registered clipboard hotkey: %s", self.config.clipboard_hotkey)
            LOG.info("Registered hard abort hotkey: %s", self.config.hard_abort_hotkey)

    def enable_recording_controls(self) -> None:
        with self._lock:
            if self._keyboard is None or self._recording_handles:
                return
            if self._on_stop is None or self._on_cancel is None:
                return

            self._recording_handles = [
                self._add_suppressed_recording_key(
                    _normalize_hotkey(self.config.stop_hotkey),
                    self._handle_stop_key_down,
                ),
                self._add_suppressed_recording_key(
                    _normalize_hotkey(self.config.cancel_hotkey),
                    self._handle_cancel_key_down,
                ),
            ]
            LOG.info(
                "Registered recording controls: stop=%s cancel=%s",
                self.config.stop_hotkey,
                self.config.cancel_hotkey,
            )

    def disable_recording_controls(self, force: bool = False) -> None:
        with self._lock:
            if self._keyboard is None:
                return
            if not force and self._recording_handles:
                if self._disable_pending:
                    return
                self._disable_pending = True
                self._disable_generation += 1
                generation = self._disable_generation
                threading.Thread(
                    target=self._disable_after_key_release,
                    args=(generation,),
                    daemon=True,
                ).start()
                return
            self._remove_recording_controls_locked()

    def _remove_recording_controls_locked(self) -> None:
        if self._keyboard is None:
            return
        self._disable_pending = False
        self._stop_pending = False
        self._disable_generation += 1
        for handle in self._recording_handles:
            try:
                if callable(handle):
                    handle()
                else:
                    self._keyboard.remove_hotkey(handle)
            except Exception:
                LOG.debug("Failed to remove recording hotkey", exc_info=True)
        self._recording_handles = []

    def _recording_key_is_down(self) -> bool:
        if self._keyboard is None:
            return False
        for hotkey in (self.config.stop_hotkey, self.config.cancel_hotkey):
            try:
                if self._keyboard.is_pressed(_normalize_hotkey(hotkey)):
                    return True
            except Exception:
                LOG.debug("Could not read key state for %s", hotkey, exc_info=True)
        return False

    def _disable_after_key_release(self, generation: int) -> None:
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            with self._lock:
                if generation != self._disable_generation:
                    return
                key_down = self._recording_key_is_down()
            if not key_down:
                time.sleep(0.15)
                break
            time.sleep(0.02)

        with self._lock:
            if generation == self._disable_generation:
                self._remove_recording_controls_locked()

    def stop(self) -> None:
        with self._lock:
            self.disable_recording_controls(force=True)
            if self._keyboard is not None:
                for handle in self._start_handles:
                    try:
                        self._keyboard.remove_hotkey(handle)
                    except Exception:
                        LOG.debug("Failed to remove start hotkey", exc_info=True)
                if self._hard_abort_handle is not None:
                    try:
                        self._keyboard.remove_hotkey(self._hard_abort_handle)
                    except Exception:
                        LOG.debug("Failed to remove hard abort hotkey", exc_info=True)
            self._start_handles = []
            self._hard_abort_handle = None

    def wait(self) -> None:
        if self._keyboard is None:
            raise HotkeyError("Hotkeys are not running.")
        self._keyboard.wait()

    def _safe_call(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            LOG.exception("Hotkey callback failed")

    def _add_suppressed_recording_key(
        self,
        hotkey: str,
        callback: Callable[[], None],
    ) -> object:
        if self._keyboard is None:
            raise HotkeyError("Hotkeys are not running.")

        if "+" in hotkey:
            return self._keyboard.add_hotkey(
                hotkey,
                callback,
                suppress=True,
                trigger_on_release=False,
            )

        return self._keyboard.hook_key(
            hotkey,
            lambda event: self._handle_suppressed_recording_event(event, callback),
            suppress=True,
        )

    def _handle_suppressed_recording_event(
        self,
        event: object,
        callback: Callable[[], None],
    ) -> bool:
        if _is_key_down_event(event):
            callback()
        return False

    def _handle_stop_key_down(self) -> None:
        self._handle_stop_key()

    def _handle_cancel_key_down(self) -> None:
        self._handle_cancel_key()

    def _handle_stop_key(self) -> None:
        with self._lock:
            if self._stop_pending:
                return
            self._stop_pending = True

        def run() -> None:
            try:
                time.sleep(max(0, int(self.config.hard_abort_window_ms)) / 1000)
                if self._is_pressed(self.config.cancel_hotkey):
                    callback = self._on_hard_abort
                else:
                    callback = self._on_stop
                if callback is not None:
                    self._safe_call(callback)
            finally:
                with self._lock:
                    self._stop_pending = False

        threading.Thread(target=run, daemon=True).start()

    def _handle_cancel_key(self) -> None:
        if self._is_pressed(self.config.stop_hotkey):
            callback = self._on_hard_abort
        else:
            callback = self._on_cancel
        if callback is not None:
            self._safe_call(callback)

    def _is_pressed(self, hotkey: str) -> bool:
        if self._keyboard is None:
            return False
        try:
            return bool(self._keyboard.is_pressed(_normalize_hotkey(hotkey)))
        except Exception:
            LOG.debug("Could not read key state for %s", hotkey, exc_info=True)
            return False


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


def _is_key_down_event(event: object) -> bool:
    return getattr(event, "event_type", None) == "down"
