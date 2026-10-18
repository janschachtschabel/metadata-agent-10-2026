# Plan: Befunde aus dem Audit vom 18.10. beheben

Quelle: [../audits/2026-10-18-audit-3.0.0-nachkorrektur.md](../audits/2026-10-18-audit-3.0.0-nachkorrektur.md).
Regeln: Test zuerst (rot, dann grün). Kleinste Änderung. Keine Commits. Keine
B-API-Routen anlegen. Keys nie ausgeben. Angular bleibt 21. Downloads nur für
Dev-Pakete des Widgets (`npm audit fix` ohne `--force`, `npm dedupe`).

Entscheidungen des Nutzers (18.10.):
- **OPS-07:** Der Nutzer installiert selbst. Wir liefern eine Prüfliste in DEPLOYMENT.md.
- **DEP-04:** Lizenz Apache-2.0 für beide Repos. LICENSE und NOTICE mit Drittkomponenten.
- **DEP-03:** Dev-Pakete aktualisieren erlaubt.

## Stränge

Drei Stränge laufen parallel und berühren getrennte Dateien.

### A – Backend-Code (`metadata-agent-3/src`, `tests/`)

**A1 Vokabular und Werte**
- L-07: Eine Liste, die nach dem Entfernen von `None` leer ist oder `None` enthielt, ist ungültig; dann Retry.
- L-08: Ein geschlossenes Vokabular ohne Treffer ergibt `None`.
- L-09: Eine Funktion `is_closed_vocabulary` (closed und skos) für `llm_values`, `field_normalizer` und `metadata_validation`.
- L-11: `NaN`, `inf` und „null“ werden nicht zu Zahlen.
- L-12: Markdown-Export mit `dict`-Werten.
- L-13: Korrektur nicht am Seitentext erkennen.
- L-14: „LETZTER VERSUCH“ nur beim letzten Versuch.
- L-10: ein Zeitformat.
- T-06: Tests für die Normalisierer und `save_node`.

**A2 Zeitgrenzen und Schreibpfade**
- D-04: Gesamt-Zeitlimit `METADATA_AGENT_UPLOAD_TIMEOUT` für `/upload` und `/nodes`.
- P-01-Rest: Deadline auch für detect und extract; die Screenshot-Wartezeit liegt innerhalb.
- D-01: Wird nach dem Anlegen abgebrochen, wird der Node gemeldet bzw. gelöscht.
- D-02: Gesamtlimit für den Feld-für-Feld-Fallback.
- D-05: Vorschau-Problem in den Teilproblemen von `/upload`.

**A3 Sicherheit**
- S-09: `route_web_socket`.
- S-10: Vorschau nur PNG, JPEG, GIF und WebP (Magic Bytes).
- S-06: neutrale Fehlertexte nach außen.
- S-12: CORS-Default und `nosniff`.
- S-08: Testseiten nicht ausliefern, `/health` ohne Details.
- S-04: Die Vorschau-Verbindung wird an die geprüfte IP festgenagelt.
- B-02: Weiterleitungen im Browser laufen über dieselbe Prüfung (route greift bei jeder Navigation; dokumentieren und testen).
- S-11: Sandbox als dokumentierte Entscheidung (lokal nicht prüfbar).
- S-13: Die akzeptierten Lücken stehen in der OpenAPI-Beschreibung.

**A4 API**
- API-03: ein Fehlermodell und `responses=` je Route.
- API-04: Den SVG-Text entfernen.
- API-05: Billige Prüfungen zuerst; Längengrenze für die Korrektur.

**A5 Performance**
- P-05: Levenshtein mit Vorfilter, ohne neue Abhängigkeit.
- P-02: Prompt-Cache auch für `b-api-router`.
- P-03: Lock nicht über `sleep` halten.
- P-04: ein gemeinsamer Repository-Client.
- P-06: Retries nur bei Transport- bzw. LLM-Fehlern.

**A6 Wartbarkeit**
- M-01-Rest: Funktionen über C901 15 zerlegen.
- M-02: ein Upload-Ablauf und ein Datum-/Zahl-Modul.
- M-03: toter Code (`geocode_location_string`, `geocode_locations`).
- M-05: Schichtrichtung.
- L-03: LRT-Lesart.
- L-04: Warnungen statt stiller `except`.
- L-05: eine Ausschlusskonstante.
- L-15: Kleinkram.

### B – Betrieb, CI, Helm, Doku (`helm/`, `.gitlab-ci.yml`, `.github/`, `docker-compose.yml`, `*.md`, `src/metrics.py`, `src/infra.py`, `src/config.py`, `tests/test_helm_chart.py`, `tests/test_ci_hygiene.py`, `tests/alerts.test.yaml`)

- OPS-05: `workflow:rules` und MR-Regeln für alle Prüfjobs.
- OPS-06: Alarm-Serien vorbelegen und ein promtool-Fall, der bei 1 beginnt.
- OPS-08: AcademicCloud-Drosselung je Pod und `NOTES.txt`.
- OPS-09: Login direkt vor dem Push, Digests, `permissions`.
- OPS-10: DNS nur zu kube-dns, B-API mit Port, IPv6 dokumentiert.
- OPS-11: `sizeLimit`, `topologySpreadConstraints`, Annotation `checksum/secret` ohne Klartext-Hash.
- OPS-12: `/ready` und eine Startwarnung ohne Zugangsdaten.
- OPS-13: `service!="other"`.
- OPS-14: Methoden-Label `OTHER`.
- OPS-15: compose mit `stop_grace_period`, `read_only` und Bind auf 127.0.0.1.
- OPS-07: Prüfliste in DEPLOYMENT.md.
- T-03: `REQUIRE_BROWSER`.
- T-04: Events statt `sleep`.
- T-05: Schichtentest.
- T-07: mypy erweitern; kubeconform in die CI.
- DOC-02 bis DOC-05.
- DEP-04: LICENSE und NOTICE (Backend).

### C – Widget (`metadata-agent-webcomponent-3`)

- L-01-Rest: Felder während der Extraktion sperren bzw. Nutzerwerte schützen.
- U-02, U-04, U-07, U-09 bis U-16.
- M-06: i18n.
- Widget-Teil von M-02 und M-03.
- Coverage-Schwelle in Karma.
- DEP-03: `npm audit fix` und `dedupe`; die CI prüft auch Dev-Pakete.
- DEP-04: LICENSE und NOTICE.

## Prüfung am Ende

- **Backend:** `uv run pytest --cov` (Untergrenze 79), `ruff check`, `ruff format --check`, `mypy`, `helm lint`, promtool.
- **Widget:** `tsc`, Karma, Build und Deploy nach `metadata-agent-3/src/static/widget`.
- **Danach:** ein Review mit frischem Kontext und ein Abschnitt „Stand der Behebung“ im Audit.
