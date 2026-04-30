# RedMic Dictate Architecture

RedMic Dictate is structured around a small controller and explicit worker
components. The controller owns user-facing state transitions; worker modules own
long-running background work.

## Core Flow

1. `DictationController` starts and stops recording sessions.
2. `AudioRecorder` captures microphone audio, exposes a non-destructive PCM
   stream tap for OpenAI Realtime, and still keeps the full recording for local
   fallback.
3. `OpenAIRealtimeTranscriptionSession` streams PCM to OpenAI in short commits
   and delivers completed German transcript parts back to the controller.
4. If OpenAI Realtime is unavailable or returns no usable text, the controller
   falls back to the local `ChunkPipeline`.
5. `ChunkPipeline` transcribes chunks in the background:
   - `base` is the fast path and has priority.
   - `small` is optional quality replacement and never blocks final output.
6. `DictationController` can progressively paste finished realtime/local chunks while
   retaining the full transcript for clipboard recovery and quality correction.
7. `DictationController` assembles the final transcript and sends it to
   `ClipboardPaste`.
8. `TrayApp` and `RecordingOverlay` display status, waveform, and progress.
9. `EventTracker` writes local diagnostic events for later 24-hour reviews.

## Module Responsibilities

- `controller.py`
  Orchestrates recording state, hotkey actions, final output, focus restore,
  cancellation, and hard abort behavior.

- `chunking.py`
  Owns all chunk queues, worker threads, quality replacement, WAV grouping, temp
  audio cleanup, and transcript assembly from preprocessed parts.

- `openai_realtime.py`
  Owns WebSocket connection setup, Realtime transcription session updates,
  non-destructive PCM streaming, periodic audio commits, ordered completion
  delivery, and local-fallback result reporting.

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

- `tracking.py`
  Writes local JSONL telemetry for sessions, state changes, chunk progress,
  transcription timings, errors, and output metadata. Full transcript text is
  disabled by default.

## Pipeline Invariants

- OpenAI Realtime is the default fast-quality path when `OPENAI_API_KEY` is
  available.
- The local `base` path remains the guaranteed offline fallback.
- Realtime streaming never consumes the local recording buffer; fallback always
  has the full audio.
- `small` only receives chunks after the matching `base` chunks have completed.
- `small` is skipped whenever the fast queue has backlog.
- `small` gets a bounded wait window after `Space`.
- Progressive live paste only appends text that has not already been inserted.
- If finished `small` coverage is too low for a longer dictation, the quality
  guard can reprocess retained audio in the background and update the clipboard.
- Every final transcript is copied to the clipboard as a recovery path.
- `Space+Esc` invalidates the active session and stale worker output is ignored.
- Temporary audio files are removed by the owner that last holds them.
- Tracking failures must never block recording, transcription, or paste behavior.

## Stability Notes

- The fast whisper server is warmed at app startup.
- The OpenAI key value is read only from the configured environment variable and
  is never written to config, logs, tracking, or docs.
- Whisper is forced to German with both server options and request fields.
- Whisper fallback decoding and non-speech tokens are disabled by default.
- Long-running whisper servers are refreshed after the configured max age.
