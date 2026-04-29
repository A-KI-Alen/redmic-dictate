# RedMic Dictate

Local-first dictation for Windows with a global recording hotkey and offline
transcription through `whisper.cpp`.

Deutsche Bedienungsanleitung: [ANLEITUNG.md](ANLEITUNG.md)

## What it does

- Starts live dictation with `Alt+Y`.
- Starts clipboard capture with `Alt+Shift+Y`.
- Stops recording with plain `Space` while recording.
- Cancels recording with `Esc` while recording.
- Hard-aborts with `Space+Esc` in any active state.
- Sends the temporary WAV file to a local `whisper.cpp` server.
- Pastes the German transcript into the currently focused input field.
- Keeps every final transcript in the clipboard as a fallback.
- Can locally clean up clipboard dictations with Ollama and `llama3.2:3b`.
- Signals progress with tray status and Windows notifications; beeps are disabled by default.
- Shows a red top-left recording HUD with current hotkeys and a live microphone level ticker.
- Shows a red cursor ring while recording and a rotating ring while processing.
- Shows a translucent red taskbar wave driven by the live microphone level while recording.
- Switches the taskbar wave to a heartbeat curve while processing.
- Pre-transcribes 5-second chunks in the background to reduce the wait after stopping.
- Uses `base` for fast 5-second chunks and, for longer recordings, runs `small`
  in parallel on 10-second groups to replace finished sections with higher
  quality text.
- Gives the fast `base` path priority: `small` only receives audio after the
  matching `base` chunks are already done and is skipped whenever the fast queue
  has backlog.
- Stops the `small` quality worker immediately after `Space` so final insertion
  is driven by the fast `base` path.
- Warms the `base` whisper server at app startup so short dictations do not pay
  model-load time after `Space`.

`Alt+Y` avoids Windows-reserved shortcuts that can be intercepted before the app sees them.

## Quick start

```powershell
.\scripts\setup.ps1 -Model base
.\scripts\setup_llm.ps1
.\scripts\start.ps1
```

To start RedMic Dictate automatically when you log in to Windows:

```powershell
.\scripts\install_autostart.ps1
```

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
backend = "local_whispercpp"
language = "de"
model = "auto"
threads = "auto"
paste_method = "clipboard"
keep_transcript_clipboard = true
cloud_fallback = "manual"
silence_rms_threshold = 60
live_streaming = false
live_chunk_seconds = 4
background_chunking = true
background_chunk_seconds = 5
quality_chunking = true
quality_model = "small"
quality_threads = "2"
quality_chunk_seconds = 10
quality_max_fast_backlog = 0
quality_wait_after_stop_seconds = 1.5
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
```

When `model = "auto"`, the app uses the benchmark-selected model. If no
benchmark has been run yet, it falls back to `base`.

## Notes

- The app is Windows-first. The core is cross-platform-ready, but global hotkeys
  and paste behavior must be verified per operating system.
- Direct field dictation also leaves the final transcript in the clipboard. If
  the cursor has moved while local processing runs, use `Ctrl+V` or `Windows+V`
  to recover the text.
- `Alt+Y` records into the active field target and inserts text after `Space`.
  Live field insertion is disabled by default; instead, 5-second chunks are
  transcribed in the background and joined after `Space`.
- `Alt+Shift+Y` records until `Space`, then copies the final transcript into the
  clipboard and plays a discreet bell sound. By default this mode also runs a
  local LLM cleanup step before copying the text.
- `Space+Esc` is the emergency brake. It cancels the current session, closes
  local processing backends, discards stale worker output, and pastes nothing.
- LLM cleanup is not applied to direct field dictation by default. On CPU it is
  useful for quality, but too slow for immediate typing.
- Live insertion uses the text clipboard and intentionally keeps the final
  transcript there as a safety net.
- Very quiet or empty recordings are ignored before transcription to avoid local
  Whisper silence hallucinations.
- If the local whisper server is missing or no model has been downloaded, run
  `.\scripts\setup.ps1 -Model base`.
