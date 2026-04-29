from __future__ import annotations

import logging
import sys
import time

from .config import AppConfig
from .state import DictationState


LOG = logging.getLogger(__name__)


class UserNotifier:
    def __init__(self, config: AppConfig):
        self.config = config
        self.icon = None
        self._last_notification = 0.0

    def attach_icon(self, icon) -> None:
        self.icon = icon

    def on_status(self, state: DictationState, message: str) -> None:
        if self.config.beep_feedback:
            _beep_for_status(state, message)

        if not self.config.tray_notifications:
            return

        title, body = _notification_text(state, message)
        if not body:
            return

        now = time.monotonic()
        if state not in {DictationState.IDLE, DictationState.ERROR} and now - self._last_notification < 0.8:
            return
        self._last_notification = now

        if self.icon is not None:
            try:
                self.icon.notify(body, title)
            except Exception:
                LOG.debug("Tray notification failed", exc_info=True)


def _notification_text(state: DictationState, message: str) -> tuple[str, str]:
    if state == DictationState.RECORDING:
        return "RedMic Dictate", "Aufnahme laeuft. Leertaste stoppt, Esc bricht ab."
    if state == DictationState.TRANSCRIBING:
        return "RedMic Dictate", "Transkription laeuft."
    if state == DictationState.IDLE and message:
        return "RedMic Dictate", message
    if state == DictationState.ERROR:
        return "RedMic Dictate Fehler", message
    return "RedMic Dictate", ""


def _beep_for_status(state: DictationState, message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound

        if state == DictationState.IDLE and "Zwischenablage" in message:
            winsound.Beep(1047, 80)
            winsound.Beep(1319, 90)
            winsound.Beep(1568, 120)
        elif state == DictationState.RECORDING:
            winsound.Beep(880, 80)
        elif state == DictationState.TRANSCRIBING:
            winsound.Beep(660, 70)
        elif state == DictationState.IDLE:
            winsound.Beep(990, 70)
            winsound.Beep(1320, 90)
        elif state == DictationState.ERROR:
            winsound.Beep(220, 180)
    except Exception:
        LOG.debug("Beep feedback failed", exc_info=True)

