from __future__ import annotations

import tempfile
import unittest
import wave
import os
import time
from queue import Queue
from pathlib import Path

from voicely_alt.chunking import ChunkResult, QualityResult
from voicely_alt.config import AppConfig
from voicely_alt.controller import DictationController, _missing_realtime_suffix
from voicely_alt.state import DictationState, OutputMode


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

    def current_level(self) -> float:
        return 0.0

    def cancel(self) -> None:
        self.cancelled = True


class FakeTranscriber:
    def __init__(self):
        self.closed = False

    def transcribe(self, audio_path: Path) -> str:
        assert audio_path.exists()
        return "Hallo Welt"

    def close(self) -> None:
        self.closed = True


class QualityFakeTranscriber(FakeTranscriber):
    def __init__(self, text: str):
        super().__init__()
        self.text = text
        self.calls = 0

    def transcribe(self, audio_path: Path) -> str:
        assert audio_path.exists()
        self.calls += 1
        return self.text


class FakePaste:
    def __init__(self):
        self.text = ""
        self.pasted = []
        self.copied = []

    def paste_text(self, text: str) -> None:
        self.text = text
        self.pasted.append(text)

    def copy_text(self, text: str) -> None:
        self.text = text
        self.copied.append(text)


class NamedFakeTranscriber(FakeTranscriber):
    def transcribe(self, audio_path: Path) -> str:
        assert audio_path.exists()
        if audio_path.name == "final.wav":
            return "zweiter Teil"
        return "unbekannt"


class FakeTextProcessor:
    def __init__(self):
        self.calls = []
        self.closed = False

    def will_process(self, mode: OutputMode, live_chunk: bool) -> bool:
        return mode == OutputMode.CLIPBOARD and not live_chunk

    def process(self, text: str, mode: OutputMode, live_chunk: bool) -> str:
        self.calls.append((text, mode, live_chunk))
        return "Hallo, Welt."

    def close(self) -> None:
        self.closed = True


class FakeControls:
    def __init__(self):
        self.enabled = False

    def enable_recording_controls(self) -> None:
        self.enabled = True

    def disable_recording_controls(self, force: bool = False) -> None:
        del force
        self.enabled = False


def _write_test_wav(path: Path, frames: int = 160) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x01\x00" * frames)


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._openai_api_key = os.environ.pop("OPENAI_API_KEY", None)
        self._openai_admin_key = os.environ.pop("OPENAI_ADMIN_KEY", None)

    def tearDown(self) -> None:
        if self._openai_api_key is not None:
            os.environ["OPENAI_API_KEY"] = self._openai_api_key
        if self._openai_admin_key is not None:
            os.environ["OPENAI_ADMIN_KEY"] = self._openai_admin_key

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

    def test_default_live_mode_waits_until_stop_before_pasting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            paste = FakePaste()
            controller = DictationController(
                config=AppConfig(live_streaming=False),
                recorder=FakeRecorder(audio_path),
                transcriber=FakeTranscriber(),
                paste_target=paste,
                controls=FakeControls(),
                background=False,
            )

            self.assertTrue(controller.start_live_recording())
            self.assertEqual(paste.text, "")

            self.assertTrue(controller.stop_recording())

            self.assertEqual(paste.text, "Hallo Welt")

    def test_chunked_final_output_combines_pretranscribed_parts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            final_audio = Path(directory) / "final.wav"
            final_audio.write_bytes(b"fake wav")
            paste = FakePaste()
            controls = FakeControls()
            controller = DictationController(
                config=AppConfig(background_chunking=True),
                recorder=FakeRecorder(final_audio),
                transcriber=NamedFakeTranscriber(),
                paste_target=paste,
                controls=controls,
                background=False,
            )
            controller._session_id = 1
            controller.chunks.store_fast_result(ChunkResult(index=0, text="erster Teil"))

            controller._transcribe_final_with_chunks(final_audio, OutputMode.LIVE_PASTE, 1)

            self.assertEqual(paste.text, "erster Teil zweiter Teil")
            self.assertFalse(final_audio.exists())

    def test_quality_result_replaces_base_chunk_group(self) -> None:
        paste = FakePaste()
        controller = DictationController(
            config=AppConfig(background_chunking=True, quality_chunking=True),
            recorder=FakeRecorder(Path("unused.wav")),
            transcriber=NamedFakeTranscriber(),
            paste_target=paste,
            controls=FakeControls(),
            background=False,
        )
        controller._session_id = 1
        controller.chunks.store_fast_result(ChunkResult(index=0, text="base eins"))
        controller.chunks.store_fast_result(ChunkResult(index=1, text="base zwei"))
        controller.chunks.store_fast_result(ChunkResult(index=2, text="base drei"))
        controller.chunks.store_fast_result(ChunkResult(index=3, text="base vier"))
        controller.chunks.store_quality_result(QualityResult(start_index=0, end_index=2, text="small block"))

        controller._transcribe_final_with_chunks(None, OutputMode.LIVE_PASTE, 1)

        self.assertEqual(paste.text, "small block base vier")

    def test_quality_chunking_queues_configured_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paste = FakePaste()
            controller = DictationController(
                config=AppConfig(
                    background_chunking=True,
                    background_chunk_seconds=5,
                    quality_chunking=True,
                    quality_chunk_seconds=15,
                ),
                recorder=FakeRecorder(Path(directory) / "unused.wav"),
                transcriber=NamedFakeTranscriber(),
                quality_transcriber=FakeTranscriber(),
                paste_target=paste,
                controls=FakeControls(),
                background=False,
            )
            controller.chunks._quality_queue = Queue()
            paths = [Path(directory) / f"chunk_{index}.wav" for index in range(3)]
            for index, path in enumerate(paths):
                _write_test_wav(path, frames=160)
                controller.chunks.maybe_queue_quality_chunk(index, path)

            work = controller.chunks._quality_queue.get_nowait()
            try:
                self.assertEqual(work.start_index, 0)
                self.assertEqual(work.end_index, 2)
                self.assertTrue(work.audio_path.exists())
                self.assertEqual(controller.chunks._quality_pending_chunks, [])
            finally:
                work.audio_path.unlink(missing_ok=True)

    def test_quality_chunking_skips_when_fast_queue_has_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DictationController(
                config=AppConfig(
                    background_chunk_seconds=5,
                    quality_chunk_seconds=10,
                    quality_max_fast_backlog=0,
                ),
                recorder=FakeRecorder(Path(directory) / "unused.wav"),
                transcriber=NamedFakeTranscriber(),
                quality_transcriber=FakeTranscriber(),
                paste_target=FakePaste(),
                controls=FakeControls(),
                background=False,
            )
            controller.chunks._fast_queue = Queue()
            controller.chunks._quality_queue = Queue()
            controller.chunks._fast_queue.put(ChunkResult(index=99, text="pending"))

            paths = [Path(directory) / f"chunk_{index}.wav" for index in range(2)]
            for index, path in enumerate(paths):
                _write_test_wav(path, frames=160)
                controller.chunks.maybe_queue_quality_chunk(index, path)

            self.assertTrue(controller.chunks._quality_queue.empty())
            self.assertEqual(controller.chunks._quality_pending_chunks, [])

    def test_stop_recording_gives_quality_worker_final_wait_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            quality = FakeTranscriber()
            controller = DictationController(
                config=AppConfig(background_chunking=True, quality_chunking=True),
                recorder=FakeRecorder(audio_path),
                transcriber=FakeTranscriber(),
                quality_transcriber=quality,
                paste_target=FakePaste(),
                controls=FakeControls(),
                background=False,
            )

            self.assertTrue(controller.start_live_recording())
            self.assertTrue(controller.stop_recording())

            self.assertFalse(quality.closed)

    def test_quality_guard_copies_better_background_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chunk = Path(directory) / "chunk.wav"
            _write_test_wav(chunk)
            paste = FakePaste()
            quality = QualityFakeTranscriber("deutlich bessere Fassung")
            controller = DictationController(
                config=AppConfig(
                    background_chunking=True,
                    quality_chunking=True,
                    quality_guard_enabled=True,
                    quality_guard_min_recording_seconds=0,
                    quality_guard_min_coverage=1.0,
                ),
                recorder=FakeRecorder(Path(directory) / "unused.wav"),
                transcriber=NamedFakeTranscriber(),
                quality_transcriber=quality,
                paste_target=paste,
                controls=FakeControls(),
                background=False,
            )
            controller._session_id = 1
            controller._session_started_at[1] = 0.0
            controller.chunks.store_fast_result(
                ChunkResult(index=0, text="schlechte base Fassung", audio_path=chunk)
            )

            controller._transcribe_final_with_chunks(None, OutputMode.LIVE_PASTE, 1)

            self.assertEqual(paste.pasted, ["schlechte base Fassung"])
            self.assertEqual(paste.copied, ["deutlich bessere Fassung"])
            self.assertEqual(quality.calls, 1)
            self.assertFalse(chunk.exists())

    def test_progressive_live_paste_writes_fast_chunks_while_recording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paste = FakePaste()
            controller = DictationController(
                config=AppConfig(progressive_live_paste=True),
                recorder=FakeRecorder(Path(directory) / "unused.wav"),
                transcriber=NamedFakeTranscriber(),
                paste_target=paste,
                controls=FakeControls(),
                background=False,
            )

            self.assertTrue(controller.start_live_recording())
            session_id = controller._session_id
            controller._on_fast_chunk_completed(ChunkResult(index=0, text="erster Teil"), session_id)

            self.assertEqual(paste.pasted, ["erster Teil "])
            self.assertEqual(controller._progressive_pasted_chunks, {0})

            controller.cancel_recording()

    def test_progressive_final_pastes_only_missing_chunks_and_keeps_full_clipboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.wav"
            second = Path(directory) / "second.wav"
            _write_test_wav(first)
            _write_test_wav(second)
            paste = FakePaste()
            controller = DictationController(
                config=AppConfig(progressive_live_paste=True),
                recorder=FakeRecorder(Path(directory) / "unused.wav"),
                transcriber=NamedFakeTranscriber(),
                paste_target=paste,
                controls=FakeControls(),
                background=False,
            )
            controller._session_id = 1
            controller._progressive_pasted_chunks = {0}
            controller.chunks.store_fast_result(ChunkResult(index=0, text="erster Teil", audio_path=first))
            controller.chunks.store_fast_result(ChunkResult(index=1, text="zweiter Teil", audio_path=second))

            controller._transcribe_final_with_chunks(None, OutputMode.LIVE_PASTE, 1)

            self.assertEqual(paste.pasted, ["zweiter Teil"])
            self.assertEqual(paste.copied, ["erster Teil zweiter Teil"])
            self.assertEqual(paste.text, "erster Teil zweiter Teil")

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

    def test_clipboard_mode_can_run_text_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"fake wav")
            paste = FakePaste()
            processor = FakeTextProcessor()
            controller = DictationController(
                config=AppConfig(),
                recorder=FakeRecorder(audio_path),
                transcriber=FakeTranscriber(),
                paste_target=paste,
                text_processor=processor,
                controls=FakeControls(),
                background=False,
            )

            self.assertTrue(controller.start_clipboard_recording())
            self.assertTrue(controller.stop_recording())

            self.assertEqual(paste.text, "Hallo, Welt.")
            self.assertEqual(processor.calls, [("Hallo Welt", OutputMode.CLIPBOARD, False)])

    def test_live_chunks_skip_text_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"fake wav")
            paste = FakePaste()
            processor = FakeTextProcessor()
            controller = DictationController(
                config=AppConfig(),
                recorder=FakeRecorder(audio_path),
                transcriber=FakeTranscriber(),
                paste_target=paste,
                text_processor=processor,
                controls=FakeControls(),
                background=False,
            )

            controller._transcribe_and_output(audio_path, OutputMode.LIVE_PASTE, live_chunk=True)

            self.assertEqual(paste.text, "Hallo Welt ")
            self.assertEqual(processor.calls, [])

    def test_hard_abort_discards_stale_worker_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"fake wav")
            paste = FakePaste()
            controls = FakeControls()
            transcriber = FakeTranscriber()
            processor = FakeTextProcessor()
            controller = DictationController(
                config=AppConfig(),
                recorder=FakeRecorder(audio_path),
                transcriber=transcriber,
                paste_target=paste,
                text_processor=processor,
                controls=controls,
                background=False,
            )

            self.assertTrue(controller.start_live_recording())
            stale_session = controller._session_id
            self.assertTrue(controller.hard_abort())
            controller._transcribe_and_output(
                audio_path,
                OutputMode.LIVE_PASTE,
                live_chunk=False,
                session_id=stale_session,
            )

            self.assertEqual(controller.state, DictationState.IDLE)
            self.assertEqual(paste.text, "")
            self.assertFalse(controls.enabled)
            self.assertTrue(transcriber.closed)
            self.assertTrue(processor.closed)

    def test_realtime_suffix_calculates_only_unpasted_tail(self) -> None:
        missing = _missing_realtime_suffix(
            "erster Teil zweiter Teil",
            "erster Teil",
        )

        self.assertEqual(missing, "zweiter Teil")

    def test_realtime_suffix_tolerates_small_wording_differences(self) -> None:
        missing = _missing_realtime_suffix(
            "Das ist ein Test mit einem fehlenden letzten Satz.",
            "Das ist der Test mit einem",
        )

        self.assertEqual(missing, "fehlenden letzten Satz.")

    def test_late_realtime_text_is_accepted_while_transcribing(self) -> None:
        paste = FakePaste()
        controller = DictationController(
            config=AppConfig(),
            recorder=FakeRecorder(Path("unused.wav")),
            transcriber=FakeTranscriber(),
            paste_target=paste,
            controls=FakeControls(),
            background=False,
        )
        controller._session_id = 1
        controller.state = DictationState.TRANSCRIBING
        controller.output_mode = OutputMode.LIVE_PASTE

        self.assertTrue(controller._on_realtime_text("spaetes Segment", OutputMode.LIVE_PASTE, 1))

        self.assertEqual(paste.text, "spaetes Segment ")

    def test_realtime_cost_callback_receives_last_operation_estimate(self) -> None:
        costs = []
        controller = DictationController(
            config=AppConfig(openai_usage_poll_attempts=1, openai_usage_poll_delay_seconds=0),
            recorder=FakeRecorder(Path("unused.wav")),
            transcriber=FakeTranscriber(),
            paste_target=FakePaste(),
            controls=FakeControls(),
            background=False,
            cost_info_callback=lambda cost, source, usage: costs.append((cost, source, usage)),
        )
        controller._session_id = 1
        controller._session_started_at[1] = time.monotonic() - 30.0
        controller._session_started_epoch[1] = time.time() - 30.0

        controller._publish_realtime_operation_cost(1)

        self.assertEqual(costs[0][1], "geschaetzt")
        self.assertEqual(costs[0][2], "30.0s")
        self.assertAlmostEqual(costs[0][0], 30.0 * 0.0028 / 60.0, places=4)

    def test_prompt_leak_is_removed_before_clipboard_output(self) -> None:
        paste = FakePaste()
        config = AppConfig()
        controller = DictationController(
            config=config,
            recorder=FakeRecorder(Path("unused.wav")),
            transcriber=FakeTranscriber(),
            paste_target=paste,
            controls=FakeControls(),
            background=False,
        )
        controller._session_id = 1
        leaked = f"{config.transcription_prompt} {config.transcription_prompt} Eigentliche Notiz."

        self.assertTrue(
            controller._process_and_output_transcript(
                leaked,
                OutputMode.CLIPBOARD,
                live_chunk=False,
                session_id=1,
            )
        )

        self.assertEqual(paste.text, "Eigentliche Notiz.")


if __name__ == "__main__":
    unittest.main()
