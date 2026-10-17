# Architektur-Map

Überblick über Metadata Agent 2.1: Webkomponente, B-API-Proxy, Agent,
LLM-Anbindung, angebundene Dienste und Betrieb.

| Datei | Zweck |
|---|---|
| `metadata-agent-architektur.png` | Bild zum Einfügen in Dokumente (3220×1276, weißer Hintergrund) |
| `metadata-agent-architektur.svg` | Dasselbe als Vektorgrafik, Schrift eingebettet |
| `metadata-agent-architektur.html` | Interaktive Ansicht mit Erläuterungen je Komponente, im Browser öffnen |
| `metadata-agent-architektur.json` | Quelle der Map (Archify-Format, Typ `architecture`) |
| `metadata-agent-ablauf.png`, `.svg`, `.html` | Anfrageablauf über die B-API: Schlüssel, Tracing-Header, Timeouts, Speichern mit Service-Account |
| `metadata-agent-ablauf.json` | Quelle des Ablaufs (Archify-Format, Typ `sequence`) |
| `*.finalize*.json`, `*.browser-check.json`, `*.delivery.json` | Prüfbelege von Archify (Schema, Darstellung im Browser) |

## Ändern

1. Komponenten, Verbindungen und Texte in `metadata-agent-architektur.json`
   anpassen.
2. HTML neu erzeugen und prüfen (Archify-Skill, Version 3.x):

   ```bash
   node <archify>/bin/archify.mjs finalize architecture metadata-agent-architektur.json metadata-agent-architektur.html --quality showcase --json
   node <archify>/bin/archify.mjs finalize sequence metadata-agent-ablauf.json metadata-agent-ablauf.html --quality showcase --json
   ```

3. PNG und SVG neu exportieren: Die HTML-Datei im Browser im hellen Modus
   öffnen und das Diagramm-SVG (`svg[data-quality-profile]`) exportieren. Für
   das PNG wurde es mit eingebetteten Stilen und Schriften auf weißem
   Hintergrund in doppelter Größe gerendert.

Die Map beschreibt das System, nicht einzelne Commits; sie verweist nicht auf
Quelldateien.
