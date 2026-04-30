# RedMic Dictate Anleitung

RedMic Dictate ist ein hybrides Diktier-Tool fuer Windows. Es laeuft im
Hintergrund, nimmt per Tastenkombination Sprache auf, transkribiert standardmaessig
live ueber OpenAI Realtime mit `gpt-4o-mini-transcribe` und schreibt den Text
entweder direkt in das aktive Eingabefeld oder legt ihn in die Zwischenablage.
Wenn kein API-Key, kein Netz oder kein nutzbarer OpenAI-Stream vorhanden ist,
faellt RedMic automatisch auf lokales `whisper.cpp` zurueck.

## Tastenkombinationen

- `Alt+Y`: Direkt-Diktat starten. Der stabile Standard transkribiert nach dem
  Stoppen und fuegt den fertigen Text in das aktive Eingabefeld ein.
- `Alt+Shift+Y`: Aufnahme fuer die Zwischenablage starten. Nach dem Stoppen wird
  der ganze Text in die Zwischenablage kopiert.
- `Space`: Aufnahme stoppen.
- `Esc`: Aufnahme abbrechen.
- `Space+Esc`: harter Abbruch. Beendet Aufnahme, Verarbeitung und Overlay
  sofort, verwirft die laufende Sitzung und fuegt keinen Text ein.

Wenn die Aufnahme laeuft, siehst du oben links ein rotes Statusfeld mit den
aktuellen Tastenkombinationen und einer laufenden Mikrofon-Pegelanzeige. Dazu
kommt eine rote Wave-Leiste ueber der Windows-Taskleiste, die live dem
Mikrofonpegel folgt. Sobald die Aufnahme stoppt und verarbeitet wird, wechselt
die Leiste auf eine Herzschlag-Kurve. Am Mauszeiger erscheint
ein roter Ring; waehrend Text transkribiert oder nachkorrigiert wird, dreht sich
dieser Ring. Bei laengeren Direkt-Diktaten werden fertige schnelle Chunks schon
waehrend der Aufnahme ins aktive Feld geschrieben. Jeder fertige Text bleibt
zusaetzlich in der Zwischenablage, damit
du ihn bei Bedarf mit `Ctrl+V` oder `Windows+V` wieder einfuegen kannst.

## Installation

Einmalig im Projektordner ausfuehren:

```powershell
.\scripts\setup.ps1 -Model base
```

Das Skript erstellt eine lokale `.venv`, installiert die Python-Abhaengigkeiten,
laedt `whisper.cpp` herunter und installiert das deutsche/multilinguale
`base`-Modell.

Fuer bessere Qualitaet:

```powershell
.\scripts\setup.ps1 -Model small
.\scripts\setup_llm.ps1
```

`small` verbessert die Roh-Transkription. `setup_llm.ps1` installiert ein
lokales Ollama-Sprachmodell fuer vorsichtige Nachkorrektur in der
Zwischenablage-Variante.

## Starten

Manuell starten:

```powershell
.\scripts\start.ps1
```

Nach dem Start laeuft das Tool im Hintergrund im Tray.

## Automatisch mit Windows starten

Autostart aktivieren:

```powershell
.\scripts\install_autostart.ps1
```

Der Autostart nutzt einen kleinen Launcher unter
`C:\Users\AE\.redmic_dictate\start_redmic_autostart.ps1`. Dieser wartet beim
Windows-Login auf den Projektordner, falls das Laufwerk `I:` noch nicht sofort
bereit ist.

Autostart wieder entfernen:

```powershell
.\scripts\remove_autostart.ps1
```

## Nutzung

1. Klicke in das Eingabefeld, in das Text geschrieben werden soll.
2. Druecke `Alt+Y`.
3. Sprich den Text.
4. Druecke `Space`, um zu stoppen.

Mit OpenAI Realtime werden stabile Teilstuecke schon waehrend der Aufnahme
eingefuegt. Nach `Space` wird der Rest abgeschlossen; der volle Text bleibt
gleichzeitig als Sicherung in der Zwischenablage.

Fuer die Zwischenablage:

1. Druecke `Alt+Shift+Y`.
2. Sprich den Text.
3. Druecke `Space`.
4. Danach liegt der Text in der Zwischenablage und kann mit `Ctrl+V` eingefuegt
   werden.

## Konfiguration

Die aktive Konfiguration liegt hier:

```text
C:\Users\AE\.redmic_dictate\config.toml
```

Wichtige Werte:

```toml
live_hotkey = "alt+y"
clipboard_hotkey = "alt+shift+y"
stop_hotkey = "space"
cancel_hotkey = "esc"
hard_abort_hotkey = "space+esc"
language = "de"
transcription_prompt = "Dies ist ein deutsches Diktat. Transkribiere ausschliesslich auf Deutsch. Schreibe keine englischen Woerter, ausser sie wurden klar gesprochen. Fachbegriffe: RedMic Dictate, Windows, Alt, Shift, Zwischenablage, Transkription, Mikrofon, Codex, OpenAI."
whisper_no_fallback = true
whisper_suppress_non_speech = true
whisper_server_max_age_seconds = 14400
model = "auto"
selected_model = "base"
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
recording_overlay = true
taskbar_recording_overlay = true
keep_transcript_clipboard = true
beep_feedback = false
tray_notifications = true
transcript_cleanup = "clipboard"
cleanup_model = "llama3.2:3b"
cleanup_keep_alive = "30m"
tracking_enabled = true
tracking_retention_days = 14
tracking_include_transcript_text = false
tracking_transcript_preview_chars = 0
backend = "openai_realtime"
cloud_fallback = "local_whispercpp"
openai_api_key_env = "OPENAI_API_KEY"
openai_realtime_session_model = "gpt-realtime"
openai_realtime_transcription_model = "gpt-4o-mini-transcribe"
openai_realtime_fallback_model = "gpt-4o-transcribe"
openai_realtime_commit_seconds = 3.0
openai_realtime_finish_timeout_seconds = 7.0
```

Wenn du Hotkeys aenderst, danach die App im Tray beenden und mit
`.\scripts\start.ps1` neu starten.

## OpenAI API-Key

Fuer den Realtime-Modus muss der Key als Windows-Umgebungsvariable gesetzt sein:

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "DEIN_OPENAI_API_KEY", "User")
```

Danach RedMic neu starten. Den Key nicht in die Config-Datei und nicht in Git
schreiben. Ohne Key startet RedMic weiter und nutzt automatisch den lokalen
Fallback.

## Modell und Geschwindigkeit

Standard ist jetzt OpenAI Realtime mit `gpt-4o-mini-transcribe`, weil die lokale
CPU-Transkription auf diesem Rechner zwar privat, aber fuer laengere Diktate zu
langsam und zu wechselhaft war. Wenn die Mini-Qualitaet nicht reicht, kann in
der Config `openai_realtime_transcription_model = "gpt-4o-transcribe"` gesetzt
werden.

Der lokale Fallback bleibt installiert. Lokal ist `base` der schnelle Pfad,
weil es auf CPU deutlich schneller als `small` ist. Im lokalen Test brauchte
`base` fuer 15 Sekunden Audio rund 5 Sekunden, `small` rund 14 Sekunden.
`small` bleibt die Option fuer hoehere lokale Qualitaet, wenn Wartezeit weniger
wichtig ist.

RedMic transkribiert waehrend der Aufnahme alle 5 Sekunden einen Audio-Chunk mit
`base` im Hintergrund. Zusaetzlich werden fertige 10-Sekunden-Gruppen parallel
mit `small` verarbeitet, aber erst nachdem die passenden `base`-Chunks schon
fertig sind. Wenn die schnelle Warteschlange Rueckstand hat, wird der
`small`-Block uebersprungen. Wenn ein `small`-Block rechtzeitig fertig ist,
ersetzt er die zwei schnellen `base`-Teile. Wenn du `Space` drueckst, bekommt
die laufende `small`-Qualitaetsverarbeitung standardmaessig noch 7 Sekunden
Zeit. Wenn sie in diesem Fenster fertig wird, wird der bessere Text verwendet.
Wenn nicht, wird der vorhandene `base`-Text sofort genutzt.

Bei laengeren Diktaten mit schwacher `small`-Abdeckung startet RedMic danach
einen Quality-Guard im Hintergrund. Dabei wird die behaltene Audiofassung noch
einmal mit `small` verarbeitet. Der schnelle Text bleibt sofort verfuegbar; die
bessere Fassung wird spaeter automatisch in die Zwischenablage gelegt, wenn sie
brauchbar ist.

Der schnelle `base`-Whisper-Server wird beim Start der App im Hintergrund
vorgeladen. Dadurch muss ein kurzes Diktat nach `Space` nicht erst das Modell
laden.

Zur Qualitaetsstabilisierung erzwingt RedMic Deutsch zusaetzlich mit einem
festen Diktat-Prompt, deaktiviert Whisper-Temperatur-Fallbacks und unterdrueckt
Nicht-Sprach-Tokens wie Musik-Hinweise. Lang laufende Whisper-Server werden
regelmaessig neu gestartet, damit Standby- oder Uebernacht-Laeufe nicht
traege werden.

Die lokale LLM-Nachkorrektur laeuft standardmaessig nur bei `Alt+Shift+Y`,
also fuer die Zwischenablage. `Alt+Y` ist im stabilen Standard ein
Direkt-Diktat: aufnehmen, stoppen, dann einmal transkribieren und einfuegen.
Das vermeidet Live-Einfuegen waehrend du noch sprichst, nutzt aber trotzdem
Hintergrund-Chunking fuer kuerzere Wartezeit nach dem Stoppen.

Fuer einen automatischen Modellvergleich:

```powershell
.\scripts\benchmark.ps1 -RecordSeconds 8
```

Das Tool testet `tiny`, `base` und `small`. Fuer deutsche Diktate ist `base`
meist der bessere Kompromiss als `tiny`.

## Diagnose und 24h-Tracking

RedMic schreibt lokale Diagnose-Events nach:

```text
C:\Users\AE\.redmic_dictate\logs\events-YYYY-MM-DD.jsonl
```

Erfasst werden Session-Start und -Ende, Statuswechsel, Chunk-Fortschritt,
Transkriptionszeiten, Quality-Bloecke, Abbrueche, Fehler und Ausgabe-Metadaten.
Vollstaendige diktierte Texte und Audiodateien werden standardmaessig nicht
gespeichert. Stattdessen werden Laengen, Wortzahlen und kurze Hashes erfasst,
damit man Laeufe vergleichen kann, ohne den Inhalt offenzulegen.

Auswertung der letzten 24 Stunden:

```powershell
.\.venv\Scripts\python.exe -m voicely_alt diagnostics --hours 24 --write
```

Der Bericht wird zusaetzlich als Markdown-Datei im Log-Ordner abgelegt.

## Fehlerbehebung

- Es passiert nichts beim Hotkey:
  Starte die Aufnahme testweise ueber das Tray-Menue. Wenn das funktioniert, ist
  der Hotkey durch Windows oder eine andere App belegt.
- Es erscheint kein rotes Mikrofon:
  Pruefe, ob `recording_overlay = true` gesetzt ist.
- Der Text wird nicht eingefuegt:
  Stelle sicher, dass vorher ein Eingabefeld fokussiert war. Der fertige Text
  bleibt trotzdem in der Zwischenablage und kann mit `Ctrl+V` eingefuegt werden.
- Die Zwischenablage enthaelt keinen Text:
  Sprich lauter oder laenger. Sehr leise Aufnahmen werden absichtlich ignoriert,
  damit Whisper keine Halluzinationen aus Stille erzeugt.
- Lokale Transkription startet nicht:
  Fuehre `.\scripts\setup.ps1 -Model base` erneut aus.
