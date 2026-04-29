from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import AppConfig
from .controller import DictationController
from .notify import UserNotifier
from .overlay import RecordingOverlay
from .state import DictationState


LOG = logging.getLogger(__name__)


class TrayApp:
    def __init__(self, config: AppConfig, controller: DictationController):
        self.config = config
        self.controller = controller
        self.icon = None
        self.title = "Voicely Alt: Ready"
        self.notifier = UserNotifier(config)
        self.overlay = RecordingOverlay(config)

    def set_status(self, state: DictationState, message: str) -> None:
        self.title = f"Voicely Alt: {message or state.value}"
        if self.icon is not None:
            self.icon.title = self.title
        self.notifier.on_status(state, message)
        if state == DictationState.RECORDING:
            self.overlay.show()
        else:
            self.overlay.hide()

    def run(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception as exc:
            raise RuntimeError("Tray dependencies are missing. Install requirements.txt.") from exc

        image = Image.new("RGB", (64, 64), color=(22, 28, 36))
        draw = ImageDraw.Draw(image)
        draw.ellipse((14, 8, 50, 44), fill=(80, 180, 140))
        draw.rectangle((28, 42, 36, 54), fill=(80, 180, 140))
        draw.rectangle((20, 52, 44, 58), fill=(80, 180, 140))

        self.icon = pystray.Icon(
            "voicely_alt",
            image,
            self.title,
            menu=pystray.Menu(
                pystray.MenuItem("Start Live Dictation", lambda: self.controller.start_live_recording()),
                pystray.MenuItem("Start Clipboard Capture", lambda: self.controller.start_clipboard_recording()),
                pystray.MenuItem("Stop Recording", lambda: self.controller.stop_recording()),
                pystray.MenuItem("Cancel Recording", lambda: self.controller.cancel_recording()),
                pystray.MenuItem("Benchmark Models", lambda: self.controller.benchmark()),
                pystray.MenuItem("Settings", lambda: _open_path(Path.home() / ".voicely_alt" / "config.toml")),
                pystray.MenuItem("Quit", self._quit),
            ),
        )
        self.notifier.attach_icon(self.icon)
        self.icon.run()

    def _quit(self) -> None:
        self.overlay.stop()
        if self.icon is not None:
            self.icon.stop()


def _open_path(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        LOG.exception("Could not open path: %s", path)
