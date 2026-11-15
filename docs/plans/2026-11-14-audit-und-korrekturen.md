# Audit vom 14.11.2026 und Korrekturen – Arbeitsplan

Stand: Metadata Agent 3.0.1 mit den unveröffentlichten Änderungen vom 13./14.11.
(CHANGELOG „Unveröffentlicht“), Webkomponente 3.0.1 mit den Korrekturen vom 13.11.
Nichts committet, keine Versionsanhebung vor dem Test.

Ablauf: (1) bekannte Problemstellen festhalten, (2) vollständiger Audit beider
Repos, Bericht in `docs/audits/2026-11-14-audit-3.0.1.md`, (3) alle bestätigten
Befunde beheben, je Befund Test zuerst, (4) Gesamtprüfung.

## 1. Vor dem Audit bekannte Problemstellen

Beim Schreiben der neuen Doku (14.11.) und in der Korrekturrunde vom 13.11.
aufgefallen, noch nicht behoben. Der Audit prüft jede Stelle nach.

### Agent (Backend)

| Nr. | Stelle | Problem |
|---|---|---|
| K-01 | `src/api/responses.py` (422-Handler) | `/extract-field`, `/detect-content-type`, `/node`, `/screenshot`: die 422-Antwort enthält den gesendeten Body samt Text (`input` aus `exc.errors()`); die übrigen Endpunkte vermeiden das |
| K-02 | dieselben vier Endpunkte | `NaN`/`Infinity` im Body werden nicht abgelehnt (die Korrektur vom 13.11. greift nur bei den Endpunkten, die ihren Body selbst lesen) |
| K-03 | `src/api/input_resolver.py`, `metadata_generation.py` | `/generate`: Kopfdaten in `existing_metadata` (`contextName`, `processing` …) laufen als Felder mit; das alte `processing` gerät in den Korrektur-Prompt |
| K-04 | `src/config.py`, Screenshot-Aufrufer | `METADATA_AGENT_SCREENSHOT_METHOD` wird gelesen, wirkt aber nicht |
| K-05 | `src/config.py`, `schemata.py` | `METADATA_AGENT_DEFAULT_CONTEXT` wird nur in `/info/schemata` angezeigt; Anfragen nehmen fest `default` |
| K-06 | `schema_loader.py`, `metadata_export.py` | Schemata haben kein eigenes Label: `/info/schemas` nennt den Dateinamen, `/export/markdown` titelt `# event.json` |
| K-07 | `src/api/input_resolver.py` | Zweig „Korrekturanweisung zu lang“ (400) ist von `/generate` aus nicht erreichbar |
| K-08 | `/validate`, `/screenshot` | Uneinheitlich: `/validate` antwortet bei unbekanntem Kontext/Schema `200 valid:false`, `/generate` `404`; `/screenshot` mit `node_id` ohne Zugangsdaten `200`, die anderen Schreibpfade `503` |
| K-09 | `/detect-content-type` | 404 bei unbekanntem Kontext nennt „version '1.8.1'“ |
| K-10 | `helm/metadata-agent/values.yaml` | `image.tag: v3.0.1`, das Chart braucht aber ein Image aus dem neuen Stand (OPS-16, löst die Versionsanhebung) |
| K-11 | `tests/test_compose.py` | Seit der Umstellung auf zwei Sprachen prüft der Test nur noch den Wert 320, nicht mehr, dass jedes `docker run`-Beispiel die Option trägt |
| K-12 | Tests | 36 Browser-Tests laufen lokal nicht (Chromium fehlt in der `.venv`); Docstrings zitieren die alte README |
| K-13 | offen aus dem 13.11. | M-04 (drei Vokabular-Abgleiche), A-02 (große Module), P-13 (Geocoder-Client), Antwortbeispiele in der OpenAPI, OPS-25c, OPS-24 b–d, DEP-06 |

### Webkomponente

| Nr. | Stelle | Problem |
|---|---|---|
| K-20 | Export (`canvas-metadata.ts`, `shape-expander`) | Jedes unberührte Objektfeld exportiert `[]`; Objekte tragen leere Container wie `address: {}` |
| K-21 | `field.component`, Presets | `showActions` und `style.compact` haben keine Wirkung |
| K-22 | `canvas.component.ts` | W-LOG-05: `contextName`, `schemaVersion`, `apiUrl` nach dem Einhängen laden nichts neu (nur dokumentiert) |
| K-23 | Layouts | Hinweisbanner nur im Standard-Layout schließbar; kein „Abbrechen“ für eine laufende Erschließung |
| K-24 | `canvas-feedback.ts` | 4xx ohne JSON zeigt Angulars Meldung samt Adresse |
| K-25 | `polyfills` | Zone-Flags (`PASSIVE_EVENTS`, `UNPATCHED_EVENTS`) können für die ganze Host-Seite gelten |
| K-26 | Tests | W-TEST-01: Specs testen die Komponente über TestBed, nicht das registrierte Custom Element |
| K-27 | offen aus dem 13.11. | W-ARCH-02, W-PERF-02, W-A11Y-01, DEP-08 (Vitest) |

## 2. Audit

Abgeschlossen am 14.11.: `docs/audits/2026-11-14-audit-3.0.1.md`. Sieben Bereiche
(R Repository-Pfad, S API/Sicherheit, L Erschließungslogik, O Betrieb, Q
Tests/Doku/Architektur, U Widget-Oberfläche, W Widget-Kernlogik). Gesamtwert 70,
kein kritischer, neun hohe Befunde. Urteil: bedingt auslieferbar.

## 3. Korrekturen

**Umfang (Vorgabe vom 14.11.): alle Befunde, auch die mittleren und kleinen**,
einschließlich der Listen „Kleinigkeiten“ (R-N, S-N, L-N, O-N, Q-N, U-N, W-N).
Was sich hier nicht sicher beheben lässt, wird mit Grund gemeldet, nicht
stillschweigend ausgelassen. Nach den Strängen folgt ein Abgleich jeder ID.

Regeln für alle Stränge: je Befund zuerst ein fehlschlagender Test, dann die
kleinste Änderung; getrennte Dateien je Strang; nichts committen, keine
Versionsanhebung. Regeln im Wortlaut: Scratchpad `audit2/FIX-RULES.md` und
`FIX-RULES-WIDGET.md`.

| Strang | Dateien | Befunde | Stand |
|---|---|---|---|
| A – API und Sicherheit | `src/api/**`, `models/schemas.py`, `utils/{loggable,schema_loader,request_id}.py`, `screenshot_service.py`, `metadata_validation.py`, `metadata_export.py` | S-30 bis S-43, L-33, L-37, R-10, Q-01, Q-06 (Text), Q-08, Q-11, Q-12, Q-17 | erledigt – alle Befunde; S-30, S-39 und S-41 anders als vorgeschlagen (siehe unten) |
| B – Erschließungslogik | `services/llm_*.py`, `field_normalizer.py`, `output_normalizer.py`, `geocoding_service.py`, `metadata_generation.py`, `metadata_service.py`, `utils/{dates_numbers,vocabulary}.py` | L-30 bis L-32, L-34 (M-04), L-35, L-36, L-38, L-40 bis L-46, S-35, Q-13, Q-15, Q-16 | erledigt – alle Befunde samt L-N; ein Abgleich für Vokabulare (`services/vocabulary_matching.py`) |
| C – Repository-Pfad | `services/repository_*.py`, `input_source_service.py`, `models/repository_rules.py` | R-01 bis R-09, R-11 bis R-14, Q-03, Q-06 (Docstring) | erledigt – alle Befunde samt R-N; zehn neue Testdateien für den Schreibweg |
| D – Betrieb | `config.py`, `metrics.py`, `main.py`, `infra.py`, Logging, Dockerfile, Compose, Chart, CI, Skripte, NOTICE | O-31 bis O-46, Q-04, Q-05 | erledigt – alle Punkte samt O-N bearbeitet, fünf Teilpunkte mit Grund offen (siehe unten); O-34 vollständig erst im Integrationslauf (Zeitstempel-Metriken) |
| W1 – Widget-Kernlogik | `src/app/core/**`, `canvas.component.ts`, `field.component.ts` (Logik), `main.ts` | W-30 bis W-50, U-08, K-25 | erledigt – 875 Specs grün |
| W2 – Widget-Oberfläche | Layouts, gemeinsame Komponenten, Styles, i18n | U-01 bis U-07, U-09, U-12 bis U-15, U-21 bis U-25, W-51 | erledigt – 1215 Specs grün; vier gemeinsame Bausteine statt sieben Kopien, Fortschritt mit „Abbrechen“, Eingang `language`, Texte im Bundle, Zeitwähler von Material |
| W3 – Widget Build, Tests, Doku | `scripts/`, `package*.json`, CI, README, Specs | U-10, U-11, U-16 bis U-20 | erledigt – 1281 Specs grün; ESLint, `build.json`, README neu in beiden Sprachen, Patch-Update auf Angular 21.2.26 (kein hoher Befund mehr im Dev-Audit), Bundle 20 % kleiner |
| E – Doku und Abschluss | Dokumente beider Sprachen, CHANGELOG, Steckbrief | Doku-Folgen aller Stränge, L-39, L-44, Q-14, Q-17 | erledigt – Dokumente beider Sprachen und CHANGELOG nachgezogen; L-39 und L-44 mit Strang A, Q-14, Q-17 und L-34 d/e im Integrationslauf nach A–D |
| F – Architektur | große Module | Q-07, Q-09, Q-10, Q-18 | erledigt – Module geteilt, ein Settings-Objekt, `write_node` und `GenerationOptions`; OpenAPI unverändert; 4425 Tests bestanden, 29 übersprungen, Abdeckung 93,96 % |

### Mit Grund nicht umgesetzt

- **O-43, uv-Version nicht angehoben** (weiter 0.7.13): ohne Netz nicht
  prüfbar. Ein Test hält die Version in Image und beiden CIs gleich.
- **apt-Pakete und Browser-Download nicht per Hash festgelegt:** braucht Netz
  und einen Image-Build.
- **SBOM auf GitHub nur als Zeile in der Job-Zusammenfassung,** nicht als
  Artefakt: dafür muss `actions/upload-artifact` auf einen Commit festgelegt
  werden. In GitLab ist `sbom.cdx.json` ein Artefakt.
- **kubeconform, Standard-Schemata nicht auf einen Commit festgelegt**
  (`-schema-location default`): der Commit fehlt. Der CRD-Katalog ist
  festgelegt.
- **Chart bricht ohne die CRDs des Prometheus Operators nicht mit eigener
  Meldung ab:** `.Capabilities.APIVersions.Has` ist auch ohne Cluster falsch
  und würde `helm template`, `helm lint` und die CI scheitern lassen.
  Stattdessen steht der Hinweis in `values.yaml` und in der Doku.
- **Compose ohne `init: true`:** `tini` ist schon ENTRYPOINT des Images.
- **S-39:** Der `400` „Korrekturanweisung zu lang“ ist nicht entfernt, sondern
  vor den Abruf der Quelle gezogen. Sonst ginge bei URL- und Node-Quelle eine
  unbegrenzte Anweisung in jeden Prompt.
- **S-41:** Für die Antwort auf einen POST im Screenshot-Browser gibt es keine
  harte Grenze. Geprüft wird die tatsächliche Länge; gelesen hat der
  Browser-Treiber die Antwort dann schon.
- **S-30:** Die Grenze von 20 Befunden gilt je Feld und Prüfung, nicht je
  Feld; die Grenze von 500 je Liste gilt.
- **Q-07, Screenshot-Hilfen von `/generate` und `/upload` nicht
  zusammengelegt:** sie tun Verschiedenes.
- **Q-09, Rest:** Ein Service, der vor dem Wechsel des Settings-Objekts gebaut
  wurde, behält, was er kopiert hat. Das betrifft nur Tests.
- **Textbereinigung:** Bereinigt werden die ersten 600.000 Zeichen, der Rest
  bleibt unbereinigt angehängt, damit die Kürzungswarnung die echte Länge
  nennt. Schrumpft die Bereinigung diesen Teil unter 150.000 Zeichen, erreicht
  unbereinigter Text das Modell; bewusst so belassen.
- **Folgearbeit Repository-Pfad:** `ccm:lifecyclecontributer_authorFN` beim
  Lesen eines Nodes auf `cm:author` zurückführen. Die Standardversion 4.0 für
  CC-Lizenzen bleibt (dokumentiert, per Test festgehalten).

### Nur auf Staging, im Cluster oder in edu-sharing prüfbar

- **Image:** Build mit `playwright install --only-shell chromium`, `tini` als
  PID 1 und der erweiterte Smoke-Test.
- **CI:** Der Pipeline-Schedule in GitLab muss in der Oberfläche angelegt
  werden (braucht `git` im `$DIND_IMAGE`); der geplante Workflow auf GitHub
  setzt voraus, dass sich das Release-Image ohne Anmeldung ziehen lässt.
  kubeconform lief hier nicht.
- **Cluster:** `listenHost: "::"` lauscht nur auf IPv6. Die drei kritischen
  Alarmregeln brauchen übereinstimmende Uhren von Pod und Prometheus (deutlich
  unter 10 Minuten); während eines Rollouts hat ein Pod des älteren Images die
  Zeitstempel-Metriken nicht. Ob Prometheus Metriken und Regeln vom neuen
  Service `<name>-internal` übernimmt.
- **edu-sharing, Schreibweg:** ob das Repository beim Anlegen selbst
  Properties setzt. Dann findet die Suche nach einem verlorenen Node nichts;
  das ist die sichere Richtung.
- **edu-sharing, Webkomponente (W1):** Wiedereinhängen des Elements;
  `contentType` im Datensatz samt `metadataset: "core.json"`; das synchrone
  `metadataChange` im Handler von edu-sharing ohne Rückkopplung; Hosts, die
  Datensätze aus Events zusammenführen, müssen einen fehlenden Schlüssel als
  „kein Wert“ lesen; die längeren Formulare (`person.json`,
  `organization.json`); die Zone-Einstellungen nach einem Build (K-25).

Offen nach Entscheidung: O-30 (Versionsanhebung erst nach dem Test). Nicht hier
prüfbar (Staging, Cluster, edu-sharing): die mit „n. v.“ markierten Punkte.

## 4. Abschluss (14.11.)

- Bundle neu gebaut und in `src/static/widget` ausgeliefert (`build.json`,
  Stempel durch `tests/test_widget_bundle_stamp.py` geprüft).
- Schlussläufe: Agent 4557 bestanden, 29 übersprungen, Abdeckung 94,0 %; ruff,
  mypy, `helm lint`, promtool sauber. Webkomponente 1281 Specs, tsc, ESLint ohne
  Fehler.
- Im Browser am ausgelieferten Bundle geprüft (siehe Auditbericht, „Stand der
  Behebung“).
- Offen: die Punkte unter „Nur auf Staging, im Cluster oder in edu-sharing
  prüfbar“.
- **15.11.:** Versionsanhebung (O-30) erledigt: Agent und Webkomponente tragen
  4.0.0, auf GitHub getaggt (`v4.0.0`). Davor am selben Tag: Cache-Haltepunkt,
  Vorlauf parallel zur Typ-Erkennung, Metrik `cache_write`; die Grenze von 20
  gleichzeitigen Aufrufen bleibt. Schlussläufe: Agent 4579 bestanden, 29
  übersprungen, Abdeckung 94,00 %; Webkomponente 1281 Specs.
