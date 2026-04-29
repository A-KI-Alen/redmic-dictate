# RedMic Dictate Lastenheft

## Ziel

RedMic Dictate ist ein Windows-Diktat-Tool, das stabil im Hintergrund laeuft,
per Hotkey Sprache aufnimmt, lokal transkribiert und den Text entweder in das
vorher aktive Eingabefeld einfuegt oder in die Zwischenablage legt.

## Stabilitaetsprinzipien

- Der Hotkey-Prozess darf nicht durch Audio, Whisper, Ollama oder Overlay
  blockieren.
- Jede Aufnahme hat eine Session-ID. Ergebnisse aus alten oder abgebrochenen
  Sessions duerfen niemals eingefuegt werden.
- `Space+Esc` ist der harte Abbruch in jedem aktiven Zustand.
- Das Overlay ist passiv. Es darf Aufnahme, Transkription oder Hotkeys nicht
  steuern und nicht blockieren.
- Standard ist stabiler Direktmodus: aufnehmen, stoppen, einmal transkribieren.
  Live-Streaming ist nur ein spaeteres Feature-Flag.

## Tastatur

- `Alt+Y`: Direkt-Diktat in das aktive Eingabefeld.
- `Alt+Shift+Y`: Diktat in die Zwischenablage.
- `Space`: Aufnahme stoppen.
- `Esc`: Aufnahme abbrechen.
- `Space+Esc`: harter Abbruch, auch waehrend Transkription oder LLM-Korrektur.

## Hard-Abbruch

`Space+Esc` muss:

- Aufnahme stoppen oder verwerfen.
- Laufende Live-/Worker-Session ungueltig machen.
- Whisper/Ollama-Verbindungen schliessen, damit blockierende Requests abbrechen.
- Overlay beenden.
- Temporäre Audiodateien nach Moeglichkeit loeschen.
- Nichts einfuegen und nichts in die Zwischenablage schreiben.
- Den Zustand wieder auf `idle` setzen.

## Sichtbares Feedback

- Rote Linie ueber der Taskleiste waehrend Aufnahme oder Verarbeitung.
- Roter Maus-Indikator als Hinweis auf aktive Aufnahme.
- Statusfeld oben links mit aktuellen Tastenkombinationen.
- Verarbeitungszustand sichtbar, damit Wartezeit nach dem Stoppen erkennbar ist.

## Akzeptanztests

- 20 mal `Alt+Y`, sprechen, `Space`: kein Haenger, Text wird eingefuegt.
- 20 mal `Alt+Shift+Y`, sprechen, `Space`: kein Haenger, Text liegt in der
  Zwischenablage.
- `Space+Esc` waehrend Aufnahme: sofort idle, kein Text.
- `Space+Esc` waehrend Transkription: Verarbeitung bricht ab, kein Text.
- Overlay bleibt sichtbar, blockiert aber nicht die Bedienbarkeit des Systems.
