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
        self._recording_monitor_stop: threading.Event | None = None
        self._recording_monitor_thread: threading.Thread | None = None
        self._start_key_pending = False
        self._last_start_at = 0.0
        self._hard_abort_latched = False

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
            self._start_handles = self._register_start_hotkeys(on_live_start, on_clipboard_start)
            self._hard_abort_handle = keyboard.add_hotkey(
                _normalize_hotkey(self.config.hard_abort_hotkey),
                self._handle_global_hard_abort,
                suppress=False,
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
                self._add_blocked_recording_key(
                    _normalize_hotkey(self.config.stop_hotkey),
                    self._handle_stop_key_down,
                ),
                self._add_blocked_recording_key(
                    _normalize_hotkey(self.config.cancel_hotkey),
                    self._handle_cancel_key_down,
                ),
            ]
            self._start_recording_monitor_locked()
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
        self._stop_recording_monitor_locked()
        for handle in self._recording_handles:
            try:
                self._remove_keyboard_handle(handle)
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
                        self._remove_keyboard_handle(handle)
                    except Exception:
                        LOG.debug("Failed to remove start hotkey", exc_info=True)
                if self._hard_abort_handle is not None:
                    try:
                        self._remove_keyboard_handle(self._hard_abort_handle)
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

    def _register_start_hotkeys(
        self,
        on_live_start: Callable[[], None],
        on_clipboard_start: Callable[[], None],
    ) -> list[object]:
        if self._keyboard is None:
            raise HotkeyError("Hotkeys are not running.")

        live_hotkey = _normalize_hotkey(self.config.live_hotkey)
        clipboard_hotkey = _normalize_hotkey(self.config.clipboard_hotkey)
        if live_hotkey == "alt+y" and clipboard_hotkey == "alt+shift+y":
            return [
                self._keyboard.hook_key(
                    "y",
                    lambda event: self._handle_alt_y_start_event(
                        event,
                        on_live_start,
                        on_clipboard_start,
                    ),
                    suppress=True,
                )
            ]

        return [
            self._keyboard.add_hotkey(
                live_hotkey,
                lambda: self._handle_start_callback(on_live_start),
                suppress=True,
                trigger_on_release=False,
            ),
            self._keyboard.add_hotkey(
                clipboard_hotkey,
                lambda: self._handle_start_callback(on_clipboard_start),
                suppress=True,
                trigger_on_release=False,
            ),
        ]

    def _handle_alt_y_start_event(
        self,
        event: object,
        on_live_start: Callable[[], None],
        on_clipboard_start: Callable[[], None],
    ) -> bool:
        alt_down = self._is_pressed("alt")
        shift_down = self._is_pressed("shift")

        with self._lock:
            pending = self._start_key_pending
            if _is_key_up_event(event):
                self._start_key_pending = False

        if not alt_down and not pending:
            return True

        if _is_key_down_event(event):
            with self._lock:
                if self._start_key_pending:
                    return False
                self._start_key_pending = True
            self._handle_start_callback(on_clipboard_start if shift_down else on_live_start)

        return False

    def _handle_start_callback(self, callback: Callable[[], None]) -> None:
        debounce_seconds = max(0, int(self.config.start_debounce_ms)) / 1000
        now = time.monotonic()
        with self._lock:
            if debounce_seconds and now - self._last_start_at < debounce_seconds:
                LOG.info("Ignored duplicate start hotkey within debounce window")
                return
            self._last_start_at = now
        self._safe_call(callback)

    def _add_blocked_recording_key(
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

        block_key = getattr(self._keyboard, "block_key", None)
        if callable(block_key):
            return block_key(hotkey)
        return self._keyboard.hook_key(hotkey, lambda event: False, suppress=True)

    def _remove_keyboard_handle(self, handle: object) -> None:
        if self._keyboard is None:
            return
        if callable(handle):
            handle()
        else:
            self._keyboard.remove_hotkey(handle)

    def _start_recording_monitor_locked(self) -> None:
        if self._recording_monitor_thread is not None and self._recording_monitor_thread.is_alive():
            return

        stop_event = threading.Event()
        self._recording_monitor_stop = stop_event
        self._recording_monitor_thread = threading.Thread(
            target=self._recording_monitor_loop,
            args=(stop_event,),
            daemon=True,
        )
        self._recording_monitor_thread.start()

    def _stop_recording_monitor_locked(self) -> None:
        stop_event = self._recording_monitor_stop
        thread = self._recording_monitor_thread
        self._recording_monitor_stop = None
        self._recording_monitor_thread = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.3)

    def _recording_monitor_loop(self, stop_event: threading.Event) -> None:
        last_stop_down = False
        last_cancel_down = False

        while not stop_event.wait(0.015):
            stop_down = self._is_pressed(self.config.stop_hotkey)
            cancel_down = self._is_pressed(self.config.cancel_hotkey)

            if stop_down and cancel_down and not (last_stop_down and last_cancel_down):
                LOG.info("Detected recording hard abort keys")
                callback = self._on_hard_abort
                if callback is not None:
                    self._safe_call(callback)
            elif stop_down and not last_stop_down:
                LOG.info("Detected recording stop key")
                self._handle_stop_key()
            elif cancel_down and not last_cancel_down:
                LOG.info("Detected recording cancel key")
                self._handle_cancel_key()

            last_stop_down = stop_down
            last_cancel_down = cancel_down

    def _handle_stop_key_down(self) -> None:
        self._handle_stop_key()

    def _handle_cancel_key_down(self) -> None:
        self._handle_cancel_key()

    def _handle_global_hard_abort(self) -> None:
        with self._lock:
            if self._hard_abort_latched:
                return
            self._hard_abort_latched = True
            callback = self._on_hard_abort

        if callback is not None:
            self._safe_call(callback)

        threading.Thread(target=self._release_hard_abort_latch, daemon=True).start()

    def _release_hard_abort_latch(self) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not self._hotkey_parts_pressed(self.config.hard_abort_hotkey):
                break
            time.sleep(0.05)
        with self._lock:
            self._hard_abort_latched = False

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

    def _hotkey_parts_pressed(self, hotkey: str) -> bool:
        parts = [part for part in _normalize_hotkey(hotkey).split("+") if part]
        if not parts:
            return False
        return all(self._is_pressed(part) for part in parts)


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


def _is_key_up_event(event: object) -> bool:
    return getattr(event, "event_type", None) == "up"
