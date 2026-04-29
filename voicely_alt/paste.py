from __future__ import annotations

import sys
import time

from .config import AppConfig


class ClipboardPasteError(RuntimeError):
    pass


class ClipboardPaste:
    def __init__(self, config: AppConfig):
        self.config = config

    def paste_text(self, text: str) -> None:
        if not text.strip():
            return

        try:
            import keyboard
            import pyperclip
        except Exception as exc:
            raise ClipboardPasteError(
                "Clipboard paste dependencies are missing. Install requirements.txt."
            ) from exc

        previous_text: str | None = None
        if not self.config.keep_transcript_clipboard:
            try:
                previous_text = pyperclip.paste()
            except Exception:
                previous_text = None

        try:
            pyperclip.copy(text)
            time.sleep(0.05)
            keyboard.send(_paste_shortcut())
            time.sleep(max(0, self.config.paste_restore_delay_ms) / 1000)
        except Exception as exc:
            raise ClipboardPasteError("Could not paste transcript into the active window.") from exc
        finally:
            if not self.config.keep_transcript_clipboard and previous_text is not None:
                try:
                    pyperclip.copy(previous_text)
                except Exception:
                    pass

    def copy_text(self, text: str) -> None:
        if not text.strip():
            return
        try:
            import pyperclip
        except Exception as exc:
            raise ClipboardPasteError(
                "Clipboard dependency is missing. Install requirements.txt."
            ) from exc
        pyperclip.copy(text)


def _paste_shortcut() -> str:
    if sys.platform == "darwin":
        return "command+v"
    return "ctrl+v"
