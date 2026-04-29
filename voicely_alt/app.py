from __future__ import annotations

import logging
import sys

from .config import AppConfig
from .controller import DictationController
from .hotkeys import KeyboardHotkeyManager
from .paths import logs_dir
from .paste import ClipboardPaste
from .recorder import AudioRecorder
from .single_instance import AlreadyRunningError, SingleInstance
from .tray import TrayApp
from .whispercpp import WhisperCppServerManager, WhisperCppTranscriber


def configure_logging() -> None:
    log_file = logs_dir() / "voicely-alt.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def build_app(config: AppConfig) -> tuple[DictationController, KeyboardHotkeyManager, TrayApp]:
    hotkeys = KeyboardHotkeyManager(config)
    server = WhisperCppServerManager(config)
    controller = DictationController(
        config=config,
        recorder=AudioRecorder(config),
        transcriber=WhisperCppTranscriber(config, server),
        paste_target=ClipboardPaste(config),
        controls=hotkeys,
    )
    tray = TrayApp(config, controller)
    controller.status_callback = tray.set_status
    hotkeys.start(
        controller.start_live_recording,
        controller.start_clipboard_recording,
        controller.stop_recording,
        controller.cancel_recording,
    )
    return controller, hotkeys, tray


def run_app(no_tray: bool = False) -> None:
    configure_logging()
    try:
        with SingleInstance():
            config = AppConfig.load_or_create()
            controller, hotkeys, tray = build_app(config)
            try:
                if no_tray:
                    print("Voicely Alt is running. Press Win+Alt+Space for live dictation.")
                    hotkeys.wait()
                else:
                    tray.run()
            finally:
                controller.shutdown()
                hotkeys.stop()
    except AlreadyRunningError:
        logging.info("Voicely Alt is already running; exiting duplicate instance.")
