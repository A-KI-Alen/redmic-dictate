# RedMic Dictate Architecture

RedMic Dictate is structured around a small controller and explicit worker
components. The controller owns user-facing state transitions; worker modules own
long-running background work.

## Core Flow

1. `DictationController` starts and stops recording sessions.
2. `AudioRecorder` captures microphone audio and exposes 5-second WAV chunks.
3. `ChunkPipeline` transcribes chunks in the background:
   - `base` is the fast path and has priority.
   - `small` is optional quality replacement and never blocks final output.
4. `DictationController` assembles the final transcript and sends it to
   `ClipboardPaste`.
5. `TrayApp` and `RecordingOverlay` display status, waveform, and progress.

## Module Responsibilities

- `controller.py`
  Orchestrates recording state, hotkey actions, final output, focus restore,
  cancellation, and hard abort behavior.

- `chunking.py`
  Owns all chunk queues, worker threads, quality replacement, WAV grouping, temp
  audio cleanup, and transcript assembly from preprocessed parts.

- `whispercpp.py`
  Starts, warms, validates, refreshes, and calls local whisper.cpp servers.

- `recorder.py`
  Captures audio, tracks live levels, writes WAV files, and filters silence.

- `hotkeys.py`
  Registers global hotkeys and recording-only stop/cancel controls.

- `overlay.py` and `overlay_window.py`
  Render the recording HUD, cursor ring, taskbar wave, and processing heartbeat.

- `llm.py`
  Optionally cleans clipboard-mode transcripts with the local Ollama model.

## Pipeline Invariants

- The `base` path is always the source of fast output.
- `small` only receives chunks after the matching `base` chunks have completed.
- `small` is skipped whenever the fast queue has backlog.
- `small` is stopped immediately after `Space`.
- Every final transcript is copied to the clipboard as a recovery path.
- `Space+Esc` invalidates the active session and stale worker output is ignored.
- Temporary audio files are removed by the owner that last holds them.

## Stability Notes

- The fast whisper server is warmed at app startup.
- Whisper is forced to German with both server options and request fields.
- Whisper fallback decoding and non-speech tokens are disabled by default.
- Long-running whisper servers are refreshed after the configured max age.
