from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from voicely_alt.config import AppConfig
from voicely_alt.controller import DictationController
from voicely_alt.state import DictationState


class FakeRecorder:
    def __init__(self, path: Path):
        self.path = path
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True
        self.path.write_bytes(b"fake wav")

    def stop(self) -> Path:
        return self.path

    def stop_if_audio(self) -> Path | None:
        return self.path

    def pop_chunk(self) -> Path | None:
        return None

    def cancel(self) -> None:
        self.cancelled = True


class FakeTranscriber:
    def transcribe(self, audio_path: Path) -> str:
        assert audio_path.exists()
        return "Hallo Welt"


class FakePaste:
    def __init__(self):
        self.text = ""

    def paste_text(self, text: str) -> None:
        self.text = text

    def copy_text(self, text: str) -> None:
        self.text = text


class FakeControls:
    def __init__(self):
        self.enabled = False

    def enable_recording_controls(self) -> None:
        self.enabled = True

    def disable_recording_controls(self) -> None:
        self.enabled = False


class ControllerTests(unittest.TestCase):
    def test_start_stop_transcribes_and_pastes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            paste = FakePaste()
            controls = FakeControls()
            controller = DictationController(
                config=AppConfig(),
                recorder=FakeRecorder(audio_path),
                transcriber=FakeTranscriber(),
                paste_target=paste,
                controls=controls,
                background=False,
            )

            self.assertTrue(controller.start_recording())
            self.assertEqual(controller.state, DictationState.RECORDING)
            self.assertTrue(controls.enabled)

            self.assertTrue(controller.stop_recording())

            self.assertEqual(controller.state, DictationState.IDLE)
            self.assertFalse(controls.enabled)
            self.assertEqual(paste.text, "Hallo Welt")
            self.assertFalse(audio_path.exists())

    def test_space_stop_is_only_available_while_recording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            controls = FakeControls()
            controller = DictationController(
                config=AppConfig(),
                recorder=FakeRecorder(audio_path),
                transcriber=FakeTranscriber(),
                paste_target=FakePaste(),
                controls=controls,
                background=False,
            )

            self.assertFalse(controls.enabled)
            self.assertFalse(controller.stop_recording())
            self.assertFalse(controls.enabled)

            self.assertTrue(controller.start_recording())
            self.assertTrue(controls.enabled)

            self.assertTrue(controller.cancel_recording())
            self.assertFalse(controls.enabled)
            self.assertEqual(controller.state, DictationState.IDLE)

    def test_clipboard_mode_copies_final_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            paste = FakePaste()
            controller = DictationController(
                config=AppConfig(),
                recorder=FakeRecorder(audio_path),
                transcriber=FakeTranscriber(),
                paste_target=paste,
                controls=FakeControls(),
                background=False,
            )

            self.assertTrue(controller.start_clipboard_recording())
            self.assertTrue(controller.stop_recording())

            self.assertEqual(controller.state, DictationState.IDLE)
            self.assertEqual(paste.text, "Hallo Welt")


if __name__ == "__main__":
    unittest.main()
