from __future__ import annotations

import logging
import sys
import threading
from dataclasses import replace

from .config import AppConfig
from .controller import DictationController
from .hotkeys import KeyboardHotkeyManager
from .llm import OllamaTranscriptCleaner
from .paths import logs_dir
from .paste import ClipboardPaste
from .recorder import AudioRecorder
from .single_instance import AlreadyRunningError, SingleInstance
from .tracking import EventTracker, NullTracker
from .tray import TrayApp
from .whispercpp import WhisperCppServerManager, WhisperCppTranscriber, stop_stale_whisper_servers


def configure_logging() -> None:
    log_file = logs_dir() / "redmic-dictate.log"
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
    tracker = EventTracker(config) if config.tracking_enabled else NullTracker()
    tracker.record(
        "app_started",
        model=config.resolved_model(),
        quality_model=config.quality_model if config.quality_chunking else "",
        language=config.language,
        live_hotkey=config.live_hotkey,
        clipboard_hotkey=config.clipboard_hotkey,
        stop_hotkey=config.stop_hotkey,
        cancel_hotkey=config.cancel_hotkey,
        hard_abort_hotkey=config.hard_abort_hotkey,
        background_chunk_seconds=config.background_chunk_seconds,
        quality_chunk_seconds=config.quality_chunk_seconds,
    )
    server = WhisperCppServerManager(config)
    fast_transcriber = WhisperCppTranscriber(config, server)
    quality_transcriber = None
    if config.quality_chunking and config.quality_model:
        quality_config = replace(
            config,
            model=config.quality_model,
            selected_model="",
            threads=config.quality_threads,
            port=int(config.port) + 1,
        )
        quality_transcriber = WhisperCppTranscriber(
            quality_config,
            WhisperCppServerManager(quality_config),
        )
    controller = DictationController(
        config=config,
        recorder=AudioRecorder(config),
        transcriber=fast_transcriber,
        quality_transcriber=quality_transcriber,
        paste_target=ClipboardPaste(config),
        text_processor=OllamaTranscriptCleaner(config),
        controls=hotkeys,
        tracker=tracker,
    )
    tray = TrayApp(config, controller)
    controller.status_callback = tray.set_status
    controller.level_callback = tray.set_audio_level
    hotkeys.start(
        controller.start_live_recording,
        controller.start_clipboard_recording,
        controller.stop_recording,
        controller.cancel_recording,
        controller.hard_abort,
    )
    _warm_fast_transcriber(fast_transcriber)
    return controller, hotkeys, tray


def run_app(no_tray: bool = False) -> None:
    configure_logging()
    try:
        with SingleInstance():
            stop_stale_whisper_servers()
            config = AppConfig.load_or_create()
            controller, hotkeys, tray = build_app(config)
            try:
                if no_tray:
                    print("RedMic Dictate is running. Press Alt+Y for live dictation.")
                    hotkeys.wait()
                else:
                    tray.run()
            finally:
                controller.shutdown()
                hotkeys.stop()
    except AlreadyRunningError:
        logging.info("RedMic Dictate is already running; exiting duplicate instance.")


def _warm_fast_transcriber(transcriber: WhisperCppTranscriber) -> None:
    def run() -> None:
        try:
            transcriber.server.ensure_running()
        except Exception:
            logging.warning("Could not warm up fast whisper.cpp server", exc_info=True)

    threading.Thread(target=run, daemon=True).start()
