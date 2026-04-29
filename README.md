# RedMic Dictate

Local-first dictation for Windows with a global recording hotkey and offline
transcription through `whisper.cpp`.

Deutsche Bedienungsanleitung: [ANLEITUNG.md](ANLEITUNG.md)

## What it does

- Starts live dictation with `Alt+Y`.
- Starts clipboard capture with `Alt+Shift+Y`.
- Stops recording with plain `Space` while recording.
- Cancels recording with `Esc` while recording.
- Sends the temporary WAV file to a local `whisper.cpp` server.
- Pastes the German transcript into the currently focused input field.
- Can locally clean up clipboard dictations with Ollama and `llama3.2:3b`.
- Signals progress with tray status, Windows notifications, and short beeps.
- Shows a large red microphone overlay at the mouse pointer while recording.
- Shows a translucent red bar over the Windows taskbar while recording.

`Alt+Y` avoids Windows-reserved shortcuts that can be intercepted before the app sees them.

## Quick start

```powershell
.\scripts\setup.ps1 -Model small
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
backend = "local_whispercpp"
language = "de"
model = "auto"
threads = "auto"
paste_method = "clipboard"
cloud_fallback = "manual"
silence_rms_threshold = 60
live_chunk_seconds = 4
beep_feedback = true
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
- The clipboard integration restores the previous text clipboard after pasting.
  Non-text clipboard formats are not preserved in this MVP.
- `Alt+Y` inserts text chunk by chunk into the active field while you
  dictate. It still uses short local transcription chunks, not true word-by-word
  streaming.
- `Alt+Shift+Y` records until `Space`, then copies the final transcript into the
  clipboard and plays a discreet bell sound. By default this mode also runs a
  local LLM cleanup step before copying the text.
- LLM cleanup is not applied to live chunks by default. On CPU it is useful for
  quality, but too slow for immediate live typing.
- Live insertion briefly uses the text clipboard for each paste chunk and then
  restores the previous text clipboard.
- Very quiet or empty recordings are ignored before transcription to avoid local
  Whisper silence hallucinations.
- If the local whisper server is missing or no model has been downloaded, run
  `.\scripts\setup.ps1 -Model base`.
