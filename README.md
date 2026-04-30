# RedMic Dictate

Hybrid dictation for Windows with a global recording hotkey. The default path
uses OpenAI Realtime transcription with `gpt-4o-mini-transcribe`; if no API key,
network, or Realtime session is available, RedMic falls back to local
`whisper.cpp`.

Deutsche Bedienungsanleitung: [ANLEITUNG.md](ANLEITUNG.md)

Architecture notes: [ARCHITECTURE.md](ARCHITECTURE.md)

## What it does

- Starts live dictation with `Alt+Y`.
- Starts clipboard capture with `Alt+Shift+Y`.
- Stops recording with plain `Space` while recording.
- Cancels recording with `Esc` while recording.
- Hard-aborts with `Space+Esc` in any active state.
- Streams microphone audio to OpenAI Realtime transcription in 3-second commits.
- Uses `gpt-4o-mini-transcribe` first; `gpt-4o-transcribe` is the planned
  quality upgrade if mini is not good enough.
- Falls back to local `whisper.cpp` automatically when OpenAI Realtime cannot
  start or returns no usable text.
- Keeps the full local recording while streaming, so fallback has the whole
  dictation available.
- Pastes the German transcript into the currently focused input field.
- Keeps every final transcript in the clipboard as a fallback.
- Can locally clean up clipboard dictations with Ollama and `llama3.2:3b`.
- Signals progress with tray status and Windows notifications; beeps are disabled by default.
- Shows a red top-left recording HUD with current hotkeys and a live microphone level ticker.
- Shows a red cursor ring while recording and a rotating ring while processing.
- Shows a translucent red taskbar wave driven by the live microphone level while recording.
- Switches the taskbar wave to a heartbeat curve while processing.
- Progressively pastes finished fast chunks into the active field while you keep
  speaking, so there is visible text feedback during longer dictations.
- Pre-transcribes 5-second chunks in the background to reduce the wait after stopping.
- Uses `base` for fast 5-second chunks and, for longer recordings, runs `small`
  in parallel on 10-second groups to replace finished sections with higher
  quality text.
- Gives the fast `base` path priority: `small` only receives audio after the
  matching `base` chunks are already done and is skipped whenever the fast queue
  has backlog.
- Gives the `small` quality worker a short wait window after `Space`; the
  default is 7 seconds.
- If `small` did not cover enough of a longer dictation, starts a background
  quality guard that transcribes the retained audio with `small` and copies the
  improved version into the clipboard when ready.
- Warms the `base` whisper server at app startup so short dictations do not pay
  model-load time after `Space`.
- Forces German transcription with a German prompt, disables temperature fallback,
  suppresses non-speech tokens, and refreshes long-running whisper servers.
- Writes local JSONL diagnostic events for sessions, chunks, processing times,
  errors, and outputs so problematic runs can be reviewed later.

`Alt+Y` avoids Windows-reserved shortcuts that can be intercepted before the app sees them.

## Quick start

```powershell
.\scripts\setup.ps1 -Model base
.\scripts\setup_llm.ps1
.\scripts\start.ps1
```

For OpenAI Realtime mode, set an API key in your Windows user environment
before starting the app:

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "DEIN_OPENAI_API_KEY", "User")
```

Do not put the API key into `config.toml` or commit it to Git.

Optional: if you want RedMic to replace the immediate per-dictation estimate
with OpenAI usage data after a run, set an admin usage key separately:

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_ADMIN_KEY", "DEIN_OPENAI_ADMIN_KEY", "User")
```

Without `OPENAI_ADMIN_KEY`, the overlay still shows the active backend, model,
and an estimated EUR amount for the last OpenAI dictation.

To start RedMic Dictate automatically when you log in to Windows:

```powershell
.\scripts\install_autostart.ps1
```

The installer writes a small launcher into `%USERPROFILE%\.redmic_dictate` and
points the Windows Startup shortcut to that launcher. This lets startup wait for
cloud or mapped drives before launching the app.

To remove autostart again:

```powershell
.\scripts\remove_autostart.ps1
```

The setup command downloads the latest Windows x64 `whisper.cpp` release and the
selected multilingual model into `%USERPROFILE%\.redmic_dictate`.
`setup_llm.ps1` installs a portable Ollama runtime when needed, pulls
`llama3.2:3b`, and enables local cleanup for clipboard dictations.

For better local model selection, record a short German benchmark sample and let
the app test `tiny`, `base`, and `small`:

```powershell
.\scripts\benchmark.ps1 -RecordSeconds 8
```

## Configuration

The config file is created at:

```text
%USERPROFILE%\.redmic_dictate\config.toml
```

Default values:

```toml
start_hotkey = "alt+y"
live_hotkey = "alt+y"
clipboard_hotkey = "alt+shift+y"
stop_hotkey = "space"
cancel_hotkey = "esc"
hard_abort_hotkey = "space+esc"
hard_abort_window_ms = 250
backend = "openai_realtime"
language = "de"
transcription_prompt = "Dies ist ein deutsches Diktat. Transkribiere ausschliesslich auf Deutsch. Schreibe keine englischen Woerter, ausser sie wurden klar gesprochen. Fachbegriffe: RedMic Dictate, Windows, Alt, Shift, Zwischenablage, Transkription, Mikrofon, Codex, OpenAI."
whisper_no_fallback = true
whisper_suppress_non_speech = true
whisper_server_max_age_seconds = 14400
model = "auto"
threads = "auto"
paste_method = "clipboard"
keep_transcript_clipboard = true
cloud_fallback = "local_whispercpp"
openai_api_key_env = "OPENAI_API_KEY"
openai_realtime_url = "wss://api.openai.com/v1/realtime"
openai_realtime_session_model = "gpt-realtime"
openai_realtime_transcription_model = "gpt-4o-mini-transcribe"
openai_realtime_fallback_model = "gpt-4o-transcribe"
openai_realtime_audio_rate = 24000
openai_realtime_commit_seconds = 3.0
openai_realtime_finish_timeout_seconds = 7.0
openai_realtime_connect_timeout_seconds = 6.0
openai_realtime_send_interval_ms = 120
openai_realtime_noise_reduction = "near_field"
openai_realtime_mini_transcribe_eur_per_minute = 0.0028
openai_realtime_transcribe_eur_per_minute = 0.0056
openai_usage_admin_key_env = "OPENAI_ADMIN_KEY"
openai_usage_project_id = ""
openai_usage_api_key_id = ""
openai_usage_poll_delay_seconds = 20.0
openai_usage_poll_attempts = 3
silence_rms_threshold = 60
live_streaming = false
live_chunk_seconds = 4
progressive_live_paste = true
background_chunking = true
background_chunk_seconds = 5
quality_chunking = true
quality_model = "small"
quality_threads = "6"
quality_chunk_seconds = 10
quality_max_fast_backlog = 1
quality_wait_after_stop_seconds = 7.0
quality_guard_enabled = true
quality_guard_min_recording_seconds = 20
quality_guard_min_coverage = 0.50
quality_guard_min_text_ratio = 0.40
beep_feedback = false
tray_notifications = true
recording_overlay = true
overlay_size = 72
taskbar_recording_overlay = true
taskbar_overlay_height = 22
taskbar_overlay_alpha = 0.90
transcript_cleanup = "clipboard"
cleanup_backend = "ollama"
cleanup_model = "llama3.2:3b"
cleanup_keep_alive = "30m"
tracking_enabled = true
tracking_retention_days = 14
tracking_include_transcript_text = false
tracking_transcript_preview_chars = 0
```

When `model = "auto"`, the app uses the benchmark-selected model. If no
benchmark has been run yet, it falls back to `base`.

## Notes

- The app is Windows-first. The core is cross-platform-ready, but global hotkeys
  and paste behavior must be verified per operating system.
- OpenAI Realtime needs the `OPENAI_API_KEY` environment variable. Without it,
  RedMic logs the reason and immediately uses the local whisper.cpp fallback.
- Direct field dictation also leaves the final transcript in the clipboard. If
  the cursor has moved while local processing runs, use `Ctrl+V` or `Windows+V`
  to recover the text.
- `Alt+Y` records into the active field target and inserts text after `Space`.
  Finished 5-second chunks are also pasted progressively while recording. The
  full transcript is kept in the clipboard after the run.
- `Alt+Shift+Y` records until `Space`, then copies the final transcript into the
  clipboard and plays a discreet bell sound. By default this mode also runs a
  local LLM cleanup step before copying the text.
- For longer dictations with weak `small` coverage, RedMic keeps temporary audio
  just long enough to run a background quality guard. The fast transcript stays
  available immediately; the better `small` version replaces the clipboard
  later if it passes basic safety checks.
- `Space+Esc` is the emergency brake. It cancels the current session, closes
  local processing backends, discards stale worker output, and pastes nothing.
- LLM cleanup is not applied to direct field dictation by default. On CPU it is
  useful for quality, but too slow for immediate typing.
- Live insertion uses the text clipboard and intentionally keeps the final
  transcript there as a safety net.
- Very quiet or empty recordings are ignored before transcription to avoid local
  Whisper silence hallucinations.
- Tracking files are written locally under
  `%USERPROFILE%\.redmic_dictate\logs\events-YYYY-MM-DD.jsonl`. By default they
  contain metadata, timings, counts, hashes, and errors, but no full dictated
  text and no audio.
- To inspect the last 24 hours:

```powershell
.\.venv\Scripts\python.exe -m voicely_alt diagnostics --hours 24 --write
```

- If the local whisper server is missing or no model has been downloaded, run
  `.\scripts\setup.ps1 -Model base`.
