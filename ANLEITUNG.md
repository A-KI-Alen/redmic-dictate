# RedMic Dictate Anleitung

RedMic Dictate ist ein lokales Diktier-Tool fuer Windows. Es laeuft im
Hintergrund, nimmt per Tastenkombination Sprache auf, transkribiert lokal mit
`whisper.cpp` und schreibt den Text entweder direkt in das aktive Eingabefeld
oder legt ihn in die Zwischenablage. Fuer die Zwischenablage kann der Text
zusaetzlich lokal mit Ollama und `llama3.2:3b` nachkorrigiert werden.

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
dieser Ring. Jeder fertige Text bleibt zusaetzlich in der Zwischenablage, damit
du ihn bei Bedarf mit `Ctrl+V` oder `Windows+V` wieder einfuegen kannst. Wenn
ein Text erfolgreich in der Zwischenablage liegt, spielt das Tool ein dezentes
Glockensignal.

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

Autostart wieder entfernen:

```powershell
.\scripts\remove_autostart.ps1
```

## Nutzung

1. Klicke in das Eingabefeld, in das Text geschrieben werden soll.
2. Druecke `Alt+Y`.
3. Sprich den Text.
4. Druecke `Space`, um zu stoppen.

Der fertige Text wird eingefuegt und bleibt gleichzeitig als Sicherung in der
Zwischenablage.

Fuer die Zwischenablage:

1. Druecke `Alt+Shift+Y`.
2. Sprich den Text.
3. Druecke `Space`.
4. Nach dem Glockensignal liegt der Text in der Zwischenablage und kann mit
   `Ctrl+V` eingefuegt werden.

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
model = "auto"
selected_model = "small"
live_streaming = false
live_chunk_seconds = 4
background_chunking = true
background_chunk_seconds = 15
recording_overlay = true
taskbar_recording_overlay = true
keep_transcript_clipboard = true
beep_feedback = true
tray_notifications = true
transcript_cleanup = "clipboard"
cleanup_model = "llama3.2:3b"
cleanup_keep_alive = "30m"
```

Wenn du Hotkeys aenderst, danach die App im Tray beenden und mit
`.\scripts\start.ps1` neu starten.

## Modell und Geschwindigkeit

Standard fuer bessere Qualitaet ist jetzt `small`. Es ist langsamer als `base`,
erkennt aber deutsche Diktate meist sauberer.

RedMic transkribiert waehrend der Aufnahme alle 15 Sekunden einen Audio-Chunk im
Hintergrund. Nach `Space` muss dadurch nur noch der letzte Rest verarbeitet und
alles zusammengesetzt werden.

Die lokale LLM-Nachkorrektur laeuft standardmaessig nur bei `Alt+Shift+Y`,
also fuer die Zwischenablage. `Alt+Y` ist im stabilen Standard ein
Direkt-Diktat: aufnehmen, stoppen, dann einmal transkribieren und einfuegen.
Das vermeidet Live-Einfuegen waehrend du noch sprichst, nutzt aber trotzdem
Hintergrund-Chunking fuer kuerzere Wartezeit nach dem Stoppen.

Fuer einen automatischen Modellvergleich:

```powershell
.\scripts\benchmark.ps1 -RecordSeconds 8
```

Das Tool testet `tiny`, `base` und `small` und speichert das schnellste
funktionierende Modell.

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
