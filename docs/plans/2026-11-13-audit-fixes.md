# Plan: Befunde aus dem Audit vom 13.11.2026 beheben

Grundlage: [docs/audits/2026-11-13-audit-3.0.1.md](../audits/2026-11-13-audit-3.0.1.md).
Reihenfolge wie im Fahrplan des Audits. Je Paket: Test zuerst, kleinste
Änderung, danach die ganze Suite. Kein Commit, kein Push, kein Versions-Tag;
die Version bleibt 3.0.1, bis alles fertig und getestet ist (OPS-16 ist der
letzte Schritt).

Status: ☐ offen · ◐ in Arbeit · ☑ erledigt · ✗ nicht in diesem Repo lösbar

## Phase 1 – vor dem Push

| Paket | Befunde | Dateien | Status |
|---|---|---|---|
| 1.1 | DEP-07 `certifi` | `uv.lock`, `requirements.txt` | ☑ |
| 1.2 | P-08, P-09 Regexe | `metadata_generation.py`, `utils/dates_numbers.py`, `llm_values.py` | ☑ |
| 1.3 | L-14 leer = nicht senden, CORS | `config.py`, Tests, Doku | ☑ |
| 1.4 | L-16, L-22, L-23, L-25, S-23, S-21 (Downloads) | `input_source_service.py`, `request_id.py`, `config.py`, `main.py`, `logging_setup.py`, `metrics.py`, `infra.py`, `screenshot_service.py` | ☑ |
| 1.5 | L-15, OPS-20 Geocoder-Fehler, Alarm | `geocoding_service.py`, `metadata_service.py`, `alerts.yaml`, `alerts.test.yaml` | ☑ |
| 1.6 | S-24 Startwarnung | `config.py` | ☑ |
| 1.7 | DOC-06, DOC-07, DOC-08, DOC-09, OPS-17, OPS-18 | `UEBERGABE.md`, `DEPLOYMENT.md`, `docs/b-api-kurzinfo.md`, `README.md`, `INSTALL.md`, Chart-README | ☑ |
| 1.8 | T-01 Tests für die Randfälle | `tests/` | ☑ |

## Phase 2 – vor dem ersten Deployment

| Paket | Befunde | Status |
|---|---|---|
| 2.1 | OPS-19 `/debug/dependencies` mit Schlüssel und Geocoder | ☑ |
| 2.2 | S-20 Korrekturblock begrenzen, S-21 Größengrenze im Browser | ☑ |
| 2.3 | L-17 abgeschnittene LLM-Antworten | ☑ |
| 2.4 | API-13 Schema-Marker in den Schreibpfaden | ☑ |
| 2.5 | OPS-22 Tag gegen Version, OPS-23 ruff, OPS-25 Chart-Robustheit | ☑ |
| 2.6 | Proxy-Pfade mit Nutzer- und Gast-Schlüssel, S-25, OPS-07, OPS-21 | ✗ braucht Staging, B-API-Team, Cluster |

## Phase 3 – Verbesserung

| Paket | Befunde | Status |
|---|---|---|
| 3.1 | Webkomponente W-LOG-01 bis 04, 06 bis 10, W-PERF-01, W-ARCH-01 (Button-Typen, Konsole), W-SEC-01, W-DOC-01, Anzeige von `processing.warnings` | ☑ (618 Specs; im Browser am gebauten Bundle geprüft). W-LOG-05 nur dokumentiert; W-TEST-01, W-ARCH-02, W-PERF-02, W-A11Y-01 offen |
| 3.2 | P-10, P-11, P-12 (Mechanik da, Schwelle erst nach Lastmessung), L-18 | ☑ |
| 3.3 | API-14, API-15 (Optionen, Pfadparameter, Body-Schema; Antwortbeispiele offen), L-19, L-20, L-21, L-24, P-13 (doppeltes Parsen; Geocoder-Client und Regex-Pässe bewusst gelassen) | ☑ |

## Phase 4 – laufend

| Paket | Befunde | Status |
|---|---|---|
| 4.1 | S-22 Textformat, OPS-24 (a; b–d nicht nachprüfbar), OPS-26, DEP-05, DOC-10 bis DOC-12 | ☑ |
| 4.2 | M-04 (toter Code entfernt; die drei Vokabular-Abgleiche bleiben, siehe unten), A-02 (Eingangsquellen-Block als `InputSourceRequest`; Aufteilen der großen Module offen), W-ARCH-02, W-PERF-02, W-A11Y-01, W-SEC-01, W-LOG-10, W-DOC-01 | ◐ Backend ☑, Widget siehe 3.1 |
| 4.3 | DEP-06 geplante Pipeline, DEP-08 Vitest, Leitfaden (Chart-Pfad, OpenTelemetry) | ☐ eigene Arbeitspakete |

## Zum Schluss

- OPS-16: Version anheben (Vorschlag 3.1.0), CHANGELOG datieren – auf Signal.
- ☑ Widget neu gebaut und in `src/static/widget` ausgeliefert (13.11., Bundle
  `main.104b9bb5…`, i18n-Dateien dabei).
- Vor dem Tag testen: Erschließung und Upload gegen Staging mit echtem
  Schlüssel; Einbettung in edu-sharing (weniger `metadataChange`, `@type`
  und `null` im Datensatz, `schemaVersion` als `v2.0.0`).

## Bewusst nicht umgesetzt (Phase 4.2)

- **M-04, ein Vokabular-Abgleich:** Die drei Abgleiche unterscheiden sich mit
  Absicht (Werte des Aufrufers exakt wie in 2.1.1, Antworten des Modells mit
  Fuzzy-Grenze, Validierung streng). Zusammenlegen ändert Ergebnisse und
  braucht vorher Charakterisierungstests je Pfad – eigenes Arbeitspaket.
- **A-02, große Module teilen:** `schemas.py` (1.650 Zeilen, überwiegend
  OpenAPI-Texte), `repository_service.py` (1.270) und `generate.py` (670)
  bleiben ungeteilt. Reines Verschieben ohne Nutzen für den Betrieb, vor einer
  Auslieferung nicht angezeigt.
