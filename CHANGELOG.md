# Änderungen

## 4.0.1 — 2026-11-15 — Abhängigkeiten, Korrekturen aus den ersten CI-Läufen, Webkomponente 4.0.1

Schnittstellen und Einstellungen wie 4.0.0. Entstanden am Tag von 4.0.0: Erst
mit den öffentlichen Repositories liefen die Pipelines wirklich, und sie
zeigten drei Stellen, die lokal nicht aufgefallen waren.

- **Webkomponente 4.0.1:** In einem englischen Widget zeigte die
  Nur-Lese-Ansicht ein Datum mit deutschen Punkten, ein Datum mit Uhrzeit
  daneben mit Schrägstrichen; jetzt beide englisch. Sieben Specs der
  Webkomponente hingen an der Browser-Sprache und scheiterten im englischen
  Chromium der GitLab-Pipeline. Bundle neu gebaut und ausgeliefert
  (`dist/build.json`).

- **Python-Pakete** auf Stände, die mindestens sieben Tage alt sind: fastapi
  0.162.2, starlette 1.10.0, pydantic 2.14.3, pydantic-settings 2.16.2,
  playwright 1.64.1 (bringt das Chromium der Screenshots mit), pyee 14.0.0,
  anyio 4.16.0, httpx2 2.16.0, pytest 9.2.0, ruff 0.17.4, mypy 2.4.1 und
  kleinere. `uv.lock` und `requirements.txt` sind gemeinsam neu erzeugt.
  Der Dependabot-PR dazu passte in sich nicht zusammen: Seine
  `requirements.txt` verlangte `pyee==16.0.1`, Playwright 1.64.1 erlaubt aber
  nur `pyee<15` – der Image-Build über `requirements.txt` wäre gescheitert.
- **Basis-Image** `python:3.13-slim` auf den aktuellen Digest, im Dockerfile
  und in der GitLab-CI; `docker/login-action` 4.7.0; das Ruff-Image der
  GitLab-CI folgt der Lock-Datei (0.17.4).
- **Browser-Test an die Webkomponente 4.0.0 angepasst:**
  `test_loading_the_widget_scripts_twice_registers_the_element_once` erwartete
  noch die Registrierungs-Meldung in der Konsole, die das Widget seit 4.0.0
  nur im Debug-Modus schreibt. Geprüft wird jetzt die Version am registrierten
  Element. Aufgefallen, weil die Browser-Tests erstmals lokal liefen.
- **Tests:** 4608 bestanden, 1 übersprungen (manueller Netztest), Abdeckung
  95 %, einschließlich der Browser-Tests; `pip-audit` ohne Befund.
- **Übersetzungen des Widgets Byte für Byte im Repository:** `.gitattributes`
  schützte nur `src/static/widget/dist/**` vor der Umwandlung von
  Zeilenenden, `dist/build.json` stempelt aber auch `assets/i18n`. Git schrieb
  `de.json` und `en.json` beim Commit um, und der Stempel-Test schlug auf dem
  Linux-Runner fehl – der erste echte CI-Lauf von 4.0.0 hat das gezeigt. Jetzt
  gilt `-text` auch für `src/static/widget/assets/**`; ein Test prüft, dass
  jede gestempelte Datei geschützt ist.
- **Architektur-Map für 4.0.0:** `docs/architektur/metadata-agent-architektur-b-api.*`
  neu gezeichnet (Archify 3.2.0): Rahmen um den Pod des Agenten, interner
  Port als eigener Service, Versionen 4.0.0, Cache-Vorlauf und Haltepunkt in
  den Erläuterungen. Jede Komponente verweist auf ihre Stelle im Code am
  Commit `7eac5e1`.

## 4.0.0 — 2026-11-15 — Entwicklerleitfaden für Dienste, Audit-Korrekturen, schnellere Erschließung

> **Warum 4.0.0:** Mehrere Änderungen sind für Aufrufer und Betrieb sichtbar
> (Statuscodes, Helm-Service, Einstellungen, Verhalten der Webkomponente).
> Agent und Webkomponente tragen dieselbe Version.

> **Update:** neu ist `METADATA_AGENT_SERVICE_NAME` (Default `metadata-agent`).
> **Für Log-Auswertungen:** In den JSON-Zeilen heißt `time` jetzt `timestamp`,
> und `trace_id` ist die B3-Trace-ID; der bisherige Wert steht in `request_id`.
> Das Access-Log von uvicorn entfällt. **Helm:** Das Chart trägt ab jetzt die
> Version des Dienstes (bisher 0.3.1).
>
> **Vor dem Update prüfen:** Textextraktion und Geocoder ruft der Agent jetzt
> über die B-API auf (`METADATA_AGENT_B_API_SERVICES=auto`). Die Schlüssel der
> Aufrufer (Nutzer- und Gast-Schlüssel, `B_API_KEY`) brauchen dafür die Rechte
> für die Proxy-Dienste `text-extraction` und `photon_geocoding`. Fehlt das
> Recht, scheitert das Erschließen aus einer URL mit
> `TEXT_EXTRACTION_AUTH_REFUSED`, und Orte bleiben ohne Koordinaten. Der
> bisherige Weg bleibt mit `METADATA_AGENT_B_API_SERVICES=direct` oder einer
> gesetzten `METADATA_AGENT_TEXT_EXTRACTION_API_URL`; das Chart nannte bisher
> den Dienst im Cluster (`http://bildungs-api-services-full-text.b-api`).
>
> **Neu seit dem 15.11.:** `METADATA_AGENT_LLM_CACHE_BREAKPOINT` (Default
> `auto`); `metadata_agent_llm_tokens_total` kennt zusätzlich
> `kind="cache_write"`.
>
> **Vor dem Update prüfen (Korrekturen vom 14.11., Einzelheiten unten):**
>
> - `/validate` und `/export/markdown` antworten mit `404`, wenn Kontext,
>   Version oder Schemadatei unbekannt sind (bisher `200` mit `valid: false`
>   bzw. einem Markdown „Schema not found“).
> - Ungültiges JSON ist bei jedem Endpunkt `400`; `/extract-field`,
>   `/detect-content-type`, `/node` und `/screenshot` antworteten bisher `422`.
> - `/widget/info` liefert relative Verweise (`dist/main.js`,
>   `examples/full.html`) statt absoluter URLs; in den Snippets steht der
>   Platzhalter `{AGENT_URL}`.
> - `/nodes` lehnt die Optionen von `/upload` mit `422` ab und umgekehrt, ebenso
>   einen unbekannten Schlüssel neben einem verschachtelten `metadata`.
> - Einstellungen mit ungültigem Wert beenden den Start (Liste unten).
>   `METADATA_AGENT_SCREENSHOT_METHOD` und `METADATA_AGENT_DEFAULT_CONTEXT`
>   gibt es nicht mehr.
> - Helm: Der interne Port liegt auf einem eigenen Service `<name>-internal`;
>   der Haupt-Service hat nur noch Port 8000. Wer `/metrics`, `/health` oder
>   `/ready` über den Haupt-Service auf Port 9000 abruft, stellt auf den neuen
>   um. Das Chart bricht jetzt bei weiteren unpassenden Werten ab (Liste unten).
> - Ein angehängter Schrägstrich wird nicht mehr weitergeleitet: `/generate/`
>   antwortet `404` statt `307`.

- JSON-Logzeilen tragen `timestamp`, `level`, `service`, `logger`, `message`,
  `trace_id`, `span_id` und `request_id`; dazu `client_trace_id`, wenn der
  Aufrufer `x-client-trace-id` schickt.
- Ohne eingehende B3-Header legt der Agent eine Trace-ID an und gibt sie an
  B-API, Textextraktion und Repository weiter. Die erzeugte Anfrage-ID
  (`X-Request-ID`, `error.request_id`) ist dann diese Trace-ID, 32 statt 16
  Hex-Zeichen.
- Eine Zeile je Anfrage mit Methode, Pfad, Status und Dauer ersetzt das
  Access-Log von uvicorn, in beiden Formaten: ohne Query-String und Header,
  `/health` und `/ready` nur auf `DEBUG`.
- Textextraktion und Geocoder über die Pfade der B-API
  (`/api/v1/proxy/text-extraction`, `/api/v1/proxy/photon_geocoding`), mit
  demselben Schlüssel wie die LLM-Aufrufe: Zur Staging-B-API gehören so immer
  die Staging-Dienste, zur Produktion die der Produktion. Neu ist
  `METADATA_AGENT_B_API_SERVICES` (`auto`, `proxy`, `direct`). Ohne B-API
  (`METADATA_AGENT_LLM_PROVIDER=openai`) folgt die Textextraktion der
  Repository-Adresse; `METADATA_AGENT_TEXT_EXTRACTION_API_URL` ist jetzt leer
  vorbelegt und überschreibt die Wahl. Neue Fehlercodes:
  `TEXT_EXTRACTION_AUTH_MISSING`, `TEXT_EXTRACTION_AUTH_REFUSED` (beide `502`),
  `TEXT_EXTRACTION_NOT_CONFIGURED` (`503`).
- Eine leere Umgebungsvariable (`VAR=` in einer `.env`, `""` in den
  Chart-Werten) gilt als nicht gesetzt: Es gilt der Default. Bisher traf das
  nur auf einzelne zu; ein leeres `METADATA_AGENT_LOG_FORMAT` oder
  `METADATA_AGENT_LLM_PROVIDER` ließ den Start scheitern. Unverändert bleiben
  die drei Einstellungen, bei denen „leer“ etwas bedeutet:
  `METADATA_AGENT_LLM_VERBOSITY` und `METADATA_AGENT_LLM_REASONING_EFFORT`
  (nicht senden), `METADATA_AGENT_CORS_ORIGINS` (kein Origin).
- Helm-Chart: Version = Version des Dienstes (ein Test hält Chart, Image-Tag
  und Dienst gleich); jeder Wert in `values.yaml` hat eine Zeile `## @param`,
  ebenfalls per Test.
- Steckbrief: Angaben und begründete Abweichungen nach dem Entwicklerleitfaden
  (Dienst-ID, Funktionen, b-api, Abhängigkeiten, Kompatibilitätsmatrix).
- Neue Grafiken für den Betrieb über die B-API
  (`docs/architektur/*-b-api.*`); die bisherigen zeigen den direkten Betrieb.

Korrekturen aus dem [Audit vom 13.11.2026](docs/audits/2026-11-13-audit-3.0.1.md):

- **Zwei reguläre Ausdrücke konnten den ganzen Pod anhalten** (P-08, P-09;
  schon in 3.0.1): ein Text `[IST-STAND]` mit vielen Leerzeichen an `/generate`
  (gemessen 43 s bei 4 KB) und ein Datumswert mit vielen Leerzeichen an
  `/validate` (102 s bei 2 KB). Die Marker werden jetzt ohne Regex getrennt,
  Datumswerte über 100 Zeichen sind kein Datum.
- **Geocoder über die B-API:** Lehnt die B-API den Schlüssel ab oder drosselt
  sie (401, 403, 429), steht das einmal in `processing.warnings` („Orte bleiben
  ohne Koordinaten …“), und die übrigen Orte der Anfrage werden nicht mehr
  einzeln versucht. Neue Alarmregel `MetadataAgentUpstreamKeyRefused` für
  abgelehnte Schlüssel an Textextraktion und Geocoder (13 Regeln).
- **Ein Schlüssel, eine B-API:** `METADATA_AGENT_B_API_SERVICES=auto` nutzt die
  B-API für Textextraktion und Geocoder nur, wenn auch die LLM-Aufrufe an
  `METADATA_AGENT_B_API_BASE_URL` gehen. Mit einer eigenen LLM-Basisadresse
  wären die Schlüssel sonst an eine zweite B-API gegangen; der Agent warnt
  beim Start und ruft beide Dienste direkt auf.
- `input_source=node_url`: Ein abgelehnter oder fehlender Schlüssel und eine
  fehlende Textextraktion werden nicht mehr durch den Rückfall auf Titel und
  Beschreibung verdeckt; die Anfrage endet mit dem Fehlercode.
- B3: Eine `x-b3-traceid`, die keine ist (16 oder 32 Hex-Zeichen), beginnt
  eine neue Trace und wird nicht mehr zur Anfrage-ID der Antwort. Ein `b3`, das
  nur die Sampling-Entscheidung trägt, geht als `x-b3-sampled` bzw.
  `x-b3-flags` weiter.
- Die Textextraktion wird ohne B-API nur noch für OpenEduHub-Repositorys
  abgeleitet; ein fremder Host mit „staging“ im Namen bekommt sie nicht mehr.
- Logs: Zusatzfelder können `message`, `level` und die IDs nicht überschreiben;
  der Pfad der Anfragezeile wird nach 2.000 Zeichen abgeschnitten; der Start per
  `python -m src.main` schreibt kein Access-Log mehr.
- Der interne Port weist den Screenshot-Browser ab wie Port 8000; der Browser
  nimmt keine Downloads mehr an.
- Abhängigkeiten: `certifi` 2026.10.23 (PYSEC-2026-4320) und
  `opentelemetry-api` 1.45.2 (CVE-2026-126360).
- `/debug/dependencies` prüft Textextraktion und Geocoder über die B-API mit
  dem Schlüssel der Anfrage und meldet fehlende Rechte (`authorized: false`);
  neu ist der Eintrag `geocoder`.
- Korrekturlauf (`existing_metadata` mit Anweisung): Der Block der bisherigen
  Werte ist begrenzt (je Wert 2.000, zusammen 30.000 Zeichen). Bisher ging er
  unbegrenzt in jeden Feld-Prompt und verdrängte den Quelltext.
- Eine an der Tokengrenze abgeschnittene LLM-Antwort (`finish_reason: length`)
  zählt als gescheitertes Feld (`LLM_ANSWER_TRUNCATED`); aus einem halben
  JSON-Objekt wurde bisher der Wert „value“.
- `/upload`, `/nodes`, `/upload/verify`: `metadataset`, `schemaVersion`,
  `contextName` und `_schema` werden vor dem Schreiben geprüft (`422`). Bisher
  wurde der Node geschrieben, und die Antwort scheiterte danach mit `500`.
  `metadataset` darf wie bei `/validate` die URI des Inhaltstyps sein.
- Screenshot-Browser: Auf die Antwort auf einen POST einer Seite wartet er
  höchstens 15 s; eine Antwort über 5 MB erreicht die Seite nicht. Geprüft wird
  seit dem 14.11. auch die tatsächliche Länge, nicht nur die angekündigte
  (S-41); gelesen hat der Browser-Treiber die Antwort dann schon.
- Helm: Eine Zahl ohne Anführungszeichen unter `env` wird als Zahl gerendert
  (bisher `"4.194304e+07"`); `sharedLlmLimit.requestsPerMinute: 0` bricht ab.
- CI: Ein Release-Tag muss `vX.Y.Z` heißen und zur Version in `pyproject.toml`
  passen; GitHub veröffentlicht nur `main` und Tags; GitLab lintet mit dem ruff
  aus `uv.lock` (0.17.0), ruff- und kubeconform-Image per Digest.
- **Teilergebnis statt 504:** Feld-Aufrufe, die 5 s vor dem Zeitlimit noch
  laufen, werden beendet; was bis dahin erschlossen ist, kommt als `partial`
  zurück, die späten Felder stehen in `processing.errors`. Ein Lese-Timeout
  des KI-Diensts wird nur noch einmal wiederholt (bisher bis zu 273 s für ein
  einzelnes Feld).
- **Getrennte Verbindung:** Schließt der Aufrufer die Verbindung (etwa ein
  Proxy nach seinem eigenen Timeout), stoppen `/generate`, `/extract-field`
  und `/detect-content-type` ihre KI-Aufrufe. Im Log und in den Metriken steht
  dafür der Status `499`.
- Neu: `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` (Default leer = keine
  Grenze). Darüber antwortet `/generate` sofort mit `503`
  `TOO_MANY_GENERATIONS` und `Retry-After`.
- Schreibpfad: Sammlungsverweise entstehen erst direkt vor dem Workflow, nach
  Extended Data und Vorschau. Ein Abbruch davor ließ einen Verweis auf einen
  verworfenen Node in der Sammlung zurück.
- Ein Node, den das Repository nicht hat oder nicht zeigt, ist bei `/generate`,
  `/extract-field` und `/detect-content-type` jetzt `404` (bisher `502`); ein
  nicht freigegebenes `llm_model` wird vor dem Abruf der Quelle abgelehnt.
- Textextraktion: Drosselung ist `503` `TEXT_EXTRACTION_RATE_LIMITED`, eine
  Antwort ohne JSON `502` `TEXT_EXTRACTION_FAILED`.
- `/generate`: `_origins` und `_source_text` falscher Art sind `422`, `NaN` und
  `Infinity` im Body `400` (bisher `500`, beim `NaN` erst nach dem ganzen Lauf).
- `/extract-field` gibt ein Feld in derselben Form zurück wie `/generate`:
  Koordinaten für `schema:location`, Tageskürzel bei Öffnungszeiten.
- `/validate` und `/export/markdown` beachten `context`, `version`,
  `schema_file` und `include_empty` auch, wenn sie in der flachen Form neben
  den Feldern stehen.
- Deutsch geschriebene Daten vor 1900 (`28.08.1749`) werden gelesen wie
  `1749-08-28`.
- OpenAPI: Jeder Pfadparameter hat Beschreibung und Beispiel.
- Logs: Im Textformat steht jede Meldung auf einer Zeile (ein Zeilenumbruch in
  einem Wert aus der Anfrage begann bisher eine eigene Zeile). Orte, Adressen,
  Koordinaten und Feldwerte stehen nicht mehr im Log ab `INFO`, nur noch Anzahl
  und Feld-IDs; die Einzelheiten gibt es bei `DEBUG`.
- Image: `uvicorn` ohne das Extra `standard`. `watchfiles`, `websockets` und
  `pyyaml` werden nicht mehr installiert, `httptools` und `uvloop` bleiben.
  `.dockerignore` schließt `__pycache__`, `*.pyc` und `*.log` in allen
  Verzeichnissen aus.
- OpenAPI: `/validate` und `/export/markdown` zeigen ihre Optionen im Schema
  des Bodys (bisher `{"type": "object"}`).
- Intern: `/generate`, `/detect-content-type` und `/extract-field` teilen sich
  den Block der Eingangsquelle (`InputSourceRequest`); die OpenAPI ist dadurch
  unverändert. Unbenutzte Tageskürzel-Abbildung im `FieldNormalizer` entfernt.
- **Webkomponente neu gebaut** (weiter 3.0.1, Korrekturen aus dem Audit; siehe
  deren CHANGELOG). Für Einbettende ändert sich: weniger `metadataChange`
  (keines mit leerem Datensatz beim Start, eines je Import), Objektfelder
  behalten `@type` und fremde Schlüssel, geleerte Felder melden `null`,
  Ja/Nein-Felder `true`/`false`, `schemaVersion` nennt die echte Version
  (`v2.0.0` statt `vlatest`), `readonly` sperrt die Wahl des Inhaltstyps,
  Vorschaubilder nur von `data:image/…`, `https:` oder dem eigenen Host.
  Warnungen des Agents (`processing.warnings`) erscheinen als Hinweis.
- **Schneller und günstiger erschließen (15.11.):** drei Änderungen, jede
  zuerst gegen die B-API geprüft (`gpt-6-luna`).
  - Der Seitentext endet mit einem ausdrücklichen Cache-Haltepunkt
    (`prompt_cache_options.mode: "explicit"`, `prompt_cache_breakpoint`).
    Die Feld-Aufrufe lesen den Text nur noch aus dem Cache und schreiben
    ihre Feldanweisung nicht mehr hinein (1,25-facher Eingabepreis). Je
    Lauf werden 3.000 statt 53.300 Tokens in den Cache geschrieben, die
    Kosten sinken um rund 15 %. `METADATA_AGENT_LLM_CACHE_BREAKPOINT`:
    `auto` (nur Modelle `gpt-6…`; andere antworten `400`), `on`, `off`.
  - Preis des Haltepunkts: Derselbe Text, kurz danach wortgleich erneut
    erschlossen, kostet mit Haltepunkt so viel wie beim ersten Mal (0,0065 $
    statt 0,0018 $). Über die B-API fängt deren eigener Cache das ab; sonst
    `METADATA_AGENT_LLM_CACHE_BREAKPOINT=off`.
  - Der Cache-Vorlauf läuft gleichzeitig mit der Erkennung der Inhaltsart
    statt danach (`LLMService.text_cache_primer`), außer die Anfrage bringt
    schon Metadaten mit. Endet der Lauf vorher, wird der Vorlauf gestoppt.
  - `metadata_agent_llm_tokens_total` zählt `kind="cache_write"`.
  - Gemessen mit einem Text von 4.400 Zeichen (47 Felder): 9,2–9,5 s
    vorher, jetzt im Median 5,2 s; 0,0083 $ vorher, 0,0070 $ jetzt.
    Einzelheiten in `UEBERGABE.md` §7.
  - Unverändert bleiben die 20 gleichzeitigen Aufrufe und `max_workers` von
    1 bis 20. Die B-API beantwortete 60 und 100 Aufrufe im selben Moment
    ohne `429`, 60 Worker brachten aber nur 4,6 s statt 5,2 s.
- **Steckbrief im neuen Schema (15.11.):** `docs/steckbrief.md` folgt dem
  gemeinsamen Schema für die Übergabe von Diensten (Stammdaten, Architektur,
  Deployment und Infrastruktur, Security und Konfiguration, Monitoring und
  Betrieb, Ressourcen und Kosten; Status „vorhanden“, „geprüft“, „In
  Klärung“, „In Nacharbeit“). `UEBERGABE.md` zeigt die Grafiken für den
  Betrieb über die B-API und nennt in §7 die Kontrollmessung der Kosten und
  den Stand des Prompt-Cachings.
- **Doku neu gegliedert, Englisch als Standard (14.11.):** `README.md`,
  `INSTALL.md`, `DEPLOYMENT.md` und `ENV-PARAMETER.md` sind englisch, die
  deutschen Fassungen heißen `….de.md` und haben denselben Aufbau. Die README
  ist eine Einstiegsseite; die API-Referenz steht in `docs/API.md` /
  `docs/API.de.md`. Übergabe-Dokumente (UEBERGABE, Steckbrief, B-API-Kurzinfo,
  die Referenzen) und dieser CHANGELOG bleiben deutsch und verweisen auf die
  deutschen Fassungen. Die Update-Hinweise 2.0.x → 2.1.0 stehen jetzt in
  `docs/update-2.0-auf-2.1.md`. Die Tests zu den Dokumenten prüfen beide
  Sprachen.
- OpenAPI-Texte berichtigt: Feldzahlen bei `/upload` (Schema 2.0.0: 26 / 33 /
  40), der Name `WLO_GUEST_USERNAME` bei `/workflow`, `source_url` ist bei
  `node_url` optional.
- Doku: Steckbrief nennt drei weitere Abweichungen vom Leitfaden (Textlogs als
  Standard, `/debug/*` auf Port 8000, `503` für fehlende Einrichtung);
  `docs/team6-anforderungen.md` ist als historisch markiert; die Beispiele für
  AcademicCloud nennen 0,16 Anfragen je Sekunde.

Korrekturen aus dem [Audit vom 14.11.2026](docs/audits/2026-11-14-audit-3.0.1.md):

**Aufrufer: Statuscodes und Grenzen**

- `/validate`, `/export/markdown`: Unbekannter Kontext, unbekannte Version oder
  Schemadatei ist `404` `NOT_FOUND` (bisher `200`); ein Body über 2 MiB ist
  `413` `REQUEST_TOO_LARGE`. Eine unbekannte Inhaltstyp-URI bleibt `400`.
- Ungültiges JSON, `NaN`/`Infinity`, ungültiges UTF-8, zu tiefe Verschachtelung
  und ein Body, der kein Objekt ist, sind bei jedem Endpunkt mit Body `400`
  (bisher je nach Endpunkt `422` oder `500`).
- Die Liste in `detail` einer `422`-Antwort nennt je Eintrag nur noch `loc`,
  `msg` und `type`, nicht mehr den gesendeten Wert.
- Längengrenzen, darüber `422`: `context` 64, `version` 32, `schema_file` 300,
  `field_id` 200, `language` 8, `url`/`source_url`/`preview_url` 8.192 Zeichen,
  Einträge von `collection_id`, `workflow_steps` und `steps` 200. Dasselbe gilt
  für die Marker im Datensatz und für Pfadparameter (`/widget/i18n/{lang}.json`
  über 8 Zeichen: `422` statt `404`).
- Ein angehängter Schrägstrich wird nicht weitergeleitet: `/generate/` und
  `/widget/info/` antworten `404` statt `307`.
- Ein Node ohne Text (`node_id`, `node_url`) ist `422` statt `502`.
- `/generate`: `screenshot_method` nimmt nur `playwright` und `pageshot`,
  anderes ist `422`. Eine Korrekturanweisung über 10.000 Zeichen ist auch bei
  URL- und Node-Quelle `400`.
- `/generate`: Kopfschlüssel (`contextName`, `schemaVersion`, `metadataset`,
  `language`, `exportedAt`, `processing` …) in `existing_metadata` und in den
  Metadaten eines gelesenen Nodes entfallen und kommen nicht mehr als Feld
  zurück.
- `/screenshot` mit `node_id` ohne Repository-Zugang: `503` vor der Aufnahme
  (bisher `200` mit `preview_uploaded: false` nach ihr). Ein vom Repository
  abgelehnter Vorschau-Upload bleibt `200` mit `preview_uploaded: false` und
  `error`.
- Geht der Aufrufer, bevor der Request-Body vollständig ist, steht `499` in Log
  und Metrik.
- CORS: `Retry-After` ist für Seiten anderer Herkunft lesbar. Ein
  `x-request-id` mit 65 bis 128 Zeichen wird nicht mehr weitergereicht; an
  seine Stelle tritt die Anfrage-ID.

**Aufrufer: Schreiben ins Repository (`/upload`, `/nodes`, `/upload/verify`)**

- `/nodes` lehnt die Optionen ab, die es nur bei `/upload` gibt, und umgekehrt
  (`422`, bisher still ignoriert oder als Feld abgelegt). Neben einem
  verschachtelten `metadata` ist jeder unbekannte Schlüssel `422`.
- Ein `metadataset`, dessen Schema sich nicht laden lässt, ist `422`
  `SCHEMA_UNAVAILABLE` mit `step: "schema"`, vor der ersten Anfrage ans
  Repository. Bisher galt still nur `core.json`. `schemaVersion: null` steht
  für die Standardversion.
- Neu in der Antwort: `skipped_fields` nennt die gesendeten Felder, die auf
  keine Property geschrieben wurden; `fields_skipped` zählt sie jetzt.
- Zeitlimit während der Sammlungsverknüpfung: Der Node bleibt bestehen. Die
  `504`-Antwort nennt ihn in `node` (kein `discarded_node`) und in
  `collections` je Sammlung: verknüpft, „Verknüpfung nicht bestätigt“ oder
  „Verknüpfung nicht versucht“.
- Die Vorschau lässt den Anfragen nach ihr mehr Zeit: 15 s für die erste
  Sammlung oder den ersten Workflow-Schritt, 5 s für jede weitere, höchstens
  45 s; folgt nichts, 1 s (bisher immer 15 s).
- Dublettenprüfung: zusätzlich zur Suche werden die 25 neuesten Nodes des
  Eingangsordners gelesen; Prüfung und Anlegen laufen je URL nacheinander (je
  Prozess). Ein Treffer ohne Titel ist eine Dublette
  (`URL existiert bereits: Node <id>`).
- Geht die Antwort auf das Anlegen verloren, wird ein Node nur verworfen, wenn
  er sicher der eigene ist (unverändert, nur die Felder des Anlegens). Sonst
  meldet `error` „möglicherweise wurde trotzdem ein Node angelegt“.
- Lizenz: `CC BY-SA`, `cc-by-sa` und `CC0` werden als `CC_BY_SA` bzw. `CC_0`
  geschrieben. Ein portierter CC-Link (`/3.0/de/`) behält `ccm:custom_license`
  neben Schlüssel und Version. Als Vokabular-URI gilt nur noch eine Adresse
  unter `http(s)://w3id.org/openeduhub/vocabs/`, alles andere ist `CUSTOM`.
- Werte auf der Leitung: Wahrheitswerte als `"true"`/`"false"`, verschachtelte
  Listen flach, Werte nur aus Leerzeichen werden nicht geschrieben; ein
  übergebenes `cm:name` wird bereinigt.
- Sammlungen: Antwortet das Repository mit `409` (schon verknüpft), gilt die
  Sammlung als verknüpft.
- `extended_text` wird auf 150.000 Zeichen gekürzt (Hinweis in `error`).
  `ccm:oeh_extendedData` enthält die Metadatenfelder ohne Umschlag und ohne
  die Schlüssel `ccm:oeh_extended*`.
- Node als Quelle: Übernommen werden nur Properties, die ein Schema des
  Kontexts führt, nie `ccm:oeh_extended*`; `ccm:oeh_lrt` steht unter
  `oeh:new_lrt`, wo das Schema es so nennt. Der gespeicherte Text wird auf
  150.000 Zeichen gekürzt (Warnung); `403`/`404` beim Textabruf heißt „kein
  gespeicherter Text“.
- `/upload/verify`: Die flache und die beiden verschachtelten Formen ergeben
  denselben Vergleich, Optionen zählen nicht als Felder. Ein CC-Link gilt als
  Schlüssel plus Version, `oeh:new_lrt` wird gegen `ccm:oeh_lrt` geprüft,
  abgeleitete Properties stehen nicht in `extra_in_repo`. Groß-/Kleinschreibung
  zählt bei Freitext, nicht bei Vokabularfeldern und Lizenzschlüssel.
- `/upload/verify`, Beschreibung in `/docs` und `docs/API.de.md` berichtigt:
  `not_written` heißt „wird nie geschrieben“ – das Feld ist kein
  Repository-Feld des Schemas, weder unter seinem Namen noch unter dem, unter
  dem es geschrieben wird (`oeh:new_lrt` zählt als `ccm:oeh_lrt`), oder es ist
  ein `virtual:`-Feld. `summary.unresolved` zählt die verglichenen Felder,
  deren Wert das Repository nicht auflöst; ein solches Feld kann zugleich
  `match` sein.
- `METADATA_AGENT_REPOSITORY_URL` gilt mit und ohne `/rest` am Ende.

**Aufrufer: Prüfen, Exportieren, Widget**

- `/validate`: höchstens 20 Befunde je Feld und Prüfung plus ein
  Schlusseintrag, höchstens 500 Fehler und 500 Warnungen (danach ein Eintrag
  mit `field_id: "_limit"`); zitierte Werte sind auf 200 Zeichen gekürzt.
- `/validate`: `[""]`, Werte nur aus Leerzeichen und `{}` füllen kein
  Pflichtfeld. Zahlen und Wahrheitswerte werden in geschlossenen Vokabularen
  nach ihrer Textform verglichen; `0` und `false` werden geprüft.
- `/validate` prüft Vokabularwerte mit demselben Abgleich wie die Erschließung
  (`services/vocabulary_matching.py`). Einen Vorschlag (`Did you mean '…'?`)
  gibt es damit auch für Vokabulare, deren Schlüssel ein `value` ist (`Deutsh`
  ergibt `de`), für ein Label, eine andere Groß-/Kleinschreibung und das Ende
  einer URI (`460`); nach Ähnlichkeit erst ab vier Zeichen. Vorgeschlagen wird
  immer der Schlüssel des Konzepts.
- `/export/markdown`: Jedes gefüllte Feld steht genau einmal da, Felder
  nicht deklarierter Gruppen unter „Weitere Angaben“ / „Further details“;
  `[0]` und `[false]` sind Werte. Titel ist der Name des Inhaltstyps,
  `schema_used` nennt die Datei.
- `GET /info/schemas/…`: `label` ist der Name des Inhaltstyps (`Veranstaltung`
  statt des Dateinamens; `core.json` behält den Dateinamen).
- `GET /widget/info`: `dist_base_url`, Script-, Style- und Beispieladressen
  sind relative Verweise (`dist`, `dist/main.js`, `examples/full.html`), die
  Snippets nennen `{AGENT_URL}`. Der Dienst baut keine Adresse mehr aus der
  Anfrage; hinter einem Proxy mit Pfad-Präfix stimmten die URLs bisher nicht.
- `/detect-content-type`, `/extract-field`: Eine Kürzung des Texts auf 150.000
  Zeichen steht in den Warnungen.
- `/extract-field` meldet in `processing.warnings` dieselben Hinweise wie
  `/generate`, im selben Wortlaut: ein Datum des Modells, das nicht übernommen
  wurde, weitere Einträge einer Liste in einem Einzelfeld, Einträge ohne
  Objektform und Orte, deren Geokodierung scheitert.
- `/docs` zeigt zu jeder der 16 Operationen eine Beispielantwort (`200`), bei
  `/generate` und `/upload` je zwei benannte: vollständiges Ergebnis und
  Teilergebnis, geschriebener Datensatz und Dublette (Audit vom 13.11.,
  API-15). Es ist dasselbe JSON wie nach „Antwort `200`“ in `docs/API.de.md`
  und `docs/API.md`; ein Test hält beides gleich und prüft jedes Beispiel
  gegen das Antwortmodell. Die Beispiele lassen Felder ohne Wert (`null`) weg,
  in der Referenz jetzt auch bei `/detect-content-type`, `/upload/verify` und
  `/screenshot`. Sonst ist das OpenAPI-Dokument unverändert.

**Erschließung**

- Vokabulare: Aus einer Modellantwort wird immer der Schlüssel, nie das Label
  („Deutsh“ und „German“ ergeben `de`); zugeordnet wird über Schlüssel, Label
  und Alternativlabel in jeder Sprache, nach Ähnlichkeit nur bei geschlossenem
  Vokabular und erst ab vier Zeichen. Werte des Aufrufers werden nur exakt
  zugeordnet, Unbekanntes bleibt stehen.
- Ein einzelner Wert in einem Listenfeld wird zur Liste mit einem Eintrag. Eine
  Liste in einem Einzelfeld behält den ersten Eintrag; `processing.warnings`
  nennt die übrigen („Feld nimmt einen Wert auf: N weitere Wert(e) nicht
  übernommen.“).
- Neue Einträge in `processing.warnings`: ein Datum des Modells, das es in der
  Lesart Tag/Monat/Jahr nicht gibt (`03/15/2025`), entfällt; Einträge ohne
  Objektform in `schema:eventSchedule` und
  `schema:openingHoursSpecification` entfallen; Orte, deren Geokodierung
  scheitert, bleiben unverändert.
- `/generate` ohne ein Feld, das zu erschließen wäre, antwortet `200` mit dem
  unveränderten Datensatz und `fields_extracted: 0` (bisher `500`).
- `_origins` nennt `ai` auch für ein Feld, nach dem das Modell gefragt wurde
  und das leer blieb (bisher `user`).
- Ein Feld, das `core.json` und der Inhaltstyp beide führen, wird einmal
  erschlossen und einmal gezählt: `fields_total` für `event.json` ist 47
  (bisher 48).
- Ein Pflichtfeld ohne Wert kostet genau einen weiteren KI-Aufruf, bei
  geschlossenem Vokabular keinen (bisher bis zu
  `METADATA_AGENT_LLM_MAX_RETRIES` weitere).
- Der bisherige Wert eines Felds steht höchstens mit 2.000 Zeichen im Prompt.
- Zahlen: `1.500` ist in einem Ganzzahlfeld 1500 (bisher 2); `1_000` und „ein“
  sind keine Zahlen. Wahrheitswerte: nur `1` und `0` gelten als `true` und
  `false`, jede andere Zahl nicht.
- Datum: „März 15, 2025“ und „1. Sept. 2025“ werden gelesen.
- Wochentage: Aufzählungen in einem Text („Mo, Mi, Fr“, „Mo-Do, Sa“, „Montag
  und Mittwoch“) werden aufgelöst, wenn jeder Teil ein Tag oder Bereich ist;
  `mon` bis `sun` zählen; ein Eintrag über 200 Zeichen bleibt, wie er ist.
- Geokodierung: Eine Adresse als Text wird nachgeschlagen, Breitengrad 0 ist
  eine Koordinate, ein Fehler betrifft nur den einen Ort. An Photon geht nur
  die Sprache `de`, `en` oder `fr`, sonst keine.
- `/detect-content-type` nennt als Grund, wenn die Antwort des KI-Diensts an
  der Tokengrenze abgeschnitten wurde.

**Betrieb: Einstellungen**

- Ein ungültiger Wert beendet den Start, statt still ersetzt zu werden:
  `METADATA_AGENT_LLM_VERBOSITY` (nur `low`, `medium`, `high`, leer),
  `METADATA_AGENT_LLM_REASONING_EFFORT` (`none`, `low`, `medium`, `high`,
  leer), Temperatur außerhalb 0 bis 2, `METADATA_AGENT_LLM_MAX_TOKENS` und
  `METADATA_AGENT_REQUEST_TIMEOUT` unter 1,
  `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` unter 1 (bisher als 1 gezählt),
  eine negative Rate, Screenshot-Breite außerhalb 320 bis 3840, -Höhe
  außerhalb 200 bis 2160, -Wartezeit außerhalb 0 bis 10000 ms und eine
  `METADATA_AGENT_ALLOWED_LLM_MODELS` nur aus Kommas und Leerzeichen.
- Entfernt: `METADATA_AGENT_SCREENSHOT_METHOD` und
  `METADATA_AGENT_DEFAULT_CONTEXT`. Die Methode und den Kontext nennt die
  Anfrage; beide Variablen werden ignoriert.
- Neu: `METADATA_AGENT_GRACE_PERIOD_SECONDS` (leer = 320 s angenommen). Daran
  misst die Startwarnung, ob `METADATA_AGENT_GENERATE_TIMEOUT` (+ 10 s) und
  `METADATA_AGENT_UPLOAD_TIMEOUT` (+ 15 s) in die Zeit passen, die ein Pod
  beim Beenden bekommt. Das Chart setzt sie aus
  `terminationGracePeriodSeconds`.
- URL-Einstellungen (B-API, OpenAI-Basis, Repository, Textextraktion) werden
  vereinheitlicht: ohne Leerzeichen und Schrägstrich am Ende, Schema und Host
  klein, ohne Standardport. In den Startzeilen fehlen Nutzer und Passwort
  einer URL.
- Neue Startwarnungen: Provider `openai` ohne `OPENAI_API_KEY`; Provider
  `openai` mit `METADATA_AGENT_B_API_SERVICES=proxy` ohne `B_API_KEY`.
- uvicorn lauscht auf der Adresse aus `UVICORN_HOST` (im Image `0.0.0.0`);
  `--host` steht nicht mehr im Startbefehl. Lässt sich der interne Port nicht
  binden (belegt oder Adresse unbekannt), steht das im Log, und die API läuft
  ohne ihn weiter.

**Betrieb: Chart, Image, Compose, CI**

- Chart: Der interne Port liegt auf einem eigenen Service `<name>-internal`
  (immer `ClusterIP`); der Haupt-Service hat nur Port 8000, der ServiceMonitor
  wählt den neuen.
- Chart, neue Werte: `image.digest` (Image per Digest statt Tag),
  `listenHost` (Default `0.0.0.0`; setzt `UVICORN_HOST` und
  `METADATA_AGENT_INTERNAL_HOST`, `"::"` für IPv6) und
  `env.METADATA_AGENT_WLO_INBOX_ID`. Das Label `app.kubernetes.io/version`
  trägt die `appVersion` statt des Image-Tags.
- Chart bricht ab bei: leerer B-API- oder Repository-Adresse, `UVICORN_HOST`,
  `METADATA_AGENT_INTERNAL_HOST` oder `METADATA_AGENT_GRACE_PERIOD_SECONDS`
  unter `env`, einem `image.digest`, der kein `sha256:`-Digest ist,
  `service.internalPort` außerhalb 1 bis 65535, einer Ingress-Annotation
  `proxy-body-size`, die nicht zu `METADATA_AGENT_MAX_REQUEST_BYTES` passt,
  und einem geteilten Limit, das je Pod auf 0 rundet.
- Image: `tini` ist Init-Prozess (ENTRYPOINT) und räumt die Prozesse eines
  beendeten Chromium ab; installiert ist nur noch die Headless-Shell
  (`playwright install --only-shell chromium`); uvicorn hält Verbindungen 75 s
  offen (`--timeout-keep-alive 75`).
- Compose: `METADATA_AGENT_LLM_VERBOSITY`, `METADATA_AGENT_LLM_REASONING_EFFORT`
  und `METADATA_AGENT_CORS_ORIGINS` stehen als `${VAR-default}`, damit ein leer
  gesetzter Wert leer ankommt.
- CI: Geplante Prüfungen ohne Push – in GitLab die Jobs
  `scheduled dependency checks` und `scheduled image scan` (Pipeline-Schedule
  in der Oberfläche anlegen), auf GitHub `scheduled-checks.yml` täglich:
  `pip-audit`, Alter des Chromium, Trivy über das neueste Release-Image.
- CI: Mit `REQUIRE_BROWSER_AGE=1` (in beiden CIs gesetzt) scheitert die
  Altersprüfung des Chromium auch, wenn sich das Alter nicht bestimmen lässt.
  Zu jedem Versions-Tag entsteht in GitLab eine Stückliste `sbom.cdx.json`
  (CycloneDX). kubeconform prüft gegen Kubernetes 1.33.0.
- Der Smoke-Test des Images prüft zusätzlich `/ready`, den internen Port
  (`/ready`, `/metrics`) und `tini` als PID 1.
- `NOTICE` nennt zusätzlich FFmpeg und das Node.js des Playwright-Treibers;
  `colorama`, das nicht im Image liegt, ist gestrichen.

**Betrieb: Metriken, Alarme, Logs**

- `metadata_agent_generations_total` kennt zusätzlich `cancelled` (Aufrufer hat
  die Verbindung geschlossen, bisher `failed`) und `refused` (`503`
  `TOO_MANY_GENERATIONS`). Ein Fehler der Quelle zählt nur ab `500` als
  `failed`.
- `metadata_agent_llm_requests_total`: Ein am Zeitlimit oder durch den
  Aufrufer beendeter Aufruf hat `result="cancelled"` (bisher `ok`).
- `metadata_agent_http_request_duration_seconds` hat zusätzliche Buckets bei
  150, 180, 210, 240 und 270 s. `metadata_agent_upstream_responses_total` führt für
  den Dienst `other` nur die Statusklasse (`2xx`, `4xx` …).
- Neu: `metadata_agent_llm_last_failure_timestamp_seconds` (Labels des
  LLM-Zählers; Unix-Zeit des letzten KI-Aufrufs, der mit einem Fehler endete)
  und `metadata_agent_http_last_internal_error_timestamp_seconds` (nach
  `route`; Unix-Zeit der letzten Antwort `500`).
- Die drei kritischen Alarmregeln (`MetadataAgentLlmKeyRefused`,
  `MetadataAgentLlmRouteMisconfigured`, `MetadataAgentInternalError`) lesen
  neben dem Zähler diese beiden Metriken und lösen aus, solange der letzte
  Fehler weniger als 10 Minuten her ist: beim ersten Fehler, und ohne dass eine
  Abruflücke einen alten Fehler erneut meldet (bisher ein Rückblick über
  Zeitreihen, die im Fenster neu waren). Die Uhren von Pod und Prometheus
  müssen dafür auf deutlich weniger als 10 Minuten übereinstimmen. Ein Pod
  eines älteren Images hat die Metriken nicht; bei ihm entscheidet allein der
  Zähler.
- `metadata_agent_llm_request_duration_seconds`: Der Hilfetext nennt, was
  gemessen wird – die Zeit, die der Aufrufer eines KI-Aufrufs wartet: auf einen
  freien Platz innerhalb der LLM-Grenzen, die Anfrage, ihre Wiederholungen und
  die Pausen dazwischen.
- `MetadataAgentGenerateSlow` löst aus, wenn über 5 % der Läufe länger als
  270 s brauchen (bisher p95, zwischen 120 und 300 s interpoliert).
  `MetadataAgentGenerationsFailing` zählt nur beendete Läufe,
  `MetadataAgentServerErrors` nicht die `503` `TOO_MANY_GENERATIONS`,
  `MetadataAgentUpstreamUnanswered` liest die Antworten aus dem Histogramm.
- Logs: Fehlerantworten mit eigenem Fehlercode stehen bei `4xx` auf `INFO`,
  `TOO_MANY_GENERATIONS` auf `WARNING` (bisher beide `ERROR`). Im Textformat sind Steuerzeichen entfernt;
  Python-Warnungen erscheinen als Logger `py.warnings`.

**Intern**

- Umbau großer Module ohne Änderung der API; das OpenAPI-Dokument ist davor
  und danach gleich. Wer mit Skripten, eigenen Tests oder Log-Filtern auf
  Interna zugreift, prüft:
  - **Geteilte Module.** `models/schemas.py` ist eine Fassade über
    `models/common.py`, `extraction.py`, `validation.py`, `schema_info.py`,
    `screenshot.py` und `repository.py`; `from src.models.schemas import X`
    gilt weiter. Aus `services/repository_service.py` herausgelöst sind
    `repository_write.py`, `repository_edit_window.py`,
    `repository_preview_upload.py` und `repository_cut_off.py`, die
    Ergebnisse eines Schreibvorgangs stehen in `repository_result.py`. Die
    Formprüfung der Objektfelder steht in `services/object_shapes.py` (bisher
    `field_normalizer.py`), das Lesen einer `/generate`-Anfrage in
    `api/generate_request.py`, ihre Texte für `/docs` in
    `api/routers/generate_docs.py`. `services/lifecycle.py` schließt die
    HTTP-Clients beim Beenden.
  - **Umbenannt:** `src.api.routers.infra` heißt `src.api.routers.operations`,
    ohne Weiterleitung. `src/infra.py` bleibt.
  - **Ein Settings-Objekt.** `config.get_settings()` liefert das eine Objekt
    des Prozesses, ohne Cache: `get_settings.cache_clear()` gibt es nicht
    mehr. `deps.settings` ist eine Sicht auf dieses Objekt und steht nicht
    mehr in `deps.__all__`. Tests setzen ein anderes mit `use_settings` aus
    `tests/conftest.py`.
  - **Schreibweg und Lauf.** `RepositoryService.write_node(job, deadline)` ist
    der eine Schreibweg von `/upload` und `/nodes`; die Router bauen den
    Auftrag (`NodeWrite`). `upload_metadata` und `save_node` nehmen weiter
    Schlüsselwörter. `MetadataService.generate_metadata` nimmt
    `GenerationOptions`; die bisherigen Schlüsselwörter gelten weiter.
  - **Logger-Namen verschobener Zeilen:** `src.services.object_shapes` statt
    `src.services.field_normalizer` (Formprüfung);
    `src.services.repository_preview_upload` (Vorschau),
    `src.services.repository_cut_off` (abgeschnittener Schreibvorgang) und
    `src.services.repository_edit_window` (Bearbeitungsfenster) statt
    `src.services.repository_service`.
  - **Sonst:** Alle ausgehenden Clients teilen sich einen TLS-Kontext
    (`utils/tls_context.py`), auch die der Repository-Aufrufe, der
    Textextraktion und von `/debug/dependencies`. Die Bereinigung eines
    Eingabetexts läuft nur noch über die ersten 600.000 Zeichen; der Rest
    wird ohnehin abgeschnitten (ein Body von 19 MB kostete 406 ms).
  - **Tests:** 4579 bestanden, 29 übersprungen, Abdeckung 94,00 %
    (Untergrenze 87 %). 28 der übersprungenen sind Browser-Tests; sie brauchen
    ein Chromium und laufen in der CI, wo ein fehlender Browser als Fehler
    zählt (`REQUIRE_BROWSER=1`). Der eine weitere ist ein Netzwerktest, der
    nur von Hand gestartet wird.

## 3.0.1 — 2026-10-20 — Webkomponente 3.0.1

> **Update:** keine neuen Einstellungen, API unverändert. Nur das Image tauschen
> (`v3.0.1`, Helm-Chart 0.3.1).

- Liefert die Webkomponente 3.0.1 aus: Das Entfernen-Symbol der Chips und
  `info_outline` sind eingebettete SVGs. edu-sharing lädt `styles.css` nicht,
  und seine Icon-Schrift kennt beide nicht (statt ⊗ stand „ca“ im Chip).
  `widget/dist/fonts/material-icons-outlined.woff2` entfällt (Widget-CHANGELOG).
- Doku: korrigierte Einbindung in edu-sharing ohne kurz sichtbares Standard-Layout
  (Widget-README, „Einbettung in edu-sharing“), Steckbrief Stand 20.10.

## 3.0.0 — 2026-10-17 — Neuer Aufbau, Korrekturen aus dem Audit

> **Update:** Einstellungen und Metriken bleiben; neu sind
> `METADATA_AGENT_GENERATE_TIMEOUT` (Default 300 s, auch für `/detect-content-type`
> und `/extract-field`, auch ohne Ingress), `METADATA_AGENT_UPLOAD_TIMEOUT` (Default
> 100 s), `METADATA_AGENT_REQUIRE_REPOSITORY` (Default `true`) und
> `METADATA_AGENT_ALLOWED_LLM_MODELS` (Default leer = alle);
> `METADATA_AGENT_LLM_MAX_RETRIES` muss mindestens 1 sein.
> Jede Fehlerantwort der API trägt jetzt `detail` **und** `error`, auch `413` und das
> `503` von `/ready` (siehe unten). **Breaking für Clients:**
> einige Statuscodes (siehe „Breaking“). **Betrieb:** Der Helm-Chart 0.3.0
> legt eine NetworkPolicy an (Standard an) – die Namespaces in
> `networkPolicy.*` vor der Installation an den Cluster anpassen, sonst
> erreicht die B-API den Dienst nicht. Das Root-Dateisystem ist schreibgeschützt
> (`/tmp`, `/home/appuser` als emptyDir), `METADATA_AGENT_INTERNAL_PORT` kommt aus
> `service.internalPort`. Die Logger heißen nach den neuen Modulen (siehe unten)
> – wer im Logging-System auf `logger="src.main"` filtert, muss das anpassen.
> Helm-Chart 0.3.0 deployt `v3.0.0`. Schritt für Schritt: DEPLOYMENT.md,
> „Update von 2.1.x auf 3.0.0“.

### Lange Quellen (19.10.)

Gemessen: der Wikipedia-Artikel zu Goethe, 412.000 Zeichen → 56 KI-Aufrufe à
Ø 34 s → 126 s. Das alte Zeitlimit von 110 s brach ihn ab. Auf 200.000 Zeichen
gekürzt dauerte er 59 s (Ø 12 s je Aufruf), auf 150.000 Zeichen 35–42 s
(Ø 8,6 s) mit denselben Feldern. Die Werte hier ersetzen die 110 s / 120 s /
500.000 Zeichen in den Abschnitten darunter.

- **Textbudget 150.000 Zeichen** (`MAX_TEXT_LENGTH`, vorher 500.000), der Rest
  wird abgeschnitten – für `/generate`, `/detect-content-type` und
  `/extract-field`. `/generate` meldet das in `processing.warnings`:
  „Quelltext auf 150.000 Zeichen gekürzt (Original: N Zeichen).“ Eine
  Korrekturanweisung wird weiterhin nie gekürzt, die Quelle macht ihr Platz;
  ein neuer Text über 10.000 Zeichen mit `_source_text` ist eine neue Quelle
  und wird wie jede gekürzt.
- **Zeitlimit für `/generate` 300 s** (`METADATA_AGENT_GENERATE_TIMEOUT`,
  vorher 110 s; gilt auch für `/detect-content-type` und `/extract-field`).
  `METADATA_AGENT_REQUEST_TIMEOUT` (90 s je KI-Aufruf) und
  `METADATA_AGENT_UPLOAD_TIMEOUT` (100 s) bleiben.
- **Ingress und Grace-Period 320 s** (vorher 120 s): Helm-Annotationen
  `proxy-read-timeout`/`proxy-send-timeout`, `terminationGracePeriodSeconds`,
  Compose `stop_grace_period`, `docker run --stop-timeout 320`. Die
  Startwarnung kommt jetzt ab `METADATA_AGENT_GENERATE_TIMEOUT` > 310 bzw.
  `METADATA_AGENT_UPLOAD_TIMEOUT` > 305.
- **Alert `MetadataAgentGenerateSlow`:** feuert bei p95 über 270 s statt 100 s
  (der Schwellwert hing am alten Zeitlimit; Läufe von 126 s sind jetzt normal).
- **Betrieb: Die Proxys davor brauchen mindestens ~310 s.** Der B-API-Proxy
  und edu-sharing vor dem Agenten haben eigene Timeouts, die der Chart nicht
  setzt. Sind sie kürzer, brechen sie lange Erschließungen vorher ab.

### Korrekturen aus dem finalen Audit vom 18.10. (`docs/audits/2026-10-18-audit-3.0.0-final.md`)

Verhalten, das Clients sehen:

- **Screenshot-Browser und die eigene API (S-15):** Eine gescreenshotete Seite
  konnte über Weiterleitungen Anfragen samt `text/plain`-Body an die API auf
  Loopback schicken (`route()` sieht nur den ersten Aufruf einer Kette). Jetzt
  trägt jede Anfrage des eigenen Chromium – Weiterleitungs-Hops eingeschlossen
  – den Header `X-Metadata-Agent-Screenshot`, und die API beantwortet sie mit
  `403` `SCREENSHOT_BROWSER_REFUSED`, bevor ein Endpunkt läuft. Anfragen mit
  Body (Formular-POST u. a.) gehen Hop für Hop hinaus: Jedes Weiterleitungsziel
  wird vor dem Senden geprüft, ein weitergeleitetes Formular öffnet das Ziel als
  neue (wieder geprüfte) GET-Navigation, jede andere Anfrage mit Body wird nicht
  weitergeleitet – sie würde den Body an das neue Ziel mitnehmen. GET- und
  HEAD-Navigationen lädt Chromium selbst wie in 2.1.1 (ein direkter Link auf ein
  großes Video, PDF oder ZIP wird gestreamt statt im Treiber ganz geladen); ihre
  Weiterleitungen werden wie bisher nachträglich geprüft und verwerfen die
  Aufnahme – der Hop selbst ist dann schon gesendet, an die eigene API markiert
  und mit `403` abgelehnt. Der Header löst keinen CORS-Preflight aus (im echten
  Chromium geprüft). Clients ohne den Header
  (2.x-Widget, edu-sharing-Proxy, `text/plain`-Bodies, Docker-HEALTHCHECK,
  lokale Entwicklung auf 127.0.0.1) sind nicht betroffen.
- **URL-Prüfung (S-14):** Ein Hostname besteht nur noch aus Buchstaben, Ziffern,
  Bindestrich und Unterstrich (nach IDNA) oder ist eine IP-Adresse;
  `http://127.0.0.1\x.example/` (für Python ein Hostname, für Browser
  127.0.0.1), `%`, Leerzeichen oder Steuerzeichen im Host ergeben `400`.
  Weitergegeben wird die URL mit dem geprüften Host (Punycode, ohne Tabs und
  Zeilenumbrüche) – an die Textextraktion, den Screenshot und in `url` der
  `/screenshot`-Antwort.
- **Ungültiges JSON über 1 MB (S-16)** wird nicht mehr zeichenweise auf der
  Event-Loop repariert (20 MB blockierten den Worker 2,4 s), sondern mit `400`
  abgelehnt. Kleinere Bodies mit rohen Zeilenumbrüchen werden weiter repariert;
  gültiges JSON bis `METADATA_AGENT_MAX_REQUEST_BYTES` bleibt unverändert.
- **`/ready` auf dem öffentlichen Port:** `503` mit `detail` und `error`
  (`NOT_READY`) wie jeder Fehler, weiterhin ohne Einstellungsnamen;
  `status: "not_ready"` bleibt.
- **`METADATA_AGENT_ALLOWED_LLM_MODELS` (S-19, optional):** Ein per `llm_model`
  gewähltes Modell läuft auf dem Schlüssel des Servers, wo kein Schlüssel
  durchgereicht wird. Mit der Liste antwortet ein anderes Modell `422`, bevor
  ein KI-Aufruf startet; das Default-Modell des Providers ist immer erlaubt.
  Leer (Default): wie 2.1.1.
- **OpenAPI-Rechtehinweise (S-18)** auch an `/generate`, `/detect-content-type`
  und `/extract-field` (lesen mit `node_id`/`node_url` nicht öffentliche Nodes),
  `/workflow/{node_id}` (Antwort mit Workflow-Historie samt Benutzernamen,
  `receiver` nimmt jede Authority an) und `/upload` (`workflow_receiver` ebenso).
- **`/detect-content-type` füllt `profile_id`** aus dem Schema (2.1.1: immer
  `null`).
- **Widget:** Import-Kompatibilität mit 2.1.1 wiederhergestellt
  (`metadata-input` zusammen mit `content-type` beim Seitenstart;
  `metadataInput` ohne `metadataset` behält die vom Host gesetzte Inhaltsart);
  der Export enthält nur die Felder des Datensatzes (Kern + gewählte
  Inhaltsart, keine Unterfeld- oder Fremdschlüssel); Datum, Uhrzeit und Zahlen
  werden streng geprüft; mehrwertige URL- und Datumsfelder zeigen ihre Werte;
  Upload-Fehler nennen den Node; Extraktion wird für Screenreader angesagt;
  Standalone-`?apiUrl`/`?dataUrl` nur auf localhost.
- **`/upload`, `/nodes`, `/workflow`, `/upload/verify`:** neue Statuscodes für
  Schreiben ohne Ergebnis, unbekannte Nodes und Zeitlimit (siehe „Breaking“).
  Wird ein bestehender Node (`node_id`) beim Schreiben unterbrochen, nachdem die
  Anfrage das Repository erreicht haben kann, nennt die Antwort den Node und den
  Hinweis „kann teilweise geschrieben sein“ wie beim Zeitlimit.
- Ließen sich die vorhandenen Aspekte eines Nodes nicht lesen, werden sie nicht
  mehr überschrieben (vorher konnte das PUT sie entfernen); Koordinaten bzw.
  Autor werden dann als verloren gemeldet.
- `/upload/verify` meldet einen ungültigen Body wie `/upload` mit Feld und Grund,
  ohne Eingabewerte (API-10).
- `/validate`: Vokabular-Vorschläge („Did you mean …“) mit derselben Toleranz wie
  die übrige Unschärfesuche und für höchstens 5 ungültige Werte je Feld; die
  Prüfung läuft außerhalb der Event-Loop (200 ungültige Berufsfelder blockierten
  den Dienst 12,6 s).
- `/validate`: Koordinatenprüfung nur noch für Felder, deren ID auf
  `latitude`/`lat` bzw. `longitude`/`lon`/`lng` endet – eine Kapazität von 200
  war ein „Latitude out of range“-Fehler.
- `/validate`: ISO-Datumswerte müssen existieren (`2026-02-30` wird gemeldet),
  ebenso Datum und Uhrzeit von ISO-Datum-Uhrzeit-Werten (`2026-02-30T10:00`,
  `2026-03-01T25:00`); die Normalisierung lässt solche Werte wie geschrieben
  stehen, statt sie als gültig umzuformen. Offset, `Z` und Sekundenbruchteile
  bleiben wie bisher.
- `/validate` und `/export/markdown`: `contextName`, `schemaVersion`,
  `metadataset`, `language` und `_schema` mit falschem Typ ergeben 422 statt 500
  bzw. 200 mit Python-Fehlertext.
- `/generate`, `/validate`, `/export/markdown`: abgelehnte Request-Bodies im
  einheitlichen 422-Format ohne Eingabewerte und pydantic-Links.
- `/generate`: unbekannter Kontext, unbekannte Version oder Schema-Datei → 404
  vor Abruf der Quelle, Screenshot und KI-Aufruf (statt 502 `GENERATION_FAILED`).
- `/generate`: ein Text über 10.000 Zeichen zusammen mit `_source_text` gilt als
  neue Quelle (mit Hinweis) statt als zu lange Korrekturanweisung (400) – wie
  mit dem 2.1.1-Widget gewohnt.
- `/generate`: Vokabularwerte aus `existing_metadata` und vom Node werden wie in
  2.1.1 exakt zugeordnet (URI, Wert, Label oder Alternativlabel in jeder Sprache
  → Begriff), aber nicht mehr unscharf; was keinem Begriff entspricht (z. B.
  Altbegriffe), bleibt unverändert erhalten statt still zu verschwinden. Nur die
  in diesem Lauf extrahierten Werte durchlaufen die volle Zuordnung samt
  Unschärfesuche.
- KI-Extraktion: eine teilweise passende Vokabular-Antwort behält ihre Treffer,
  statt erneut gefragt und am Ende ganz verworfen zu werden.
- Zahlen: deutsche Tausenderpunkte werden erkannt, wenn sie eindeutig sind –
  mindestens zwei Punktgruppen (`12.345.678`) oder ein Komma-Teil (`1.234,5`);
  die erste Gruppe beginnt mit 1–9. Eine einzelne Punktgruppe ohne Komma
  (`1.500`, `52.520`, `0.950`) bleibt wie in 2.1.1 eine Dezimalzahl (1,5;
  52,52; 0,95) – KI-Modelle schreiben englische Dezimalzahlen weit häufiger.
  Ganzzahlfelder runden statt abzuschneiden.
- Datum/Uhrzeit: „15. März 2025 14:00“ und „01.05.2026, 18:00“ werden gelesen.
- Öffnungszeiten: Bereiche über das Wochenende („Fr-Mo“), Wochentage nur als
  ganzes Wort („Sonderöffnung“ ist kein Sonntag), Nicht-Text-Einträge brechen die
  Ausgabe-Normalisierung nicht mehr ab.
- `processing.fields_extracted` zählt leere Antworten (`[]`) nicht mehr mit.
- Schema-Fallbacks (Version, Inhaltsarten, Inhaltsart-Prompt) werden
  protokolliert; die Inhaltsart-Erkennung nennt ein unlesbares Schema als Grund
  statt „keine KI-Erkennung konfiguriert“.
- Prompt-Cache-Primer nur noch bei mehr als einem Feld und Texten über 4.000
  Zeichen.
- `/extract-field`: jeder Fehler beim Lesen von core.json führt wie in 2.1.1 zu
  „Feld nicht gefunden“ (404) statt 500.
- OpenAPI: `/generate`-Antwort als flacher Umschlag mit `_origins`,
  `_source_text`, `preview_image_url`, `processing.status` und
  `processing.error` dokumentiert; die Antwort selbst ist unverändert.

Betrieb:

- **`METADATA_AGENT_REQUIRE_REPOSITORY`** (Default `true`): `false` macht ein
  Deployment, das nur erschließt, ohne `WLO_GUEST_*` bereit – wie mit 2.1.1;
  `/upload` und `/nodes` antworten dann `503`, die Startwarnung sagt es. Im
  Helm-Chart unter `env`.
- **`METADATA_AGENT_LLM_MAX_RETRIES` mindestens 1:** `0` ließ jedem KI-Aufruf
  keinen Versuch („HTTP None“); jetzt bricht der Start mit einer klaren Meldung
  ab. Der Wert zählt die Versuche je Aufruf und die zusätzlichen Aufrufe bei
  ungültigem Feldwert.
- **Chromium 153** (Chrome for Testing 153.0.8010.12, playwright 1.63.0; vorher
  145 mit 1.58.0, von keinem Image-Scanner gesehen). NOTICE nachgezogen; Debian
  13 „trixie“ statt 12 als Basis genannt (so war das Image schon).
- **Alarme:** `MetadataAgentServerErrors` (warning: mehr als 5 eigene 5xx in
  10 min je Route, ohne `/health` und `/ready`), `MetadataAgentInternalError`
  (critical: jedes `500 INTERNAL_ERROR`, auch das erste nach dem Pod-Start).
  `MetadataAgentUpstreamUnanswered` wartet 5 min (`for: 5m`), bevor laufende
  Aufrufe als unbeantwortet gelten. promtool-Fälle für alle drei.
- **Dockerfile:** Benutzer, Chromium und dessen Rechte in einer Ebene vor
  `COPY src/` – eine Code-Änderung baut die Browser-Ebene nicht mehr neu. OCI-Labels
  `org.opencontainers.image.version`/`revision` (beide CI übergeben Tag bzw.
  Branch und Commit).
- **`starlette`** ist direkte Abhängigkeit (wird direkt importiert); die
  Sicherheitsuntergrenze 1.3.1 steht jetzt dort statt in den Constraints.
- **Logs (S-17):** Screenshot- und Textextraktions-URLs – auch innerhalb von
  Browser-Fehlermeldungen – ohne Query und Zugangsdaten; ebenso die
  Vorschau-URL in `/upload` und die Screenshot-URL in `/generate`.
- `/upload` und `/nodes` bilden das Schreibergebnis an einer Stelle ab
  (typisiertes `WriteResult`).

CI und Tests:

- **Tests auf dem Betriebssystem des Images:** GitLab-Jobs `run tests`,
  `pip audit` und `pip audit dev` laufen auf `python:3.13-slim` per Digest (Debian
  13, wie das Image) mit uv 0.7.13 statt auf bookworm.
- **Alter des Chromium:** `scripts/check_browser_age.py` in beiden CI meldet die
  Version und bricht ab, wenn sie mehr als zwei Hauptversionen hinter dem
  stabilen Chrome liegt.
- **Update-Vorschläge:** `.github/dependabot.yml` für `uv.lock`, Basis-Images und
  Actions; auf GitLab keine Automatik (UEBERGABE §8). Damit Dependabots
  Vorschläge grün durchlaufen können: ruff-Regeln stehen fest in
  `pyproject.toml` (ruff 0.17 erweiterte seine Standardregeln – 591 Meldungen);
  NOTICE nennt Pakete und Lizenzen ohne Version (die stehen in
  `requirements.txt`); das GitLab-Testimage muss Image und Tag des Dockerfile
  tragen, aber nicht denselben Digest; Python-Versionssprünge (3.13 → 3.14)
  schlägt Dependabot nicht mehr vor, nur neue Digests von 3.13; neue Versionen
  warten eine Woche (`cooldown`). Dependabot hebt `uv.lock` nur teilweise an,
  erzeugt `requirements.txt` aber vollständig neu – vor dem Merge `uv lock` und
  `uv export` auf dem Branch (UEBERGABE §8).
- **Abhängigkeiten aktualisiert** (nur Versionen, die mindestens eine Woche alt
  sind): u. a. fastapi 0.143.0, pydantic 2.14.0, uvicorn 0.54.0, websockets
  17.2, httptools 0.9.0, ruff 0.17.0, pytest 9.1.1; playwright bleibt 1.63.0
  (1.64.0 ist jünger als eine Woche).
- **pip-audit der Entwicklungsabhängigkeiten** in beiden CI, nicht blockierend.
- **kubeconform:** CRD-Katalog auf einen Commit gepinnt.
- **GitLab veröffentlicht nur Tags `v*`.** Das Release-Image wird weiter aus dem
  Tag gebaut statt befördert: `main` wird nur unter dem beweglichen Tag `main`
  veröffentlicht, es gibt also kein unveränderliches Image dieses Commits zum
  Befördern. Basis-Images per Digest und `uv.lock` halten den Neubau gleich.
- **Kein Netz im Testlauf:** `tests/conftest.py` lässt nur Verbindungen zu
  Loopback zu (`RUN_NETWORK_TESTS=1` gibt das Netz für alle Tests frei) und
  lässt einen Test auch dann scheitern, wenn der Code die Ablehnung schluckt.
  Eine Ablehnung zählt für den Test, der den Thread gestartet hat; die eines
  Threads, der seinen Test überlebt, lässt den Lauf am Ende scheitern statt
  den gerade laufenden Test. Er fand einen Test, der
  wirklich die B-API aufrief; der läuft jetzt offline.
- **Coverage-Untergrenze** 79 → 87 % (gemessen 90,1 % mit Zweigen und Browser-Tests).
- **Layer-Test:** `config`, `metrics` und `infra` importieren weder API noch
  Services; `src.main` importiert niemand.
- **Neue Tests:** Weiterleitungen zur eigenen API und ein weitergeleiteter
  Formular-POST in einem echten Chromium (die API läuft dafür auf Loopback),
  eine 50-MB-Datei als Seite (wird nicht ganz übertragen), ein
  `fetch()` zu einem fremden Ursprung ohne CORS-Preflight, der WebSocket-Filter im echten Browser statt per Quelltextsuche,
  `input_source=node_id` (Volltext, Ersatztext, kein Text → `502`).

Doku: `docker run`-Beispiele (README, DEPLOYMENT, INSTALL, Dockerfile) mit
`-p 127.0.0.1:8000:8000 --read-only --tmpfs /tmp --tmpfs /home/appuser
--stop-timeout 120` und `:v3.0.0`; INSTALL: CI-Jobs, Docker-Ebenen,
Paketzahl (20), kein OpenAI-SDK, `b-api-router`, leere Tabelle,
`LLM_AUTH_MISSING` statt „B_API_KEY not configured“; NOTICE Debian 13; toter
Link in ENTWICKLER-UEBERSICHT; UEBERGABE Statuscodes (`404`, `413`, `503`,
`SCHEMA_UNAVAILABLE`), GitHub-Trigger, Update-Automatik in §8 (verantwortlich für
Updates: Team 4); README: `POST /nodes` im Inhaltsverzeichnis, `b-api-router`,
Render mit Chromium, Abschnitt „Entwicklung: Tests, Lint, Typen“, Statuscodes
von `/upload`, `/nodes`, `/workflow` und `/generate`; DEPLOYMENT: `/ready`-Schalter,
110 s auch ohne Ingress, langer Text = neue Quelle; UPLOAD-RESPONSE:
`WorkflowStepResult.skipped`, Schreiben ohne Ergebnis; WIDGET-REFERENZ:
Widget 3.x braucht Agent 3.x; Steckbrief: Stand und Link zum finalen Audit;
`.gitignore` ohne das gelöschte `deploy-widget.ps1`; in diesem CHANGELOG die
Überschriften ohne Version (August 2026) datiert und eingeordnet.

### Korrekturen aus dem Release-Review vom 18.10.

Verhalten, das Clients sehen:

- **Textextraktion (S-06):** `detail` und `error.message` der
  `TEXT_EXTRACTION_*`-Fehler nennen den (internen) Host des
  Textextraktionsdienstes nicht mehr, nur noch „Textextraktionsdienst …“; der
  Host steht im Log.
- **`node_url` mit eigener `source_url`:** Ist sie nicht öffentlich und hat der
  Node keinen gespeicherten Volltext, antworten `/generate`,
  `/detect-content-type` und `/extract-field` jetzt wirklich mit `400` (siehe
  „Breaking“); bisher griff stillschweigend der Ersatztext. Mit Volltext wird
  die URL wie bisher nicht abgerufen.
- **Billige Ablehnungen vor dem Abruf, auch in `/generate`,
  `/detect-content-type` und `/extract-field`:** Ein nicht erlaubter
  `llm_provider` ist `422`, bevor die Quelle abgerufen wird (Reihenfolge bei
  mehreren Fehlern geändert).
- **`/validate` mit Objekten in Vokabularfeldern:** Ein Objekt oder eine Liste
  in einem geschlossenen bzw. SKOS-Feld (z. B. `ccm:taxonid: [{"uri": …}]`) ist
  eine Warnung „… is no concept of the closed vocabulary“ statt `500`.
- **Geocoding im Zeitlimit von `/generate`:** Das Geocoding bekommt nur, was vom
  Zeitlimit übrig ist (abzüglich 1 s Reserve). Lookups, deren Photon-Slot danach
  läge, entfallen, ein laufender wird abgebrochen. Die Orte bleiben ohne
  Koordinaten, `processing.warnings` sagt es – statt `504 GENERATION_TIMEOUT`
  mit Verlust aller Felder.
- **Vom Redakteur gefüllte Felder (`_origins: "user"`) gehen nicht mehr an die
  KI**, außer sie stehen in `regenerate_fields` – dann ersetzt der neue Wert den
  alten (Herkunft `ai`) und zählt in `fields_extracted`; sonst bleibt der Wert
  des Redakteurs und kostet keinen Aufruf.
- **URI-Felder mit SKOS-Vokabular (`dpt:concept`):** Ein exaktes Label wird
  gleich beim ersten Aufruf auf die Konzept-URI abgebildet (vorher vier
  Aufrufe), und nach dem letzten Versuch bleibt kein Wert, der kein Konzept ist.
- **Leere Antworten (`""`) für optionale Vokabularfelder** gelten als „kein
  Wert“ und werden nicht mehr bis zu dreimal neu angefragt.
- **Zahlen und Wahrheitswerte in geschlossenen Vokabularen** (Import- und
  Schreibpfad): `0` → `"0"`, `true` → `"true"`, Werte außerhalb der Skala
  (z. B. `7` bei `ccm:oeh_quality_relevancy_for_education`) entfallen – wie
  bereits im KI-Pfad.
- **Antwort `200` ohne JSON vom KI-Dienst** (z. B. HTML eines Proxys) ist
  `LLM_UPSTREAM_ERROR` und wird wiederholt wie ein 5xx, statt „Internal error
  while extracting …“ mit Stacktrace je Feld.
- **`/generate` mit einem JSON-Body, der kein Objekt ist,** antwortet `400` wie
  die anderen Endpunkte statt `422` mit Python-Meldung.
- **„Did you mean …?“ bei `/validate`** schlägt nur noch Konzepte vor, deren URI
  auf den Wert endet (z. B. `460`), oder solche mit ähnlichem Label.
- **`/upload`: das Vorschaubild kostet keinen fertigen Node mehr:** Der
  Screenshot wird nur so lange abgewartet, wie das Zeitlimit abzüglich 15 s
  Reserve für den Workflow erlaubt; danach geht der Upload ohne Vorschau weiter
  (`preview.success: false`, Grund in `error`). Das Zurücklesen
  (`return_full_node`, `/nodes`) läuft außerhalb des Zeitlimits mit eigenen
  10 s: ein langsames Zurücklesen ergibt `node_full: null` statt `504` und
  verwirft nichts – auch ein Client-Abbruch danach nicht.
- **Kein Verwerfen nach dem Workflow:** Ein unerwarteter Fehler nach dem
  Workflow-Schritt verwirft den Node nicht mehr, die Antwort nennt ihn in
  `node`. Zahlen als Titel oder Beschreibung führen nach vollständigem
  Schreiben nicht mehr zu `500`.
- **Verlorene Create-Antwort (D-01):** Ein Kandidat mit `ccm:wf_status`
  (gleichzeitiger, schon übergebener Upload derselben URL) wird nicht mehr als
  eigener Node verworfen; die erlaubte Uhrabweichung sinkt von 30 s auf 3 s.
- **`POST /node`:** sucht bei verlorener Antwort im Eingangsordner und liefert
  den gefundenen Node als Erfolg (statt `502` und Doppelanlage beim
  Wiederholen); endet nach `METADATA_AGENT_UPLOAD_TIMEOUT` mit
  `504 UPLOAD_TIMEOUT`; unerwartete Fehler nennen nur den Fehlertyp.
- **Repository-Ausfall ist kein `404`:** Kann ein übergebener Node wegen eines
  Repository-Fehlers (5xx, Timeout, Verbindung) nicht gelesen werden,
  antworten `/upload`, `/nodes` und `/screenshot` mit `node_id` jetzt `502`
  (`node_unreadable`); `404` bleibt für 403/404 des Repositorys.
- **`/screenshot` mit `node_id`:** prüft das Bearbeitungsfenster vor der
  Aufnahme (ein abgelehnter Node belegt keinen Browser-Slot) und endet nach
  `METADATA_AGENT_UPLOAD_TIMEOUT` mit `504 UPLOAD_TIMEOUT`.
- **Teil-Update per `/nodes` mit `write_extended_data: true`:** überschreibt
  einen genauen Lernressourcentyp (`ccm:oeh_lrt`) des Nodes nicht mehr mit dem
  groben abgeleiteten. Nur teilweise geschriebene erweiterte Felder gelten
  nicht mehr als Erfolg; `error` nennt die fehlenden.
- **Fehlertexte (S-06):** `422` von `/upload` nennt Feld und Grund ohne
  Eingabewerte („Ungültiger Request-Body: …“); ein fehlgeschlagener
  Vorschaubild-Upload nennt nur den Fehlertyp. Ein Zeitlimit beim Update vor
  dem ersten Schreiben heißt „wurde nicht verändert“.
- **OpenAPI:** `ErrorResponse` deklariert `node` und `discarded_node`.
- **Fehlertexte (S-06), Rest:** Schlägt das Schreiben in `/upload` oder
  `/nodes` mit Timeout, Verbindungs- oder unerwartetem Fehler fehl, nennt
  `error` nur noch den Fehlertyp (z. B. „Timeout bei der Verbindung zum
  Repository (ReadTimeout)“), ebenso ein nicht verfügbares Vorschaubild; die
  Einzelheiten stehen im Log. `422` von `/nodes` und `/workflow/{node_id}`
  nennt wie `/upload` nur Feld und Grund – ohne Eingabewerte und pydantic-Links.
- **Fehlertexte (S-06), letzte Stellen:** Auch `/upload/verify`,
  `/workflow/{node_id}`, die Ergebnisse je Sammlung, Workflow-Schritt und Feld
  (`collections`, `workflow`, `field_errors`) sowie Fehler beim Laden oder
  Dekodieren eines Vorschaubilds nennen nur noch einen neutralen Text mit dem
  Fehlertyp (z. B. „Verknüpfung fehlgeschlagen (ReadTimeout)“); der
  Originaltext, der interne Hosts oder Adressen enthalten kann, steht im Log.
- **Vorschaubild ohne Workflow:** Ohne Workflow-Schritte (Standard bei
  `/nodes`) darf das Vorschaubild das Zeitlimit bis auf 1 s ausschöpfen; die
  15 s Reserve gilt nur, wenn ein Workflow folgt.
- **Übergebener Node nach Fehler:** Bleibt ein per `node_id` übergebener Node
  nach einem Fehler erhalten, weil er schon im Workflow oder vollständig
  geschrieben ist, nennt die Antwort ihn in `node`.
- **Verlorene Create-Antwort, gleichzeitiger Upload:** Ein Kandidat, der nach
  dem Anlegen schon geändert wurde (oder dessen Änderungszeit fehlt), gilt als
  Node eines gleichzeitigen Uploads derselben URL und wird nicht verworfen; die
  Antwort bittet, den Eingangsordner zu prüfen.

Betrieb:

- **Startwarnungen:** fehlende `WLO_GUEST_USERNAME`/`WLO_GUEST_PASSWORD` (wie
  in OPS-12 vorgesehen), und Fristen, die samt Aufräumen und preStop die 120 s
  von Ingress und Grace-Period überschreiten
  (`METADATA_AGENT_UPLOAD_TIMEOUT` + 15 s, `METADATA_AGENT_GENERATE_TIMEOUT` + 10 s).
- **Helm-Chart:** `helm install` warnt, wenn Ingress und NetworkPolicy an sind –
  der Namespace des Ingress-Controllers gehört in
  `networkPolicy.ingressNamespaces`, sonst blockiert die Policy ihn (auch in
  `values.yaml` und der Chart-README). `sharedLlmLimit` mit `replicaCount=0`
  bricht das Rendern nicht mehr ab („integer divide by zero“).
- **docker-compose / `.env.template`:** Eine vorhandene `.env` erreicht den
  Container ganz (`env_file`, optional) – bisher nur die gelisteten Variablen,
  `B_API_IGNORE_CACHE`, `SCREENSHOT_*` & Co. kamen nie an.
  `METADATA_AGENT_UPLOAD_TIMEOUT` steht in Liste und Vorlage. `OPENAI_API_KEY`
  und `B_API_KEY` sind in der Vorlage leer: ein Platzhalter ließ `/ready`
  bestehen.
- **Logs:** Client-Fehler (4xx) werden einzeilig geloggt – ein Zeilenumbruch
  aus dem Request konnte mit `LOG_FORMAT=text` eine Logzeile fälschen.
  Blockierte Anfragen des Screenshot-Browsers erscheinen ohne Query-String und
  Zugangsdaten.
- **Aufräumen nach Abbruch:** Suche des verlorenen Nodes und Verwerfen haben
  je ein Gesamtbudget von 5 s (bisher 5 s pro httpx-Phase).
- **`/upload/verify`:** Ein vorübergehend nicht lesbares Schema bleibt nicht
  mehr bis zum Neustart unvollständig im Cache der Vokabularfelder.
- **Vorschau-Abruf:** meldet sich als `MetadataAgentAPI/3.0.0` (bisher 2.0).
- **Aufräumen abgeschirmt:** Das Verwerfen nach Zeitlimit oder Fehler läuft
  weiter, wenn der Client in diesem Moment geht oder der Pod herunterfährt; ein
  halb geschriebener Node bleibt so nicht mehr liegen.
- **Tracing-Header (B3, `x-request-id`)** gehen mit `LLM_PROVIDER=openai` nicht
  mehr an api.openai.com, nur an die B-API.
- **Robustheit:** Ein `startDate`/`endDate`, das kein Text ist, überspringt
  nicht mehr die ganze Ausgabe-Normalisierung; Fehler der Agent-Logik bei der
  Inhaltsart-Erkennung stehen mit Traceback im Log; bricht eine
  Feldextraktion unerwartet ab, werden die übrigen Feldaufrufe gestoppt.
- **Schema-Cache** je aufgelöster Version („latest“, „2.0.0“ und „v2.0.0“ sind
  ein Eintrag).

- **Aufgeräumt:** `scripts/deploy-widget.ps1` entfernt – ein zweiter, veralteter
  Deploy-Weg, der den Zielordner samt Lizenztexten löschte und keine
  Versionsprüfung kannte; maßgeblich ist `npm run build:elements` im
  Widget-Repository. Die Beispielseiten unter `/widget/examples` hörten auf
  Events, die das Widget nicht kennt (`extractionComplete`,
  `contentTypeDetected`), und setzten Attribute (`show-input`, `show-controls`,
  `node-id`) und Layouts (`compact`, `minimal`), die es nicht gibt; sie nutzen
  jetzt `uploadResult`, `show-input-area`, `controls` und die sieben Layouts.

CI und Tests:

- **GitLab rendert den Chart in den Tests:** `tests/test_helm_chart.py`
  übersprang die gerenderten Prüfungen dort immer (kein helm im Test-Image).
  Der Job `helm binary` kopiert helm aus dem gepinnten Image; `REQUIRE_HELM=1`
  (beide CI) lässt ein fehlendes helm scheitern statt überspringen.
- **Kein Netz im Testlauf:** Der Abgleich mit dem veröffentlichten
  LRT-Vokabular läuft nur mit `RUN_NETWORK_TESTS=1`.

Doku: `/health`-Beispiele (README, INSTALL) ohne Provider/Auth-Modus;
Fehlercodes je Endpunkt in der README (`/upload` 504 `UPLOAD_TIMEOUT`,
`/screenshot` mit `node_id` 403/404/502 und 504 `UPLOAD_TIMEOUT`, `/detect-content-type` und `/extract-field` 504
`GENERATION_TIMEOUT`, `/upload/verify` 422 bei Schemaverstoß, nicht öffentliche
`source_url` 400 mit eigenem Grund); DEPLOYMENT „Fehler melden“ (jede
Fehlerantwort hat `error.request_id` und eine Logzeile), Update-Abschnitt mit
`METADATA_AGENT_UPLOAD_TIMEOUT`; INSTALL-Paketliste nach `pyproject.toml`;
WIDGET-REFERENZ §9 nach dem Stand von `/widget/info`; in diesem Abschnitt die
Breaking-Zeile zu `node_url`, die ruff-Grenze (15) und Zeilenzahlen.

### Breaking: Statuscodes

Vereinheitlicht nach „wer ist schuld“ (Audit API-01). Antwortformen bleiben.

| Endpunkt | Fall | bisher | jetzt |
|---|---|---|---|
| `/detect-content-type`, `/extract-field` | Quelle (URL, Node) nicht lesbar | 400 | **502** wie bei `/generate` |
| `/generate`, `/detect-content-type`, `/extract-field` | eigene `source_url` bei `input_source=node_url` nicht öffentlich (ohne gespeicherten Volltext) | Ersatztext aus Titel/Beschreibung des Nodes (200), ohne beides Fehler | **400** wie bei `input_source=url`; eine nicht öffentliche `ccm:wwwurl` des Nodes führt weiter zum Ersatztext mit Hinweis |
| `/upload/verify` | Node existiert nicht | 502 | **404** wie bei `/nodes` |
| `/upload/verify` | Body ist JSON, aber ungültig | 400 | **422** wie bei `/nodes`, `/upload` |
| `POST /node` | Repository-Fehler beim Anlegen | 400 | **502** (fehlender Titel bleibt 400) |
| `/screenshot` mit `node_id` | Node außerhalb des Bearbeitungsfensters | 200, Vorschau gesetzt | **403** (`404` unbekannt), wie `/upload` |
| alle | unerwartete Ausnahme | 500 als Klartext ohne Request-ID/CORS | **500** JSON mit `error.code: INTERNAL_ERROR`, Request-ID und CORS |
| `/generate` | Lauf länger als `METADATA_AGENT_GENERATE_TIMEOUT` | 504 vom Ingress nach 120 s; ohne Ingress (Docker, Compose) unbegrenzt | **504** vom Agenten mit `error.code: GENERATION_TIMEOUT` nach 300 s (bis 19.10.: 110 s) – auch ohne Ingress. Wer dort längere Läufe braucht, erhöht `METADATA_AGENT_GENERATE_TIMEOUT` |
| `/generate` (API-06) | unbekannter Kontext, unbekannte Version oder Schema-Datei | 502 `GENERATION_FAILED` nach Abruf und KI-Aufruf | **404** vor Abruf der Quelle, Screenshot und KI-Aufruf |
| `/upload`, `/nodes` (API-08) | Schreiben ohne Ergebnis (nichts angelegt, nichts geändert) | 200 `success:false` | **422** `VALIDATION_ERROR` (Objektform verletzt), **422** `SCHEMA_UNAVAILABLE` (Kontext/Version unbekannt), **502** `UPSTREAM_ERROR` (Repository lehnte ab/nicht erreichbar); `detail` = bisheriger `error`-Text, dazu `discarded_node`/`node`/`step`/`field_errors`. Dubletten und Teilergebnisse bleiben 200 |
| `/workflow/{node_id}` (API-12) | unbekannter Node / kein Zeitlimit | 200 `success:false` / unbegrenzt | **404**, kein Status gesetzt / **504** `UPLOAD_TIMEOUT` nach `METADATA_AGENT_UPLOAD_TIMEOUT` mit `node` |
| `/upload/verify` (API-11) | Repository antwortet 403 (kein Zugriff) | 502 | **404** wie bei 404 |
| alle | Anfrage des eigenen Screenshot-Browsers (Header `X-Metadata-Agent-Screenshot`) | wie jede Anfrage | **403** `SCREENSHOT_BROWSER_REFUSED` (S-15; andere Clients senden den Header nicht) |
| alle mit JSON-Body | ungültiges JSON über 1 MB (S-16) | repariert, wenn möglich | **400** ohne Reparaturversuch (darunter wie bisher) |

`/upload`, `/nodes` und `/workflow` antworten mit 200 und `success:false` nur
noch bei Teilergebnissen (Node existiert) und Dubletten; ein Schreiben ohne
Ergebnis ist 422/502, ein unbekannter Node bei `/workflow` 404.

### Korrekturen aus dem Audit vom 18.10. (`docs/audits/2026-10-18-audit-3.0.0-nachkorrektur.md`)

Verhalten, das Clients sehen:

| Bereich | bisher | jetzt |
|---|---|---|
| Fehlerantworten (API-03) | drei Formen; `HTTPException` und 422 nur mit `detail` | jede Fehlerantwort der API mit `detail` (wie bisher) **plus** `error` mit `code`, `message`, `request_id`; in der OpenAPI-Spec als `ErrorResponse` (4XX/5XX). Das öffentliche `/ready`-503 bekam beides erst mit dem finalen Audit |
| `/upload`, `/nodes` (D-04) | kein Gesamt-Zeitlimit | **504** `UPLOAD_TIMEOUT` nach `METADATA_AGENT_UPLOAD_TIMEOUT`; die Antwort nennt den Node: `discarded_node` (verworfen) oder `node` (nicht verwerfbar oder schon im Workflow) |
| `/detect-content-type`, `/extract-field` (P-01) | kein Gesamt-Zeitlimit | **504** `GENERATION_TIMEOUT` wie `/generate` |
| `/extract-field` (API-05) | unbekanntes Feld erst nach dem Abruf der Quelle 404 | 404 vor dem Abruf |
| Korrekturanweisung (API-05) | ohne eigene Grenze | höchstens 10.000 Zeichen, sonst **400** |
| Zeiten (L-10) | `HH:MM:SS` oder `HH:MM` je nach Pfad | immer `HH:MM`, Datum mit Uhrzeit `YYYY-MM-DDTHH:MM` (eine Zeitzone wie `Z` oder `+02:00` bleibt) |
| Geschlossene und SKOS-Vokabulare (L-07–L-09) | Werte außerhalb des Vokabulars blieben teils stehen; Fach/Bildungsstufe/Ressourcentyp ohne Wiederholung | nur Konzepte; passt nichts, fragt der Agent das Modell erneut, danach bleibt nur, was passt |
| Zahlen (L-11) | `"NaN"`, `"inf"`, `"null"` wurden Zahlen | kein Wert |
| Vorschaubild (S-10) | jeder 200-Body als PNG | nur PNG, JPEG, GIF, WebP nach den ersten Bytes |
| öffentliches `/health` (S-08) | nannte Provider und Auth-Modus | nur `status` und `version`; Details auf dem internen Port |
| `/ready` (OPS-12) | bereit auch ohne `WLO_GUEST_USERNAME`/`-PASSWORD`; Gründe in der Antwort | **503** ohne sie (`/upload` und `/nodes` antworten dann ohnehin 503); die Gründe nur noch im Log und auf dem internen Port |
| Repository-Fehlertexte (S-06) | ganzer Antworttext (mit Stacktrace) | Status und die kurze `message` des Repositorys |

Weitere Korrekturen:

- **Verlorene Anlege-Antwort (D-01):** Kommt die Antwort auf „Node anlegen“
  nicht an, sucht der Agent den Node im Eingangsordner und verwirft ihn.
- **Feld-für-Feld-Fallback mit Budget (D-02):** höchstens 30 s, danach als
  „Nicht versucht“ gemeldet.
- **Ein Schreibweg (M-02):** `/upload` und `/nodes` laufen durch denselben
  Ablauf; `/upload` meldet ein fehlgeschlagenes Vorschaubild jetzt wie `/nodes` (D-05).
- **Sicherheit:** WebSockets im Screenshot-Browser nur zu öffentlichen Hosts
  (S-09); der Vorschau-Abruf verbindet sich mit genau der geprüften Adresse
  (DNS-Rebinding, S-04); `X-Content-Type-Options: nosniff` und
  `Referrer-Policy: no-referrer` (S-12); interne Hosts nicht mehr in Fehlertexten (S-06).
- **Korrekturmodus (L-13):** nur noch durch den Abschnitt, den der Agent selbst
  anhängt, nicht durch „--- UPDATE“ im Seitentext. „LETZTER VERSUCH“ steht nur
  im letzten Versuch (L-14).
- **Grobe LRT (L-03):** überschreibt die extrahierte auch in der verschachtelten
  Body-Form nicht mehr.
- **Programmfehler (P-06)** wiederholen keine LLM-Aufrufe mehr.
- **Performance:** unscharfer Vokabular-Abgleich mit begrenzter Editierdistanz
  (220 Labels × 50 Werte: 1,4 s → 0,01 s, P-05); Geocoding-Takt ohne Lock über
  dem Warten (P-03); Prompt-Cache auch für `b-api-router` mit `openai/…`-Modell (P-02).
- **Wartbarkeit:** keine Funktion mehr über Komplexität 15 (ruff-Grenze jetzt 15),
  eine Regel für Metadaten-Schlüssel (L-05), Modelle importieren keine Services (M-05),
  verschluckte Ausnahmen werden geloggt (L-04), Datum/Zeit/Zahl an einer Stelle
  (`src/utils/dates_numbers.py`).
- **Betrieb, CI, Doku, Lizenz:** siehe DEPLOYMENT.md („Erste Installation auf
  Staging“, „Rollback“, „Grenzen der NetworkPolicy“); Lizenz Apache-2.0 (LICENSE, NOTICE).

### Korrekturen aus dem Audit (`docs/audits/2026-10-17-audit-3.0.0.md`)

- **Vorschaubilder ohne SVG (S-03):** SVG wird abgelehnt, egal ob deklariert,
  vom Host geliefert oder hinter einem anderen Typ versteckt – ein SVG kann
  Skript tragen.
- **`/screenshot` respektiert das Bearbeitungsfenster (S-02):** Die Vorschau
  geht nur in Nodes, die `/upload` auch schreiben dürfte; eine einzige
  Implementierung (`RepositoryService.upload_preview`) statt zwei.
- **Vorschau vor dem Workflow (D-03):** `/upload` setzt das Screenshot-Bild,
  bevor der Node in die Redaktionsqueue geht.
- **Keine Tracing-Header an fremde Hosts (S-07):** Vorschau-URLs werden mit
  einem eigenen Client ohne B3-/Request-ID-Header abgerufen.
- **Client-Leck bei Abbruch (L-06):** `/generate` schließt den LLM-Client einer
  Anfrage mit eigenem Modell auch bei Abbruch.
- **Eingangsquelle an einer Stelle (A-01):** `/detect-content-type`,
  `/extract-field` und `/generate` lesen Text, URL und Node über
  `src/api/input_resolver.py`.
- **Korrekturanweisung bleibt erhalten (L-02):** Bei URL- und Node-Quellen
  ging sie verloren, bei langen Texten wurde sie abgeschnitten. Sie steht jetzt
  nach dem geholten Text, Platz dafür ist beim Kürzen reserviert.
  `_source_text` ist der reine Quelltext – ohne Anweisung und ohne den Block
  „BISHERIGER STAND“, der sonst je Runde wuchs und als `ccm:oeh_extendedText`
  ins Repository ging.
- **Eingaben der Redaktion bleiben (U-06):** Ein Feld mit Herkunft `user` in
  `_origins` wird bei einer neuen Generierung nicht überschrieben und bleibt
  `user` – bisher konnte ein getippter Titel durch den Seitentitel ersetzt
  werden, und die Herkunft wurde immer zu `ai`.
- **Gesamt-Zeitlimit (P-01):** `METADATA_AGENT_GENERATE_TIMEOUT` (110 s) begrenzt
  einen ganzen `/generate`-Lauf samt Abruf der Quelle (URL, Node); laufende
  Feldaufrufe werden abgebrochen.
- **Geocoding begrenzt (P-03):** höchstens 10 Abfragen je Anfrage (Orte mit
  Koordinaten zählen nicht), der Rest bleibt unverändert. Photon erlaubt einen
  Aufruf pro Sekunde für den ganzen Prozess.
- **Helm (S-01):** NetworkPolicy (eingehend nur aus der B-API und für
  Prometheus, ausgehend DNS, B-API und Internet ohne private Netze),
  `automountServiceAccountToken: false`. Werte und Annahmen: UEBERGABE §6.
- **Doku (S-05):** Lesezugriffe mit dem Service-Konto als offene Entscheidung
  in UEBERGABE §3.

### Audit Phase 3 und 4 (`docs/plans/2026-10-17-audit-fixes-phase-3-4.md`)

- **Gottfunktionen zerlegt (M-01):** `metadata_service.py` (1.426 Zeilen) ist
  geteilt in `metadata_generation` (Schritte eines Laufs), `metadata_validation`
  und `metadata_export`; `/generate`, `/extract-field` und der Feld-Prompt sind
  in benannte Schritte zerlegt. Die fünf schlimmsten Funktionen haben jetzt
  Komplexität ≤ 15 (vorher 31–53); ruff lässt in der CI keine Funktion über 15
  zu. Ausgaben unverändert: Charakterisierungstests (15 Abläufe, 218 Feld-Prompts
  in allen Varianten) gegen den Stand vor der Teilung.
- **Toter Code entfernt (M-03):** KI-Normalisierung ohne Aufrufer und ein
  unerreichbarer Zweig der Validierung (Zahlwörter).
- **Typprüfung (M-04):** mypy prüft API, LLM-, Repository- und Metadaten-Module
  in der CI; was die Mixins voneinander brauchen, ist deklariert statt nur
  beschrieben.
- **Coverage (T-01):** gemessen in beiden CI-Systemen, Untergrenze 79 %
  (vorher nicht gemessen, 73 % vor diesen Korrekturen). Neue direkte Tests für
  Datum/Zahl; dabei behoben: „14:30 Uhr“ wurde nicht als Uhrzeit erkannt.
- **Abhängigkeiten (DEP-01/02):** `requirements.txt` ist der Export von `uv.lock`
  mit Hashes; `pip-audit` (Python), `npm audit` (Widget) und Trivy (Image,
  kritisch und behebbar) laufen in der CI.
- **Betrieb (OPS-01–04):** `FORWARDED_ALLOW_IPS` im Chart (`forwardedAllowIps`) –
  hinter TLS lieferte `/widget/info` sonst `http://`-URLs; schreibgeschütztes
  Root-Dateisystem; Basis-Images per Digest, `/app` gehört root; GitHub
  Actions per Commit-SHA; das gebaute Image wird schreibgeschützt gestartet und
  muss `/health` beantworten, in GitLab auch in Merge Requests; ein Port-Wert
  statt zwei.
- **Doku (DOC-01):** Update-Kapitel 3.0.0 und „Hinter TLS“ in DEPLOYMENT.md,
  Versionsbeispiele auf 3.0.0.

### Neuer Aufbau

Die vier großen Dateien sind nach Zuständigkeit aufgeteilt; der Code wurde
verschoben, nicht umgeschrieben (Plan und Nachweise:
`docs/plans/2026-10-17-refactoring-3-0-0.md`; Audit-Korrekturen:
`docs/plans/2026-10-17-audit-fixes-phase-1-2.md`).

- **`src/main.py`** (3.530 → 275 Zeilen) baut nur noch die App. Die Endpunkte
  liegen in `src/api/routers/` (ein Router je Bereich, in der bisherigen
  Reihenfolge eingebunden), gemeinsame Abhängigkeiten in `src/api/deps.py`,
  Fehlerantworten in `src/api/responses.py`, das OpenAPI-Dokument in
  `src/api/openapi.py`.
- **`llm_service.py`** (2.107 → 365 Zeilen) behält die Feld-Extraktion; Transport,
  Durchsatz-Gates, Prompts, Wert-Normalisierung, Datum/Zahl und
  Inhaltsart-Erkennung liegen in `llm_transport`, `llm_gate`, `llm_prompts`,
  `llm_values`, `llm_dates_numbers`, `llm_content_type` (Mixins von
  `LLMService`; jedes nennt im Docstring, was es braucht).
- **`repository_service.py`** (1.968 → 1.189) behält die Abläufe; Node-Operationen,
  Metadaten-Schreibpfad, Prüfen/Workflow, Vorschaubild und gemeinsame Hilfen
  liegen in `repository_nodes`, `repository_metadata`, `repository_review`,
  `repository_preview`, `repository_support`. Die Hilfen tragen öffentliche
  Namen (`get_repository_config`, `first_value` …); `_get_repository_config`
  bleibt aus `repository_service` importierbar.
- **Logger-Namen:** Fehlerzeilen (`❌ [<id>] …`) kommen von `src.api.responses`,
  Endpunkt-Logs von `src.api.routers.generate`, `…upload`, `…screenshot`;
  KI-Aufrufe von `src.services.llm_transport` und `…llm_values`, das Repository
  von `src.services.repository_nodes`, `…repository_metadata` und
  `…repository_service`. `src.main` loggt nur noch Start und Ende.
- **Importe:** Alle öffentlichen Namen, die `llm_service` und
  `repository_service` in 2.1.1 definiert haben, bleiben dort importierbar
  (`tests/test_import_compatibility.py`). Nicht mehr: die internen
  `_`-Helfer von `repository_service` (heute öffentlich in
  `repository_support`, z.B. `first_value`; nur `_get_repository_config` bleibt
  als Alias) und alles außer `app` aus `src.main` – Endpunkte, `settings` und
  Hilfen liegen in `src/api/`.
- **Für Entwickler:** Tests ersetzen Abhängigkeiten über `src.api.deps`
  (`monkeypatch.setattr(deps, "get_metadata_service", …)`), nicht mehr über
  `src.main`. FastAPI ≥ 0.142 führt eingebundene Router als einen Eintrag in
  `app.routes`; die tatsächlichen Routen liefert
  `fastapi.routing.iter_route_contexts`. Neu: `tests/test_relative_imports.py`
  prüft, dass jeder relative Import in `src/` auflösbar ist – Modul und
  importierte Namen, auch innerhalb von Funktionen, die nur selten laufen.
- **Webkomponente 3.0.0:** neu gebaut, siehe deren CHANGELOG.

## 2.1.1 — 2026-10-17 — Korrekturen aus dem Review

> **Update:** nichts zu ändern. Wer Alarme oder Abfragen auf
> `metadata_agent_llm_requests_total` gebaut hat: der Zähler hat zwei Labels mehr.
> Der Helm-Chart (0.2.1) verlangt `image.repository`.

- **Alarme:** `metadata_agent_llm_requests_total` hat die Labels
  `auth_source` und `config`. Kritisch melden nur noch Fehler mit dem eigenen
  Schlüssel und dem konfigurierten Modell; ein abgelehnter weitergereichter
  Schlüssel oder ein per Anfrage gewähltes, unbekanntes Modell warnen erst
  gehäuft (zwei neue Regeln, jetzt zehn). Ein unbekanntes Standardmodell
  (404) ist jetzt ebenfalls kritisch.
- **Router-Fehler** werden nur noch beim Provider `b-api-router` als solche
  gelesen; bei anderen Providern ist ein 400 „Unsupported parameter“ ein
  gewöhnlicher abgelehnter Request ohne Rat zu `LLM_REQUEST_STYLE`.
- **`llm_model`** in Anfragen: höchstens 200 Zeichen aus `A-Z a-z 0-9 . _ :
  / @ -`, sonst `422`.
- **B-API-Adressen** (`…_OPENAI_BASE`, `…_ACADEMICCLOUD_BASE`) verlieren wie
  die Router-Adresse einen abschließenden `/`.
- **`METADATA_AGENT_INTERNAL_HOST`** (Default `0.0.0.0`): `::` lässt den
  internen Port in IPv6-Clustern lauschen.
- **Helm-Chart 0.2.1:** kein Ingress im Installationsbeispiel, Warnung in
  `values.yaml` und bei `helm install` (Ingress im Modus `auto`),
  PodDisruptionBudget, `image.repository` als Pflichtwert, Hinweis zur
  Secret-Rotation.
- **Webkomponente 2.1.1:** neu gebaut; nur die Versionsnummer ändert sich.

## 2.1.0 — 2026-10-14 — Authentifizierung über den B-API-Proxy, ehrliche Fehler

> **Update bestehender Deployments:** keine Änderung nötig; vorher prüfen, ob
> andere Programme als das Widget die API aufrufen (Fehler kommen jetzt als
> HTTP-Fehler) und ob ein Proxy einen fremden `Authorization`-Header mitschickt.
> Einzelheiten: [docs/update-2.0-auf-2.1.md](docs/update-2.0-auf-2.1.md).

Anlass: In Prod antwortete die B-API auf jeden KI-Aufruf mit `403`. `/generate`
meldete trotzdem `success: true` mit 0 von 50 Feldern und ordnete die Ressource
stillschweigend als Bildungsmaterial ein. Außerdem kann die B-API den Agenten
künftig über ihren Proxy aufrufen und dabei den Schlüssel des Aufrufers
weiterreichen.

- **Umschaltbare B-API-Authentifizierung** über
  `METADATA_AGENT_B_API_AUTH_MODE`. Mit `auto` (Default) wird ein vom Proxy
  weitergereichter `Authorization: Bearer …` (`/api/v1/proxy/<dienst>`,
  `forwardApiKey`) unverändert genutzt, sonst `B_API_KEY` als `X-API-KEY` wie
  bisher. `forward` nutzt nur den weitergereichten Schlüssel und antwortet ohne
  ihn mit `502` (`LLM_AUTH_MISSING`); `static` nutzt nur `B_API_KEY`. An die
  B-API geht genau ein Auth-Header. Der Schlüssel gilt nur für die Anfrage, die
  ihn brachte: Er wird nicht gespeichert, nicht geloggt und nicht zwischen
  gleichzeitigen Anfragen geteilt. Das gilt nur für die `b-api-*`-Provider.
  `processing.auth_source` meldet, woher der Schlüssel kam (`forwarded`,
  `static`). Hinter dem Proxy darf der Dienst nur über die B-API erreichbar sein.
- **Ehrlicher Status in `/generate`:** `processing.status` ist `success`,
  `partial` oder `failed`, dazu kommen `fields_failed` und bei Problemen
  `processing.error` (`code`, `message`). Wird gar kein Feld generiert,
  antwortet `/generate` nicht mehr mit `200`, sondern mit `502` (Rate-Limit
  `503`), mit lesbarem `detail` und `error.code`, z.B.
  `LLM_AUTH_FORBIDDEN`, `LLM_RATE_LIMITED` oder `LLM_UNREACHABLE`. **Clients, die
  bisher nur `200` erwarteten, müssen diese Fehler auswerten.** Ein `401`/`403`
  der B-API wird bewusst als `502` weitergegeben, damit edu-sharing ihn nicht
  als abgelaufene Sitzung deutet. `/detect-content-type` und `/extract-field`
  melden KI-Fehler genauso.
- **Früher Abbruch:** Antwortet die B-API mit `401`, `403` oder `404`, kommt
  nach allen Retries noch ein `429` oder ist der KI-Dienst nicht erreichbar,
  endet die Feldextraktion sofort. Es laufen dann höchstens so viele Aufrufe
  wie Worker, statt jedes Feld einzeln scheitern zu lassen. Ein Feld, dessen
  KI-Aufruf scheitert, wird nicht mehr zusätzlich neu angefragt. Wiederholt
  werden nur `429`, `500`, `502`, `503` und `504`, nach dem letzten Versuch
  aber ohne Wartezeit. Alle anderen Fehlercodes (etwa `400`, `401`, `403`,
  `404`) werden nicht wiederholt.
- **Inhaltsart nicht mehr stillschweigend „Bildungsmaterial“:** Die Antwort der
  KI wird gegen die vorhandenen Schemata geprüft. Ist sie unbrauchbar, wird die
  Schlüsselwort-Erkennung genutzt und als solche gemeldet
  (`processing.content_type_detection.method: "keywords"`,
  `confidence: "low"`, Hinweis in `warnings`). `/detect-content-type` meldet
  `method` und `warnings` ebenso. Ein fataler KI-Fehler bricht schon bei der
  Erkennung ab.
- **Textextraktion mit Fehlercodes:** Für `input_source=url` gilt:
  - Zeitüberschreitung → `504` (`TEXT_EXTRACTION_TIMEOUT`,
    `TEXT_EXTRACTION_CONNECT_TIMEOUT`);
  - Dienst nicht erreichbar oder Fehlerantwort → `502`
    (`TEXT_EXTRACTION_UNREACHABLE`, `TEXT_EXTRACTION_HTTP_ERROR`,
    `TEXT_EXTRACTION_FAILED`);
  - leerer Text → `422` (`TEXT_EXTRACTION_EMPTY`).

  Die Meldung nennt jeweils den Host des Extraktionsdienstes.
- **`node_url`-Rückfall wird gemeldet:** Ist der Webseitentext nicht abrufbar,
  nutzt der Agent wie bisher Titel, Beschreibung und Schlagworte des
  Repository-Eintrags. Das sagt er jetzt dazu, mit Ursache: in
  `processing.warnings` (`/generate`, `/extract-field`) bzw. `warnings`
  (`/detect-content-type`).
- **Anfrage-ID:** Jede Antwort trägt `X-Request-ID`. Fehlerantworten mit
  Fehlercode (`error.code`) wiederholen sie als `error.request_id`, und der
  Agent schreibt dazu eine Logzeile `❌ [<Anfrage-ID>] <Status> <Code>: …`, wo
  vorhanden mit Upstream-Status und Schlüsselquelle. Die technische Ursache bleibt so im Log; der Browser
  bekommt eine lesbare Meldung und die ID. Eine ID, die ein Proxy davor schon
  vergeben hat, wird übernommen, wenn sie ein schlichtes Token ist.
- **Diagnose:**
  - `/health` meldet `b_api_auth_mode`.
  - `GET /debug/auth` zeigt maskiert (Vorhandensein, Schema, Länge), welcher
    Schlüssel ankommt und welchen die KI-Aufrufe nutzen würden.
  - `GET /debug/dependencies` prüft, ob B-API, Textextraktion und Repository
    erreichbar sind. Ob die B-API den Schlüssel annimmt, prüft es mit einer
    Abfrage der Modellliste, die keine Tokens verbraucht.
  - Beide Debug-Endpunkte gibt es nur mit `METADATA_AGENT_DEBUG_ENDPOINTS=true`,
    sonst antworten sie mit `404`.
  - Beim Start warnt der Agent bei unpassenden Kombinationen: `static` ohne
    Schlüssel; `forward` mit ungenutztem `B_API_KEY`; `auto` ohne Rückfall;
    Adressen, die teils auf Staging und teils auf Produktion zeigen.
- **B-API-Routing als zusätzlicher Provider `b-api-router`:** Die
  KI-Aufrufe gehen an `/api/v1/llm/router/chat/completions`, `model` trägt
  einen Routennamen. Der Default `openai/gpt-6-luna` funktioniert ohne Route
  (`METADATA_AGENT_B_API_ROUTER_MODEL`). `b-api-openai` bleibt Default; ohne
  B-API läuft der Agent weiter mit `openai`.
  - `METADATA_AGENT_LLM_REQUEST_STYLE` (`auto`/`reasoning`/`classic`) legt die
    Parameterfamilie fest, die ein Routenname nicht verrät. Er gilt nur für
    `b-api-router`. `auto` erkennt
    GPT-5/6 jetzt auch hinter `openai/`.
  - Router-Fehler werden zugeordnet. Dauerhafte werden nicht wiederholt:
    unbekannte Route → `LLM_ROUTE_NOT_FOUND`; Modell ohne Preis, ungeeignet oder
    falsche Parameter → `LLM_ROUTE_MISCONFIGURED`, auch wenn die B-API dafür
    `503` sendet. GWDG-Drosselung hinter dem Router endet nach den Versuchen als
    `LLM_RATE_LIMITED`, eine Route ohne freies Modell als `LLM_UNREACHABLE`.
  - Ein `400 Unsupported parameter` beendet die Erschließung jetzt bei allen
    Providern sofort, weil jeder weitere Aufruf denselben Body trägt.
  - `METADATA_AGENT_B_API_IGNORE_CACHE=true` umgeht den B-API-Cache.
  - `/debug/dependencies` meldet `model` und bei Routennamen `route_listed`.
- **Betrieb nach den Anforderungen von Team 6** (Übersicht in
  [UEBERGABE.md](UEBERGABE.md)). Alle Defaults verhalten sich wie bisher.
  - **Tracing:** `x-client-trace-id`, `x-request-id`, `x-b3-*` und `b3` gehen
    unverändert an B-API, Textextraktion und Repository, nie an Fremddienste.
    `x-client-trace-id` ist zugleich die Anfrage-ID.
  - **Logging:** Alle Meldungen laufen über `logging` nach stdout.
    `METADATA_AGENT_LOG_FORMAT=json` schreibt ein Objekt je Zeile mit
    `trace_id`, auch für die uvicorn-Logs. `METADATA_AGENT_LOG_LEVEL` steuert
    die Schwelle. `text` (Default) sieht aus wie bisher. Die Request-Logs von
    httpx bleiben aus.
  - **`/ready`:** prüft, ob die Konfiguration arbeiten kann (Schemata geladen,
    ein Schlüssel möglich), und antwortet sonst mit `503`. `/health` und
    `/ready` stehen nicht mehr in der OpenAPI-Spec, bleiben aber erreichbar.
  - **Interner Port:** `METADATA_AGENT_INTERNAL_PORT` startet einen zweiten
    Server für `/health`, `/ready` und `/metrics`, nur mit einem Worker je
    Container. Ist der Port belegt, nennt das Log den Grund, und die API läuft
    weiter.
  - **Metriken:** `/metrics` (Prometheus, neue Abhängigkeit
    `prometheus-client`) zählt HTTP-Anfragen nach Routen-Vorlage, KI-Aufrufe
    nach Ergebnis und Erschließungen nach Status. Es gibt `/metrics` nur auf
    dem internen Port.
  - **Spec:** Die OpenAPI-Spec trägt eine Beschreibung für Endnutzende
    (`info.summary`).
  - `pyproject.toml` steht auf 2.1.0, das uv-Image im Dockerfile ist auf
    0.7.13 festgelegt. Die k8s-Beispiele nutzen `/ready` und den internen Port.
- **Korrekturen aus dem Qwen-Kurztest (15.10.2026)**, wirksam für alle Modelle:
  - **Vokabular vollständig:** Der Extraktions-Prompt listete nur die ersten
    20 Begriffe eines Vokabulars. „Physik“ steht in der Fächerliste an
    Position 46, Modelle, die sich an die Liste halten, antworteten daher mit
    „Chemie“. Jetzt stehen bis zu 300 Begriffe im Prompt; nur die
    Berufslisten (894) bleiben gekürzt.
  - **Inhaltsart:** Die Erkennung nimmt außer `content_type` auch `value` an,
    den Schlüssel, den der gemeinsame System-Prompt verlangt.
  - **Leere Antworten sind Fehler:** Liefert ein Modell keinen Text
    (`content: null`, meist `finish_reason: length`), meldet der Agent
    `LLM_EMPTY_ANSWER`, statt das Feld als „kein Wert im Text“ zu zählen.
  - **`METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT`** (Router mit `classic`):
    Denkende Modelle wie `qwen3.6-35b-a3b` brauchen `none`. Default leer, es
    wird also nichts gesendet; ältere Modelle lehnen den Parameter ab.
- **Prüfanleitung:** `DEPLOYMENT.md` beschreibt unter „Nach dem Deployment
  prüfen“ zwei Konsolentests über den B-API-Proxy (ein Feld / vollständige
  Generierung) und wie die Ergebnisse zu lesen sind.
- **Korrekturen aus dem Code-Audit (15.10.2026)**, Bericht unter
  `docs/audits/2026-10-15-audit.md`:
  - **Schreibpfad:** Lehnt das Repository alle Metadaten ab, wird der neue
    Node verworfen, statt mit nur einem Titel in Sammlungen und in die
    Redaktionsqueue zu gehen. Ohne ladbares Schema wird nichts geschrieben. Der
    Workflow hält beim ersten fehlgeschlagenen Schritt an (kein
    `150_PUBLISH_IN_SEARCH` nach gescheiterter Freigabe). Teilfehler stehen in
    `error`. `/upload` mit `node_id` beachtet das Zwei-Stunden-Fenster
    (`403`/`404` wie `/nodes`). Die Dublettenprüfung nutzt auch `schema:url`
    und meldet, wenn sie nicht laufen konnte. `/node` ohne Zugangsdaten
    antwortet `503` statt `500`.
  - **Sicherheit:** `llm_provider` (Request und Einstellung) nur noch aus der
    bekannten Liste; `openai` per Request nur auf einem OpenAI-Deployment.
    Sammlungs-IDs müssen Node-UUIDs sein. Request-Bodies höchstens
    `METADATA_AGENT_MAX_REQUEST_BYTES` (20 MB), darüber `413`. Übersetzungen
    nur für `de` und `en`.
  - **Robustheit:** Modellantworten als bloße Liste oder Zeichenkette werden
    angenommen statt viermal wiederholt. Ein fehlender Ländercode verwirft
    nicht mehr das ganze Geocoding; die Photon-Ratenbegrenzung hält auch bei
    parallelen Anfragen. Bei `429` wartet der Agent so lange, wie der Dienst
    per `Retry-After` verlangt (höchstens 30 s je Versuch). Bodies, die kein
    JSON-Objekt sind, ergeben `400` statt `500`. Ein abgebrochenes `/generate`
    schließt seinen LLM-Client und zählt in den Metriken als `failed`.
  - **Werte:** `/validate` akzeptiert Vokabulare mit `value` statt `uri`
    (Sprachen, Lizenzen). CC0 bekommt Version 1.0 statt 4.0. „Mo-Fr“ wird zu
    fünf Wochentagen. `/upload/verify` vergleicht Listen, Autorennamen und
    Zeitstempel mit Zeitzone richtig.
  - **Timeout je KI-Aufruf 90 s** statt 60 s (Code) bzw. 300 s (Compose,
    Template): ein Puffer unter dem Ingress-Timeout von 120 s.
  - **Input-Caching für die Feld-Aufrufe:** Der Seitentext steht als eigene
    Nachricht vor jeder Feldanweisung, und ein kurzer Vorlauf-Aufruf füllt
    vor den Feld-Aufrufen den Prompt-Cache (nur `b-api-openai`/`openai`).
    Gemessen kommen danach 66–93 % der Eingabe-Tokens aus dem Cache; eine
    Erschließung mit `gpt-6-luna` kostet 59–86 % weniger (UEBERGABE §7).
    Vorher wurde der Text 50-mal in den Cache geschrieben und nie gelesen.
  - **API-Doku ohne Dritt-Server:** `/docs` liefert Swagger UI 5.33.1 selbst
    aus (`src/static/docs/`, Prüfsumme im README dort) statt von jsDelivr, mit
    abgeschaltetem Validator. `/redoc` entfällt, weil es zusätzlich Google
    Fonts lud.
  - **Aufgeräumt:** fünf Einstellungen ohne Wirkung entfernt (siehe
    DEPLOYMENT.md), ungenutzte Abhängigkeiten `openai`, `tenacity`,
    `aiofiles`, `python-multipart` entfernt, `requirements.txt` um
    `prometheus-client` ergänzt (und per Test mit `pyproject.toml` abgeglichen).
    `docker-compose.yml` führt jetzt auch Auth-Modus, Router- und
    Betriebseinstellungen; die Helm-Beispielwerte zeigen Ingress-Annotationen
    für Timeout (120 s) und Body-Größe (20 MB).
    Tests laufen unabhängig von Umgebung und `.env` des Entwicklers
    (`tests/conftest.py`). CI nutzt dieselben uv- und ruff-Versionen wie
    Dockerfile und Lock, installiert mit `--locked`; der GitHub-Workflow
    testet vor dem Bau und veröffentlicht nicht aus Pull-Requests.
- **Helm-Chart und Alarmregeln** unter `helm/metadata-agent/` (16.10.2026):
  Deployment mit Probes auf dem internen Port, Service, optionales Ingress mit
  Timeout- und Body-Annotationen, Secret (vorhanden oder aus `--set`),
  `ServiceMonitor` und `PrometheusRule` (Prometheus Operator). Acht Alarmregeln
  in `files/alerts.yaml`: Key abgelehnt, Route falsch, Drosselung, KI nicht
  erreichbar, Erschließungen scheitern, abhängiger Dienst antwortet nicht oder
  mit 5xx, `/generate` nahe am Ingress-Timeout. `promtool test rules` prüft
  jede Regel mit Testdaten; `tests/test_helm_chart.py` gleicht Metriken,
  Label-Werte und Env-Variablen mit dem Code ab. Neue CI-Jobs `helm lint` und
  `alert rules` (GitLab) bzw. `chart` (GitHub).
- **Neue Metriken:** `metadata_agent_upstream_requests_total`,
  `…_responses_total` (nach Status) und `…_response_duration_seconds` für jeden
  Aufruf an KI-Dienst, Textextraktion, Repository und Geocoder. Gesendet minus
  beantwortet sind die Aufrufe ohne Antwort. `metadata_agent_llm_tokens_total`
  zählt `prompt`-, `cached`- und `completion`-Tokens aus jeder KI-Antwort,
  auch die des Cache-Vorlaufs. Der Geocoder bekommt weiterhin keine
  Tracing-Header.
- **Architektur-Map** unter `docs/architektur/`: interaktiv als HTML, als Bild
  (PNG 3220×1276, SVG) für Dokumente; verlinkt aus README und `UEBERGABE.md`.
- **Webkomponente 2.1.0:** Details im CHANGELOG der Webkomponente. Das
  ausgelieferte Bundle unter `src/static/widget/dist/` entspricht diesem Stand.
  - Sie zeigt die Ursache eines Fehlschlags samt Anfrage-ID.
  - Teilergebnisse und geratene Inhaltsarten meldet sie als Warnung.
  - Eine 200-Antwort ohne Ergebnis übernimmt sie nicht.
  - Scheitert ein Inhaltsart-Wechsel, leert sie das Formular nicht mehr.

## 2.0.2 — 2026-10-07 — gezielte Korrekturen auf dem stabilen Stand

Ein breiterer Stabilisierungsversuch (Widget 2.0.1: Angular-21-Umbau zusammen
mit rund 7.000 weiteren geänderten Zeilen) erzeugte eingebettet in edu-sharing
neue Fehler (in Prod u.a. `Metadata input must be a JSON object`). Er wird durch
diese Version ersetzt und liegt unverändert im lokalen Branch
`backup/fix-attempt-2026-10-07`. Diese Version
setzt wieder auf dem letzten stabilen Commit `b483ff5` auf und ändert nur
diese Punkte:

- **Screenshots immer lokal** mit Playwright/Chromium im Container; Default
  `playwright` (ENV, `POST /screenshot`, `/upload`). `pageshot` bleibt als
  Legacy-Alias, nimmt aber ebenfalls lokal auf — keine URL geht mehr an einen
  externen Dienst, die Antwort meldet `playwright`.
- **Entfernte Einstellungen:** `METADATA_AGENT_PAGESHOT_API_URL` und
  `METADATA_AGENT_SCREENSHOT_BLOCK_ADS` werden nicht mehr gelesen.
- **Viewport 1920×1080** statt 800×500. Andere Formate als PNG/JPEG (z.B.
  `webp`) werden als PNG geliefert und als PNG ausgewiesen.
- **Nur öffentliche URLs:** `/screenshot` sowie `/generate`,
  `/detect-content-type` und `/extract-field` mit `input_source=url` lehnen
  interne Ziele mit `400` ab; Screenshots aus `/generate` und `/upload` entfallen
  dann. Im Screenshot-Browser werden direkte Anfragen an interne Hosts
  blockiert; führt eine Weiterleitung oder Navigation der Seite (auch in Frames
  oder für Bilder) zu einem internen Ziel, wird die Aufnahme verworfen — auch
  dann, wenn das interne Ziel gar nicht antwortet. Vorschaubild-URLs
  (`POST /nodes`) werden je Weiterleitung geprüft (höchstens 5, 10 MB beim
  Download, unkomprimiert angefordert; eine trotzdem komprimierte Antwort wird
  abgelehnt). Internationale Hostnamen werden in der Form geprüft, in der der
  Client sie auflöst (IDNA 2008, `ß` → `xn--zca`). DNS wird im Container
  aufgelöst — Split-Horizon-DNS im Cluster beachten.
- **Höchstens zwei Screenshots gleichzeitig** (je ein Chromium), weitere warten;
  eine Aufnahme darf samt DNS-Prüfung und Wartezeit höchstens 90 s dauern.
  Scheitert in `/generate` die Extraktion, wird die parallel laufende Aufnahme
  abgebrochen, statt einen der beiden Plätze weiter zu belegen.
- **Screenshot-Wartestrategie:** aufgenommen wird nach `domcontentloaded` und
  höchstens 10 s Warten auf Netzruhe (vorher hart `networkidle` — Seiten mit
  Analytics oder Long-Polling liefen dabei in den Timeout). Eine Seite, die nicht
  lädt oder während der Wartezeit auf Chromiums Fehlerseite landet, ergibt `502`
  statt eines Bildes der Fehlerseite; Browser-Fehlertexte stehen nur im Log.
- **Standard-LLM `b-api-openai` / `gpt-6-luna`** statt `b-api-academiccloud` /
  `deepseek-v4-flash`; AcademicCloud behält `deepseek-v4-flash`. `gpt-6-luna`
  stand am 2026-10-06 in `/api/v1/llm/openai/models`. GPT-6 bekommt denselben
  Request-Body wie GPT-5. ENV- und Request-Overrides haben weiter Vorrang.
- **Durchsatz je Provider** mit eigenem Budget: `b-api-openai` und `openai` 20
  gleichzeitig ohne Ratengrenze, AcademicCloud 2/2 (gemessen 2026-08-12). Ein
  `/generate`-Lauf mit 20 parallelen Requests lief am 2026-10-06 ohne `429`.
  `METADATA_AGENT_DEFAULT_MAX_WORKERS` ist 20 (vorher 10, in
  `docker-compose.yml` 2) und gilt für `/generate` ohne `max_workers`; ein Wert
  außerhalb von 1–20 wird beim Start auf diesen Bereich gesetzt, und das Startlog
  zeigt den wirksamen Wert. Die Swagger-Beispiele legen weder `max_workers` noch
  Provider und Modell fest, „Try it out“ nutzt also die Server-Defaults; ein
  eigenes Beispiel zeigt den Wechsel zu AcademicCloud.
- **Webkomponente 2.0.2:** Roboto und Material Icons selbst gehostet, unter
  eigenen Familiennamen (`/widget/dist/fonts/`, `@font-face` in `styles.css`;
  `deploy-widget.ps1` kopiert `fonts/` mit und bricht ohne sie ab, bevor das
  ausgelieferte Widget gelöscht wird), Google-Fonts-Links entfallen,
  `/widget/info` meldet `"fonts": []`. Ohne `api-url` und `window.__ENV.agentUrl` gilt die Herkunft von
  `main.js` inklusive Proxy-Präfix. Doppelt geladene Scripts werfen keinen
  `NotSupportedError` mehr; ein fremdes `<app-root>` schaltet nicht mehr in den
  Standalone-Modus.
- **Widget auf Angular 21** (wie die edu-sharing-Instanz), als eigener Schritt
  und nur mit den nötigen Migrationen; `npm audit` der Laufzeitpakete jetzt ohne
  Befund (vorher 9, davon 3 hoch). Nur noch ein Material-Theme — das zusätzliche
  M2-Theme verschob unter Material 21 den Vorschaubild-Schalter. Theme, Schriften
  und Icon-Klassen gelten nur noch im Widget und seinem Overlay-Container statt
  auf `html`: Mit derselben Material-Version wie edu-sharing hätten die globalen
  `--mat-*`-Tokens sonst dessen eigene Komponenten umgestylt. Der Zeiger des
  Zeitwählers ist wieder sichtbar. Details im Changelog der Webkomponente.
- **Schema-Dateien nur noch als Namen:** Version (`x.y.z` oder `latest`) und
  Dateiname (`name.json`) werden geprüft, bevor ein Pfad entsteht. Vorher ließ
  sich per `../` jede JSON-Datei lesen, auf die der Prozess Zugriff hat, und die
  Fehlermeldung zu einem fehlenden Schema verriet den absoluten Serverpfad.
- **`/screenshot` mit `node_id` nimmt nur noch einmal auf** und lädt genau
  dieses Bild als Vorschau hoch (vorher zwei Browser-Läufe pro Anfrage).
- **Sicherheitsupdates der Abhängigkeiten:** fastapi 0.142.4 (starlette 0.52.1 →
  1.7.0), python-multipart 0.0.32, pydantic-settings 2.15.0, anyio 4.14.2,
  idna 3.20. `pip-audit` meldete für die Laufzeitabhängigkeiten 14
  Schwachstellen in fünf Paketen, jetzt keine.
  starlette, anyio und idna kommen nur indirekt; ihre Untergrenzen
  (`starlette>=1.3.1`, `anyio>=4.14.2`, `idna>=3.15`) stehen deshalb als
  `[tool.uv] constraint-dependencies` in `pyproject.toml`, damit auch ein neu
  erzeugtes Lock keine verwundbare Version wählt; `python-dotenv` beginnt bei
  1.2.2. `requirements.txt` (nur für pip, Docker und CI nutzen `uv.lock`) trägt
  dieselben Untergrenzen — vorher standen dort noch fastapi 0.115 und
  python-multipart 0.0.18. An keiner der Untergrenzen meldet `pip-audit` etwas.
  Für den Starlette-TestClient ist `httpx2` als Dev-Abhängigkeit dabei (mit
  `httpx` allein warnt er).
- **CI installiert Chromium** im Test-Job, damit die Browser-Tests (ausgeliefertes
  Widget, Screenshot-URL-Regeln) dort laufen statt übersprungen zu werden; der
  Browser kommt nach dem ersten Lauf aus dem Cache, und `pytest -rs` listet jeden
  übersprungenen Test mit Grund im Job-Log.

---

## 2026-08-18 (ohne Versionsnummer) — Objektfelder: ein String in einem Objektfeld erreicht das Repository nicht mehr

Stand: 2026-08-18 · gegenüber Commit `9abc2db`. Damals im Arbeitsbaum, nicht
committet; die Testsuite umfasste **1177 Tests** (vorher 1176, davon 7 rot);
`ruff check` und `ruff format --diff` waren sauber.

Hintergrund, Regeln und Wartungshinweise stehen in **[OBJEKTFELDER.md](OBJEKTFELDER.md)**.
Hier nur, was sich für Aufrufer und Betrieb ändert.

**Der Vorfall in zwei Sätzen:** `ccm:oeh_extendedData` wird von Elasticsearch mit
dynamischem Mapping indiziert — der erste Datensatz, der einen Pfad füllt, legt
dessen Typ für den ganzen Index fest. Eine Handeingabe vom 17.03.2026 schrieb
`schema:contactPoint: ["Virtuelles Haus"]`, damit war der Pfad `text`, und der
schema-konforme Datensatz vom 07.08. wurde mit `mapper_parsing_exception`
abgewiesen — er kam nie in den Index, woraufhin der collectionSyncTracker mit
`document_missing_exception` stehenblieb und die Sammlungszuordnung ausfiel.

### 1. `/generate` liefert Objektfelder als Objekte — für alle 37, nicht für sechs

**Vorher:** `OutputNormalizer` hob Strings in Objekte, für eine fest verdrahtete
Liste von sechs Feldnamen — allesamt Personenrollen aus dem
Veranstaltungskontext.
**Jetzt:** `FieldNormalizer` leitet es aus dem Schema ab und deckt damit jedes
Objektfeld ab.

Die Lücke war die eigentliche Ursache:

| Schemadatei | Objektfelder | vorher abgedeckt |
|---|---:|---:|
| `person.json` | 14 | **0** |
| `organization.json` | 11 | **0** |
| `event.json` | 10 | 4 |
| `education_offer.json` | 5 | **0** |

Ein nackter String landet im Unterfeld `name`, wenn eine Variante eines
deklariert, sonst im ersten deklarierten Unterfeld — bei `ContactPoint` also
`contactType`. Für die sechs bisherigen Felder ist das Ergebnis unverändert;
`test_output_normalizer_list_is_redundant.py` hält die Äquivalenz fest.

> **Sichtbar für Aufrufer:** `/generate` gibt für Felder wie `schema:provider`
> jetzt `{"name": "…"}` zurück, wo vorher ein String stand.

### 2. `/validate` meldet einen Skalar im Objektfeld als Fehler (neu)

**Vorher:** `schema:provider: "Stuttgarter Musikschule"` galt als gültig — mit
dem Werkzeug, das für genau diese Prüfung gebaut ist, ließen sich die kaputten
Datensätze nicht finden.
**Jetzt:** `valid: false` mit einem Fehler, der das Feld nennt und die richtige
Form zeigt.

Damit lässt sich der Bestand prüfen. Als `error`, nicht als `warning`: der Wert
verhindert die Indizierung vollständig.

### 3. `/upload`, `/nodes` und `/save-node` lehnen ab, statt zu korrigieren

**Vorher:** Diese Wege nahmen Metadaten aus dem Widget entgegen und schrieben sie
ungeprüft durch. Beide Handeingaben, die Staging zerlegt haben, kamen so herein.
**Jetzt:** Der Schreibvorgang wird abgelehnt, das Feld benannt, und **nichts**
wird geschrieben.

Bewusst keine Korrektur an dieser Stelle: die Redaktion hat geprüft und
abgeschickt, was sie gesehen hat. Etwas anderes zu speichern wäre bei einem
Kurationswerkzeug schlimmer als ein fehlgeschlagener Schreibvorgang. Korrigiert
wird nur dort, wo das Ergebnis danach noch auf dem Bildschirm erscheint — in
`/generate` und in der Eingabemaske des Widgets.

Die Prüfung sitzt **vor** dem ersten Repository-Aufruf. Beide Abläufe legen den
Node zuerst an und befüllen ihn danach; eine späte Ablehnung hätte einen Node
zurückgelassen, der nur seinen Titel trägt.

> **Sichtbar für Aufrufer:** Antwort wie bei jedem anderen Upload-Fehler —
> `success: false` mit `error`, zusätzlich `violations` mit Feld, Wert und
> erwartetem Unterfeld. Das Widget zeigt die Meldung der Redaktion an.

**Für Massenimporte relevant:** ein Lauf, der eine falsche Form erzeugt,
scheitert jetzt, statt sich still zu heilen. Der aufrufende Code muss
`success: false` auswerten, sonst fallen betroffene Datensätze unbemerkt heraus.

### 4. Schemata: der Elementtyp jeder Liste ist ableitbar

84 Dateien in beiden Kontexten und allen drei Versionen:

| Änderung | Vorkommen |
|---|---:|
| `datatype: object` + `multiple: true` → `datatype: array` + `items.datatype: object`, Varianten nach `items` | 49 |
| fehlendes `items.datatype` ergänzt (435× `string`, 339× `uri`) | 774 |
| Objektliste vervollständigt (`schema:instructor`, `schema:isBasedOn`) | 15 |
| `schema:additionalName` → `string`, `schema:recordedIn` → `uri` | 3 |
| internes `version`-Feld an den Ordner angeglichen | 26 |

Die Elementtypen sind nicht geraten, sondern aus Vokabular-Deklaration und
beobachteten Bestandswerten abgeleitet. `src/schemata/default/v1.8.1 - Kopie/`
(14 Dateien, im Git versioniert, von jedem Glob mitgelesen) ist entfernt.

Der alte Stil `datatype: object` + `multiple: true` legte die Varianten ins
`system` statt in `items` — dort fanden weder
`llm_service._build_object_structure` noch der ShapeExpanderService des Widgets
sie. Personenfelder hatten deshalb im Editor **überhaupt kein Objektformular**.

> **Achtung bei `schema:instructor`:** jetzt als Objekt deklariert, wie
> `organizer` und `provider`. 15 Bestandsdatensätze tragen dort Strings und
> gehören in dieselbe Bereinigung wie die übrigen.

### 5. CI führt die Tests aus (neu)

**Vorher:**

```yaml
run tests:
  script:
    - echo "This is a reminder to implement tests with pytest!"
```

Der Job wurde grün, 1176 Tests liefen nirgends — auch der Schema-Selbsttest
nicht, der genau das verhindern soll.
**Jetzt:** `uv sync` + `uv run pytest -q`, mit Cache auf `uv.lock`.

Dafür mussten 7 Wächtertests repariert werden, deren Listen beim Hinzufügen von
`POST /nodes` (13.08.) nicht mitgezogen wurden: `SaveNodeRequest` fehlte in
`REQUEST_MODELS` und `CARRY_NODE_ID`, `/nodes` in `BODY_MODEL` und
`WRAPS_A_FLAT_BODY`, dazu drei Optionsnamen in `FLAT_BODY_OPTIONS`.

Dazu `pythonpath = ["."]` in `pyproject.toml`. Der erste CI-Lauf scheiterte mit
**31 Sammelfehlern** `ModuleNotFoundError: No module named 'src'`, obwohl lokal
alles grün war: `python -m pytest` legt das Arbeitsverzeichnis selbst auf den
`sys.path`, das Konsolenskript `pytest` — und damit `uv run pytest` — tut das
nicht, es fügt nur das Verzeichnis der Testdatei hinzu. Die Einstellung behebt
es für jede Aufrufart, auch für ein blankes `pytest`.

Der zweite CI-Lauf scheiterte an sechs Tests in `test_two_step_upload.py`, die
`get_repository_service()` brauchen. Die Funktion baut den Service nur, wenn
`WLO_GUEST_USERNAME` und `WLO_GUEST_PASSWORD` in der Umgebung stehen, sonst gibt
sie `None` zurück — der Endpunkt antwortet dann mit 503, und `POST /node` lief in
ein `AttributeError` auf `None`. Die Tests liefen also auf einer
Entwicklermaschine mit gesetzten Variablen und mussten in der CI scheitern. Die
`client`-Fixture stellt den Service jetzt selbst bereit, mit denselben
Platzhalter-Zugangsdaten, die die Service-Tests derselben Datei ohnehin benutzen.

> **Beim Prüfen daran denken.** Drei Unterschiede zwischen „läuft bei mir" und
> der CI haben je einen Fehlschlag gekostet:
>
> | | lokal typisch | CI |
> |---|---|---|
> | Aufruf | `python -m pytest` legt das Wurzelverzeichnis auf den `sys.path` | `uv run pytest` tut das nicht |
> | Umgebung | Zugangsdaten stehen in der Shell | leerer Container |
> | Ruff | irgendeine aktuelle Version | Image `ruff:0.9.5-alpine` |
>
> Der ehrliche Lauf vor dem Push:
>
> ```
> env -u B_API_KEY -u OPENAI_API_KEY -u WLO_GUEST_USERNAME -u WLO_GUEST_PASSWORD pytest -q
> ```
>
> So geprüft: 1177 Tests grün ohne jede Konfiguration, `ruff 0.9.5 check` und
> `format --diff` sauber.

### 6. Ausgelieferte Bundles werden nicht mehr von Git verbogen

`.gitattributes` mit `src/static/widget/dist/** -text` (neu).

`main.js` lag mit CRLF im Repository, sein `.gz` stammte aus der LF-Fassung —
1.452.484 gegen 1.452.517 Bytes, **nicht identisch**. FastAPI liefert je nach
Client mal das eine, mal das andere. Nach dem Neubau stimmen alle vier
Dateipaare byteweise überein.

---

### Intern (Objektfelder)

- **`FieldNormalizer`** hat `_is_object_field`, `_object_key`,
  `_normalize_object_field` und `_as_object`. Alle vier Schreibweisen der
  Objektdeklaration werden erkannt, auch die alte aus früheren Releases.
- **`find_object_shape_violations`** in `field_normalizer.py` meldet, ohne zu
  verändern. Es hieß bis zum 18.08. `enforce_object_shapes` und reparierte —
  siehe Punkt 3, warum das falsch war.
- **`OutputNormalizer`** behält nur noch die echten Sonderfälle
  (`_normalize_opening_hours`, `_normalize_event_schedule`, `_normalize_about`).
  `_normalize_person_array` bleibt als Definition der Objektschlüssel-Konvention,
  auf die sich beide Seiten einigen müssen.
- **Neue Tests:** `test_schema_declarations.py` (254),
  `test_object_fields_normalized.py` (51), `test_object_shape_is_enforced.py`
  (12), `test_output_normalizer_list_is_redundant.py` (5 plus 3 übersprungen —
  `schema:actor`, `schema:performer` und `schema:attendee` sind in
  `mds_oeh/v2.0.0` nicht deklariert).

---

## 2026-08-12 (ohne Versionsnummer) — Frühere Änderungen

Stand: 2026-08-12 · gegenüber Commit `52b008c` (CORS Fix) · **789 Tests**

Der folgende Abschnitt beschreibt den Stand von August 2026 vor der oben
beschriebenen Arbeit. Er ist inzwischen committet; die Testzahl und die
Referenz auf `52b008c` beziehen sich auf jenen Zeitpunkt.

---

### Was sich für Aufrufer ändert

#### 1. Lizenz: kein `COPYRIGHT_FREE` mehr, wenn keine erkannt wurde

**Vorher:** Fand die Extraktion keine Lizenz, schrieb der Upload
`ccm:commonlicense_key: ["COPYRIGHT_FREE"]`.
**Jetzt:** Das Feld bleibt leer.

`COPYRIGHT_FREE` heißt im WLO-Vokabular wörtlich *„Urheberrechtsfrei"*. Der
Extraktions-Prompt liefert bei fehlender CC-Lizenz `null` — daraus eine positive
Rechtsaussage zu machen, veröffentlichte über fremdes Material eine Behauptung,
die niemand geprüft hatte. Die Entscheidung trifft jetzt die Redaktion im
Prüf-Workflow, den der Upload ohnehin startet.

> **Sichtbar in der Redaktion:** Neu hochgeladene Inhalte ohne erkennbare Lizenz
> haben ein leeres Lizenzfeld statt „Urheberrechtsfrei".

#### 2. Lizenz: nicht zuordenbare Angaben gehen nicht mehr verloren

**Vorher:** Jeder `ccm:custom_license`-Wert mit einem `/` galt als
Vokabular-URI. Passte das letzte Pfadsegment auf keinen bekannten Schlüssel,
wurde der Wert **gelöscht** und anschließend durch `COPYRIGHT_FREE` ersetzt.

Das Schema definiert `ccm:custom_license` als Freitext und sein Prompt fordert
dort ausdrücklich URLs an — die eigene Extraktion erzeugte also genau die
Eingabe, die den Verlust auslöste:

| Eingabe | vorher | jetzt |
|---|---|---|
| `https://example.com/nutzungsbedingungen` | gelöscht → `COPYRIGHT_FREE` | `CUSTOM`, Text bleibt |
| `Nur für Bildungszwecke / nicht kommerziell` | gelöscht → `COPYRIGHT_FREE` | `CUSTOM`, Text bleibt |
| `© 2024 Uni München` | `CUSTOM`, Text bleibt | unverändert |

#### 3. Lizenz: `creativecommons.org`-Links werden abgebildet (neu)

```
https://creativecommons.org/licenses/by-sa/4.0/      → CC_BY_SA + 4.0
https://creativecommons.org/licenses/by-nc-sa/3.0/de/ → CC_BY_NC_SA + 3.0
https://creativecommons.org/publicdomain/zero/1.0/   → CC_0 + 1.0
https://creativecommons.org/publicdomain/mark/1.0/   → PDM (ohne Version)
```

Die Version kommt **aus der URL**, nicht aus einem Default; ein Länderkürzel
(`/3.0/de/`) wird nicht mitgelesen. Ein unbekannter Code (`by-xx`) wird nicht
geraten, sondern bleibt Text mit `CUSTOM`. Steht der Link **innerhalb** eines
Satzes, wird der Schlüssel gesetzt **und** der Text behalten — der Satz kann
mehr sagen als die Lizenz.

Die Schreibweise (`CC_BY_SA`, Unterstriche) ist gegen das Live-Repository
geprüft: WLO speichert genau diese Form — und nur sie löst auf, siehe Punkt 19.

#### 4. `collection_id` ist immer ein Array

```jsonc
{ "collection_id": ["3039bdb2-…"] }   // ✅
{ "collection_id": "3039bdb2-…" }     // ❌ 422
{ "collection_id": [] }               // ✅ wie weglassen
```

`workflow_steps` und `workflow_receiver` nehmen ebenfalls ausschließlich Listen.
Ein Feld, das beide Formen schluckt, machte das Anfrageformat davon abhängig,
welchen Parameter man gerade füllt. Als Einträge werden weiterhin IDs **und**
kopierte Sammlungs-URLs akzeptiert.

*Kein Bruch für bestehende Clients — der Parameter war nie ausgeliefert.*

#### 5. Body-Format: alle drei Formen sind jetzt gleichwertig

Betrifft **`/upload`, `/validate` und `/export/markdown`**.

`/generate` liefert die Schema-Marker (`contextName`, `schemaVersion`,
`metadataset`) *neben* `metadata`, alle Verbraucher suchten sie *darin*. Wer die
Antwort unverändert weiterreichte — der von der Doku empfohlene Weg — verlor
sie:

| Endpoint | vorher bei unveränderter `/generate`-Antwort |
|---|---|
| `/upload` | nur `core.json` → bei einer Veranstaltung fielen `ccm:oeh_event_begin`, `ccm:oeh_event_end`, `ccm:price`, `ccm:competence` weg |
| `/validate` | prüfte gegen `learning_material.json` statt `event.json` |
| `/export/markdown` | `schema_used: "auto"` — keine Erkennung |

Jetzt liefern alle drei Formen dasselbe Ergebnis. **Empfohlen ist die
`/generate`-Antwort unverändert** — sie kommt flach, Umschlag und Felder auf
einer Ebene:

```jsonc
// 1) Felder unter metadata                   2) EMPFOHLEN: flach (= /generate)
{ "contextName": "…", "metadataset": "…",    { "contextName": "…",
  "metadata": { "cclom:title": "…" } }         "metadataset": "…",
                                               "cclom:title": "…" }
// 3) alles unter metadata (Webkomponente)
{ "metadata": { "contextName": "…", "metadataset": "…",
                "metadata": { "cclom:title": "…" } } }
```

#### 6. Die Antwort sagt, gegen welches Schema geschrieben wurde (neu)

```jsonc
{
  "success": true,
  "schema_used": "event.json",     // null = nur core.json
  "repo_fields_available": 33,     // wie viele Felder überhaupt erlaubt waren
  "fields_written": 12
}
```

`metadataset` entscheidet, welches Typschema zusätzlich zu `core.json` gilt —
und damit, wie viele Felder geschrieben werden dürfen (core: 26,
+ `event.json`: 33, + `learning_material.json`: 40). Fehlt es, gibt es keinen
Fehler und keine Warnung. Diese beiden Felder machen den Unterschied sichtbar:

```
mit metadataset  : schema_used="event.json"  repo_fields_available=33
ohne metadataset : schema_used=null          repo_fields_available=26
```

Der Name `schema_used` ist derselbe wie bei `/validate` und `/export/markdown`.

#### 7. `workflow_steps`: leere Liste wird abgelehnt

`[]` liest sich wie „keine Schritte", hätte aber beim Weglassen auf den Standard
`200_tocheck` zurückgefallen — das Gegenteil. Jetzt `422`. Für einen Upload ohne
Workflow: `start_workflow: false`. Unbekannte Status geben ebenfalls `422`,
bevor etwas ins Repository geht.

#### 8. `discarded_node` in der Antwort (neu)

Bricht ein Upload ab, nachdem der Node schon angelegt war, wird er
zurückgenommen (Papierkorb, wiederherstellbar) und seine ID steht in
`discarded_node`. Scheitert auch die Rücknahme, bleibt das Feld gesetzt — dann
existiert der Node und muss von Hand weg.

#### 9. Autoren-VCARD folgt dem Repository-Format

```
vorher:  BEGIN:VCARD\nFN:Max Müller\nN:Müller;Max\nVERSION:3.0\nEND:VCARD
jetzt:   BEGIN:VCARD\nVERSION:3.0\nFN:Max Müller\nN:Müller;Max;;;\nEND:VCARD
```

Gegen einen echten WLO-Knoten abgeglichen: `VERSION` steht direkt nach `BEGIN`,
`N` hat fünf positionelle Komponenten. edu-sharing zerlegt `N` daraus nach
`ccm:lifecyclecontributer_authorVCARD_SURNAME` / `_GIVENNAME`.

Zusätzlich: Zeilenumbrüche im Namen werden zu Leerzeichen (eine präparierte
Quellseite konnte sonst eigene VCARD-Eigenschaften in die Node-Metadaten
schreiben), `;` und `,` werden in den `N`-Komponenten escaped. `FN` bleibt
bewusst unescaped — dort schützt der Escape nichts und würde als Backslash im
angezeigten Namen landen.

---

#### 10. Standard-LLM ist `gpt-5.6-luna`

Vorher `gpt-4.1-mini`. Das neue Modell ist ein Reasoning-Modell der Nano-Klasse
und nimmt einen anderen Request-Body — beides gegen die B-API gemessen:

| Parameter | gpt-5.6-luna |
|---|---|
| `max_tokens` | **400** — „use `max_completion_tokens` instead" |
| `temperature: 0.3` | **400** — nur der Default (1) ist erlaubt |
| `verbosity`, `reasoning_effort` | ✅ (bei `gpt-4.1-mini` beide **400**) |
| `response_format: json_object` | ✅ |

`llm_service` schaltet deshalb per Modell-Präfix (`gpt-5`, `o1`, `o3`, `o4`):
Reasoning-Modelle bekommen `max_completion_tokens` ohne `temperature`, alle
anderen behalten **unverändert** ihren bisherigen Body.

Neue Einstellungen `METADATA_AGENT_LLM_VERBOSITY` und
`METADATA_AGENT_LLM_REASONING_EFFORT`, beide `low`. Leer = Parameter nicht
senden. `reasoning_effort` erlaubt `none`/`low`/`medium`/`high`; `minimal` wird
abgelehnt.

**Warum `low` und nicht `none`:** `none` ist rund 40 % schneller (6,9–8,9 s statt
9,6–15,3 s) und braucht halb so viele Output-Token — verlor aber in 2 von 5
Läufen `ccm:oeh_event_begin` und destabilisierte einmal die Inhaltstyp-Erkennung.
`low` traf in sieben Läufen jedes Mal 8 von 9 Sollfeldern.

> Der AcademicCloud-Default `deepseek-r1` antwortete auf Staging mit
> `404 Model Not Found`. Behoben in Punkt 13.

#### 11. Sechs Felder erreichen das Repository, die vorher weggefiltert wurden

Ein redaktioneller Vergleichs-Upload von WLO-Seite schrieb Felder, die dieser
Agent nicht schrieb. Der Grund waren drei verschiedene Sperren, nicht eine:

| Feld | Sperre | jetzt |
|---|---|---|
| `oeh:new_lrt` | `repo_field: false` | `true` in `core.json` |
| `oeh:required_tools` | `repo_field: false` | `true` in `learning_material.json` |
| `schema:datePublished` | `repo_field: false` **und** Präfix-Filter | `true` + Filter präzisiert |
| `ccm:commonlicense_ai_allow_usage` | in keinem Schema definiert | neu in `core.json` |
| `ccm:commonlicense_ai_generated` | in keinem Schema definiert | neu in `core.json` |
| `ccm:commonlicense_ai_manually_modified` | in keinem Schema definiert | neu in `core.json` |

**Der Präfix-Filter.** `normalize_for_repo()` verwarf jeden Schlüssel mit dem
Präfix `virtual:` **oder** `schema:`, unabhängig vom Schema-Flag. Das war für
`schema:` zu grob: `schema:` ist ein regulärer Namensraum des Repositories, und
`schema:datePublished` wird von der Redaktion direkt geschrieben. Jetzt ist nur
noch `virtual:` unbedingt gesperrt — dahinter steht ein Wert, den edu-sharing
beim Lesen berechnet und nie speichert, für den es also keine sinnvolle
Schema-Einstellung gibt. Über alles andere entscheidet allein `repo_field`.

Das war gefahrlos, weil `repo_field` schon vorher die schärfere der beiden
Sperren war: kein einziges Feld mit `schema:`- oder `virtual:`-Präfix trug
`repo_field: true`, der Präfix-Filter war also faktisch wirkungslos. Die
Transformations-Eingaben `schema:location` und `schema:geo` stehen weiterhin auf
`repo_field: false` und gehen weiter über `cm:latitude`/`cm:longitude` ein — ein
Test wacht darüber, dass das so bleibt. Dieselbe Präzisierung in
`repository_diff.py`, damit `/upload/verify` `schema:datePublished` nicht
weiterhin als `not_written` meldet.

**Die drei KI-Felder** liegen in `core.json` und gelten damit für jeden
Inhaltstyp — eine Veranstaltung kann so gut KI-erzeugt sein wie ein
Arbeitsblatt. Sie sind `string` mit geschlossenem Vokabular `"true"`/`"false"`,
nicht `boolean`: das Repository bekommt `["false"]`, ein Python-`False` würde als
JSON-`false` serialisiert und wäre auf der Leitung ein anderer Wert. Im Canvas
sind sie sichtbar (`ask_user: true`), damit die Redaktion eine falsche
Einschätzung korrigieren kann. Die Prompts setzen einen Wert nur, wenn Text,
Impressum oder Lizenz es hergeben — aus Stil oder Qualität auf KI-Herkunft zu
schließen ist ausdrücklich untersagt.

Dabei fiel ein Fehler im Vokabular-Abgleich auf: `_validate_vocabulary()`
verglich den Wert **exakt** gegen die Konzept-Werte. Ein Modell, das nach
`'true'` oder `'false'` gefragt wird, antwortet aber etwa gleich oft mit einem
JSON-Boolean — und `str(True)` ist `'True'`, nicht `'true'`. Der Wert fiel
kommentarlos auf `None`. Der Abgleich läuft jetzt über die kleingeschriebene
String-Form, zurückgegeben wird weiterhin die Schreibweise aus dem Vokabular.
Kein Vokabular in den Schemata hat Werte, die sich nur in der Groß-/
Kleinschreibung unterscheiden — geprüft, bevor der Vergleich gelockert wurde.

> **Kosten:** drei zusätzliche Extraktions-Calls pro Upload, für jeden
> Inhaltstyp — ein Lernmaterial kommt damit auf 50 statt 47. Sie laufen im
> bestehenden Parallel-Fan-out mit.

`oeh:new_lrt` und `ccm:oeh_lrt` bleiben getrennt: `ccm:oeh_lrt` wird weiterhin
aus dem erkannten Inhaltstyp abgeleitet und mit den Extended Data geschrieben,
`oeh:new_lrt` trägt jetzt zusätzlich, was die Extraktion gefunden hat.

Damit stehen **45 eindeutige Feld-IDs** auf `repo_field: true`, 26 davon in
`core.json` (vorher 39 / 22). `repo_fields_available` in der Upload-Antwort
steigt entsprechend von 34 auf 40 für ein Lernmaterial.

> **Sammlungen** waren nie ein Filter-Problem: der ausgerollte Stand kennt den
> Parameter `collection_id` schlicht nicht (siehe Punkt 4). Ein Deploy behebt das.

**Am lebenden Repository nachgemessen** (2026-08-12, Staging, Knoten
`5443240c-43a2-4961-8324-0c43a22961dd`) — und das Ergebnis ist zur Hälfte
ernüchternd:

| Feld | geschrieben? |
|---|---|
| `ccm:commonlicense_ai_allow_usage` | ✅ |
| `ccm:commonlicense_ai_generated` | ✅ |
| `ccm:commonlicense_ai_manually_modified` | ✅ |
| `oeh:new_lrt` | ❌ verworfen |
| `oeh:required_tools` | ❌ verworfen |
| `schema:datePublished` | ❌ verworfen |

Alfresco quittiert den POST mit `200` und **verwirft Properties, die nicht im
Content-Modell stehen, ohne Rückmeldung**. Weder Statuscode noch
`fields_written` zeigen das an — die einzige Möglichkeit, es zu bemerken, ist
Zurücklesen. Von 100 gelesenen Bestandsknoten trägt kein einziger eines der drei
Felder; `ccm:oeh_lrt` dagegen 91.

Der Lernressourcentyp geht dadurch **nicht** verloren: `ccm:oeh_lrt` wird
weiterhin aus dem erkannten Inhaltstyp abgeleitet und über den
Extended-Data-Pfad geschrieben. Verloren geht der von der KI aus dem Vokabular
extrahierte Wert.

Die drei Flags bleiben auf `true` — das Senden kostet nichts und wirkt ohne
Codeänderung, sobald WLO die Properties anlegt. `WLO-REPO-FELDER.md` führt sie
mit ⚠️ und ist damit zugleich die Liste, die es dafür braucht.

#### 12. Durchsatzgrenzen des LLM-Gateways sind einstellbar (neu)

Zwei neue Einstellungen, beide prozessweit wirksam:

```env
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=   # leer = gemessener Default
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=   # leer = gemessener Default, 0 = aus
```

| Provider-Gruppe | gleichzeitig | pro Sekunde |
|---|---:|---:|
| `b-api` (beide B-API-Provider) | **2** | **2** |
| `openai` (nativ) | 10 | keine Grenze |

Die Rate ist **pro Sekunde**, nicht pro Minute: `2` sind 120 Aufrufe/Minute.

Gegen das damalige gehostete Deployment gemessen — Erschließung mit 50 Feldern:

| Provider / Modell | Dauer |
|---|---|
| `b-api-openai` / `gpt-5.6-luna` | 25,2 s |
| `b-api-academiccloud` / `deepseek-v4-flash` | 32,4 s · 89,6 s |
| `b-api-academiccloud` / `openai-gpt-oss-120b` | 53,1 s |

Die Streuung kommt von der Warteschlange am Gateway, nicht vom Modell. Das
Hosting muss daher Requests von mindestens 90 Sekunden erlauben.

**Warum das nötig war.** Die B-API erlaubt exakt zwei Requests gleichzeitig und
etwa zwei pro Sekunde. Das Budget hängt am **Key am Gateway**, nicht am Modell —
`b-api-openai` und `b-api-academiccloud` teilen es sich deshalb. Ein dritter
paralleler Request wird sofort mit `429` abgewiesen, **ohne `retry-after`**: ein
Client kann die Wartezeit nicht ablesen, er muss unter der Grenze bleiben. Mehr
Last hilft nicht, sie schadet — bei 3 req/s fällt der effektive Durchsatz unter
den Wert bei 2 req/s. Der Agent lief mit `DEFAULT_MAX_WORKERS=10` um Faktor 5
darüber, die meisten Feld-Extraktionen landeten im Retry.

**Wo die Grenze sitzt.** In `_call_llm` — dem einzigen Punkt, durch den *jeder*
LLM-Request läuft, auch die Inhaltstyp-Erkennung und die Normalisierung, die
nicht über den parallelen Feld-Fan-out gehen. Ein Retry ist ein eigener Request
und nimmt einen Platz.

Das Semaphore ist **prozessweit**, nicht pro Service-Objekt: `get_llm_service()`
erzeugt bei Provider- oder Modell-Override eine neue `LLMService`-Instanz, ein
instanzgebundenes Limit hätte bei zwei gleichzeitigen API-Requests also die
doppelte Zahl durchgelassen. Ein Test hält das fest.

`DEFAULT_MAX_WORKERS` wird auf die Grenze gedeckelt und das beim Start
ausgewiesen, statt eine Parallelität zu versprechen, die nicht stattfindet:

```
Default Workers: 10 → 2 (limit of b-api)
LLM Throughput: max 2 in flight, max 2 req/s
```

#### 13. AcademicCloud-Default ist ein Modell, das es gibt

`deepseek-r1` → **`openai-gpt-oss-120b`**. Der alte Default antwortete mit
`404 Model Not Found`; ein Default, der nicht funktionieren kann, ist schlechter
als keiner, weil nichts am Fehler auf die Einstellung zeigt. Das neue Modell
lieferte in der Messung vom 12.08.2026 die gleichmäßigste Latenz der
AcademicCloud-Modelle (4,99 s ± 2,31 s bei der Extraktion, p95 6,59 s im Chat).

#### 14. Nativ OpenAI zieht mit der B-API-Implementierung gleich

- **`METADATA_AGENT_OPENAI_API_BASE`** muss nicht auf OpenAI zeigen. Jeder
  Endpunkt, der `/chat/completions` bedient und einen Bearer-Token annimmt,
  funktioniert — Azure, selbst gehostetes vLLM, ein Gateway davor. Die
  Einstellung gab es schon, dokumentiert war sie nicht.
- **`METADATA_AGENT_OPENAI_TEMPERATURE`** folgt jetzt standardmäßig
  `METADATA_AGENT_LLM_TEMPERATURE`. Vorher ignorierte genau einer von drei
  Providern die gemeinsame Einstellung — eine Falle, kein Feature. Wer den
  eigenen Wert schon setzt, behält ihn.
- Die Durchsatzgrenzen gelten für alle drei Provider, nur mit anderen Defaults.

Was schon vorher providerunabhängig war und es bleibt: die Erkennung der
Reasoning-Modelle (`gpt-5`, `o1`, `o3`, `o4` → `max_completion_tokens`, kein
`temperature`, dafür `verbosity` und `reasoning_effort`) greift bei nativem
OpenAI genauso wie über die B-API.

#### 15. `ENV-PARAMETER.md` — vollständige Referenz (neu)

Alle 43 Umgebungsvariablen an einer Stelle, mit Defaults und Betriebsbeispielen.
`README.md`, `INSTALL.md` und `DEPLOYMENT.md` verweisen darauf, statt jeweils
eine eigene, unvollständige Teilliste zu führen.

Zwei Tests halten das Dokument ehrlich: eine Einstellung im Code ohne Eintrag
lässt den Test fehlschlagen, und ein dokumentierter Name, den `Settings` nicht
mehr liest, ebenfalls — letzteres ist der unangenehmere Fall, weil das Setzen
dann so aussieht, als wirke es.

#### 16. Der Lernressourcentyp kommt an — im richtigen Feld, aus dem vollen Vokabular

Zwei Fehler, die sich gegenseitig verdeckten.

**Falsches Zielfeld.** Das Schema nennt das Feld `oeh:new_lrt`, und genau so
wurde es geschrieben. Diese Property gibt es im Content-Modell aber nicht — der
POST kam mit `200` zurück, der Wert war weg. Was das Repository führt, ist
**`ccm:oeh_lrt`** (91 von 100 gelesenen Knoten, 124 Werte, alle aus
`…/vocabs/new_lrt/`). Der Upload benennt das Feld beim Schreiben jetzt um, wie
er es bei `cm:author` → `ccm:lifecyclecontributer_author` schon tut. **Die
`/generate`-Antwort bleibt unverändert** — dort heißt das Feld weiter
`oeh:new_lrt`, am Vertrag ändert sich nichts.

**Halbes Vokabular.** Im Schema standen **87** Konzepte, veröffentlicht sind
**220**. Die 87 waren eine saubere Teilmenge — keine veralteten Einträge, keine
abweichenden Labels — es fehlten schlicht 133, darunter „Quelle", „Portal",
„Datenbank" und „Lexikon oder Enzyklopädie". Das Vokabular ist geschlossen, ein
fehlendes Konzept ist also ein Wert, den die Extraktion nie liefern kann. Jetzt
vollständig, mit Snapshot unter `src/schemata/vocabs/new_lrt.json`.

> `new_lrt_aggregated` (48 Konzepte) ist **nicht** das richtige Vokabular:
> kein einziger der 124 gelesenen Werte stammt daraus.

**Die Kollision, die dabei auffiel.** Der Upload schreibt `ccm:oeh_lrt` ein
zweites Mal — aus dem erkannten Inhaltstyp abgeleitet, sechs grobe Typen
(`learning_material` → „Material"). Dieser Schritt läuft **nach** dem
Metadaten-Schreiben und hätte den genaueren extrahierten Wert ersetzt. Er greift
jetzt nur noch, wenn die Extraktion nichts gefunden hat.

**Am lebenden Repository nachgewiesen.** Dieselbe Seite, zweimal hochgeladen:

| | `ccm:oeh_lrt` |
|---|---|
| vorher (`5443240c-…`) | `…/1846d876-…` → **„Material"** (abgeleitet) |
| jetzt (`5d648bba-…`) | `…/9f40cd56-…` → **„Lexikon oder Enzyklopädie"** (extrahiert) |

Das Konzept „Lexikon oder Enzyklopädie" war eines der 133 fehlenden — ohne beide
Korrekturen zusammen wäre es nicht möglich gewesen.

#### 17. Zweistufiger Upload: `POST /node` und `node_id` am `/upload` (neu)

Wer eine Node-ID braucht, bevor die Metadaten existieren — um sie anzuzeigen, zu
verlinken oder einer Redaktion zu übergeben — bekommt sie jetzt einzeln:

```jsonc
POST /node   { "metadata": { "cclom:title": "Bruchrechnung Klasse 6" } }
// → { "success": true, "node": { "nodeId": "5443240c-…" } }

POST /upload { "metadata": { …aus /generate… }, "node_id": "5443240c-…" }
// → { "success": true, "node_created": false, "fields_written": 25 }
```

Beide Hälften steckten schon in `upload_metadata`; neu ist, dass die Naht von
außen erreichbar ist. `POST /node` schreibt genau die fünf Felder, die der Upload
beim Anlegen ohnehin setzt (`cclom:title` — Pflicht —,
`cclom:general_description`, `cclom:general_keyword`, `ccm:wwwurl`,
`cclom:general_language`) plus `ccm:linktype`. Alles Weitere braucht den
Schemafilter und gehört in den zweiten Schritt.

**`node_id` ist rein optional** — ohne den Parameter ändert sich am `/upload`
nichts. Mit ihm gelten zwei Abweichungen, und beide sind der eigentliche Grund,
warum das kein Dreizeiler war:

| | einstufig | mit `node_id` |
|---|---|---|
| Dublettenprüfung | läuft | **entfällt** |
| Node bei Fehler | wird verworfen | **bleibt** |

**Die Rücknahme darf nicht zuschlagen.** `_failed()` löscht den Node, wenn nach
dem Anlegen etwas schiefgeht — richtig für einen Node, den dieser Dienst erzeugt
hat, und falsch für einen, den er bekommen hat: der Aufrufer kann dort längst
Inhalt abgelegt haben. `created_node_id` wird deshalb nur noch gesetzt, wenn der
Aufruf den Node selbst angelegt hat. Ein Test hält das fest; mit
zurückgedrehtem Schutz meldet er `🧹 Discarded incomplete node …`.

**Die Dublettenprüfung fände sich selbst.** Sie sucht nach `ccm:wwwurl` — und der
Node, der befüllt werden soll, trägt diese URL bereits aus Schritt eins. Ohne
Ausnahme bräche der Upload als Dublette seiner selbst ab.

Neu in der Antwort: **`node_created`**. Beide Fälle liefern `200` und dieselbe
`nodeId`, sonst unterscheidet sie nichts.

**Gegen Staging durchgespielt** (2026-08-12): `POST /node` mit nur einem Titel →
Knoten `24f82a2a-…` mit `cclom:title` und `ccm:linktype`; anschließend `/upload`
mit dieser ID in der flachen Form → `node_created: false`, 9 Felder auf
demselben Knoten, und im Repository existiert genau **ein** Knoten mit diesem
Titel.

#### 18. `cm:name` wird gesetzt, wenn das Repository keinen ableiten kann

Der Live-Durchlauf legte einen Fehler offen, den kein aufgezeichneter Test finden
konnte: `POST /node` mit nur einem Titel scheiterte an

```
500 java.lang.Exception: missing name
```

`cm:name` ist beim Anlegen Pflicht, und edu-sharing leitet es aus **`ccm:wwwurl`**
ab — aus `cclom:title` nie. Gemessen:

| Gesendet | Ergebnis |
|---|---|
| nur `cclom:title` | **500 `missing name`** |
| `cclom:title` + `cm:name` | 200, Name wie angegeben |
| `cclom:title` + `ccm:wwwurl` | 200, Name aus der URL (`example.org_probe-c`) |

Das traf **nicht nur den neuen Endpunkt**: jeder `/upload` ohne `ccm:wwwurl` lief
in dasselbe 500 — bei Textquellen ohne URL und bei Inhaltstypen, die keine
führen. Der Fix sitzt deshalb in `_create_node` und gilt für beide Wege.

`cm:name` wird nur in dieser Lücke gesetzt, aus dem Titel, bereinigt um die von
Alfresco verbotenen Zeichen (`* " \ > < ? / : |`), auf 200 Zeichen gekürzt.
**Liegt eine URL vor, bleibt es beim abgeleiteten Namen** — jeder bisher
hochgeladene Knoten trägt einen solchen (`Echte Hasen  Wikipedia - 2`), und ihn
zu überschreiben würde sie alle umbenennen, in einem Feld, das die Redaktion
sieht.

> **Nebenbefund aus demselben Lauf:** die LRT-URI in meinen eigenen Tests war
> erfunden. Am Knoten fiel es als leeres `ccm:oeh_lrt_DISPLAYNAME` auf — die
> Tests waren grün, weil die Transformation weiterreicht, was sie bekommt, und
> nichts davon auflöst. Korrigiert auf die echte „Arbeitsblatt"-URI, plus ein
> Test, der jede in Tests verwendete URI gegen den Vokabular-Snapshot prüft.

#### 19. Lizenzschlüssel: die Schreibweise entschied, ob die Lizenz existiert

Das Vokabular von `ccm:commonlicense_key` bot `CC BY-SA` an — mit Leerzeichen und
Bindestrich. Das Repository speichert den Wert, **erkennt ihn aber nicht als
Lizenz**. Am lebenden Knoten geprüft, jede der sieben Schreibweisen einzeln
geschrieben und zurückgelesen:

```
geschrieben 'CC BY-SA' → gespeichert ["CC BY-SA"]  virtual:licenseurl = null
geschrieben 'CC_BY_SA' → gespeichert ["CC_BY_SA"]  virtual:licenseurl =
                         https://creativecommons.org/licenses/by-sa/4.0/deed.de
```

Alle sieben mit Leerzeichen scheitern, alle sieben mit Unterstrich lösen auf —
auch `CC0` → `CC_0`. Und `ccm:commonlicense_key_DISPLAYNAME` ist in beiden Fällen
`null`, am Knoten selbst ist der Fehler also nicht zu sehen. `virtual:licenseurl`
ist die einzige Stelle, an der er auffällt.

**Wirkung:** jede Lizenz, die die Extraktion direkt in `ccm:commonlicense_key`
schrieb, kam als Text an und wurde von nichts aufgelöst. Nur der Weg über einen
Deed-Link in `ccm:custom_license` war korrekt — der ging schon immer durch
`map_creative_commons_url` und lieferte `CC_BY_SA`.

Die Vokabular-Werte stehen jetzt in beiden Kontexten und allen vier Typschemata
auf der Unterstrich-Form, die Prompts nennen sie.

**Die Labels bleiben lesbar** (`CC BY-SA`), und das ist kein Versehen: der
Vokabular-Abgleich fällt auf die Labels zurück und gibt den Wert des Konzepts
heraus. Ein Modell darf also weiter die vertraute Schreibweise liefern —
gespeichert wird die, die auflöst:

```
'CC BY-SA' → CC_BY_SA      'cc by-sa' → CC_BY_SA      'Namensnennung' → null
```

#### 20. Der Lernressourcentyp: das Modell sah die falschen 20

Der Extraktions-Prompt listet höchstens 20 Konzepte und hängt „… und N weitere"
an. In der Reihenfolge des veröffentlichten Vokabulars sind die ersten 20
**ausnahmslos Quellen- und Angebotstypen**: Quelle, Kollektion, Wörterbuch,
Portal, Datenbank, Studiengang, Ausbildungsberuf … und kein einziger
Unterrichtsmaterialtyp.

Ein Arbeitsblatt bekam also eine Auswahl vorgelegt, in der „Arbeitsblatt" nicht
vorkam. Der Wikipedia-Artikel über Hasen kam als „Lexikon oder Enzyklopädie"
zurück — ein Wert aus genau diesem Fenster.

Zehn Typen stehen jetzt vorn, gewählt aus dem, was das Repository tatsächlich
führt (100 Knoten: Material 79×, Wiki 14×, Webseite 8×, Arbeitsblatt 6×,
Unterrichtsbaustein 6×, Video 4×) plus den Alltagstypen, die in dieser Stichprobe
fehlten:

`Material` · `Arbeitsblatt` · `Video (Material)` · `Audio` · `Bild (Material)` ·
`Präsentation` · `Quiz` · `Dokumente und textbasierte Inhalte` · `Webseite` ·
`Unterrichtsbaustein`

Es ist eine Umsortierung, keine Auswahl — alle 220 Konzepte bleiben, jedes genau
einmal; ein Test prüft das. Derselbe Arbeitsblatt-Text liefert danach
`36e68792-…` = **Arbeitsblatt**.

> Beides ist dieselbe Fehlerklasse und beides fiel erst am lebenden Repository
> auf: ein Wert, der geschrieben wird, `200` bekommt und trotzdem nirgends
> ankommt. Weder Statuscode noch `fields_written` sagen etwas dazu.

#### 21. `/upload/verify` prüft jetzt, ob ein Wert überhaupt ein Wert ist

Drei Fehler dieser Sitzung hatten dieselbe Bauart: etwas wurde gesendet, das
Repository antwortete `200`, und der Wert kam nie als Wert an.

| | |
|---|---|
| `oeh:new_lrt` | nicht im Content-Modell, stillschweigend verworfen |
| `CC BY-SA` | gespeichert, aber nicht als Lizenz gelesen |
| eine erfundene `new_lrt`-UUID | gespeichert, löst auf nichts auf |

**Keiner davon war im Statuscode zu sehen, nicht in `fields_written` und nicht im
SOLL/IST-Vergleich** — das Feld war da, und sein Wert war gleich dem gesendeten.
Was sie verrät, ist die Frage, ob das Repository aus dem Wert ein Label machen
kann.

An einem lebenden Knoten gemessen: von 19 gesetzten Vokabularfeldern tragen 18
ein `<feld>_DISPLAYNAME`, und genau eines — `ccm:commonlicense_key` — nie; dessen
Signal ist `virtual:licenseurl`. Daraus die Regel:

```
Vokabularfeld       → <feld>_DISPLAYNAME muss gefüllt sein
commonlicense_key   → virtual:licenseurl muss da sein
alles andere        → nichts aufzulösen, kein Urteil
```

**Zwei Ergänzungen an der Antwort**, beide additiv:

```jsonc
{
  "unresolved": [
    { "field_id": "ccm:oeh_lrt", "value": ["…/new_lrt/8e83b3f9-…"],
      "known": false,
      "reason": "leeres _DISPLAYNAME — der Wert steht in keinem Vokabular" }
  ],
  "diff": [
    { "field_id": "oeh:new_lrt", "status": "match", "resolution": "unresolved" }
  ],
  "summary": { "match": 12, "unresolved": 1, … }
}
```

`resolution` steht **neben** `status`, nicht darin: ein Feld kann exakt den
gesendeten Wert tragen (`match`) und trotzdem tot sein. Beides zu vermischen
hätte verborgen, dass der Wert korrekt angekommen ist.

**`unresolved` braucht keine `expected_metadata`.** „Welche Werte dieses Knotens
sind tot?" ist eine Frage, die man auch über einen Knoten stellt, den man nicht
selbst hochgeladen hat. Die Node-ID kommt wie bisher aus dem Pfad
(`POST /upload/verify/{node_id}`).

**Bekannte Fälle sind markiert.** `ccm:oeh_quality_correctness` und die drei
`ccm:commonlicense_ai_*` haben im MDS keinen Wertebereich — sie sind korrekt
gespeichert, es gibt nur kein Label. Sie tauchen auf **jedem** Knoten auf; ohne
`known: true` und Sortierung nach echten Befunden zuerst würde der Bericht
antrainieren, ihn zu überblättern.

An zwei echten Knoten geprüft: der Testknoten meldet **einen** echten Befund
(die erfundene LRT-URI) und zwei bekannte, der saubere Knoten **null** echte.

> **Nebenbefund:** der Vergleich fand `oeh:new_lrt` bisher gar nicht wieder — der
> Upload benennt es in `ccm:oeh_lrt` um, und der Diff suchte nur unter dem
> Schema-Namen. Er meldete das Feld als `missing_in_repo` **und** denselben Wert
> noch einmal als `extra_in_repo`. Beides behoben; die Umbenennungstabelle liegt
> jetzt an einer Stelle.

#### 22. `/upload` zeigt seine Optionen — und ein leeres Feld heißt jetzt überall dasselbe

Zwei Befunde aus derselben Ecke.

**Das Schema unter `/docs` war leer.** `/upload` nimmt einen rohen `Request`,
weil es zwei Body-Formen akzeptiert — flach oder unter `metadata` —, und
deklarierte seinen Body deshalb von Hand als `{"type": "object"}`: ein Objekt
ohne eine einzige Property. Der Schema-Reiter listete nichts. Alle 15 Optionen
existierten am Modell und waren unsichtbar, sofern sie nicht zufällig in einem
Beispiel vorkamen — `collection_id` in einem von zweien, `node_id` in keinem.

Das Schema wird jetzt aus `UploadRequest` abgeleitet, mit zwei Lockerungen für
die flache Form: nichts ist `required` (die Metadaten liegen dort auf oberster
Ebene), und zusätzliche Properties sind eben jene Metadatenfelder. Abgeleitet
statt abgeschrieben, damit die nächste Option nicht vergessen wird. Dazu ein
drittes Beispiel für den zweistufigen Upload.

**Ein leeres Feld wurde stellenweise als Wert gelesen.** Die Beispiele sind wie
ein Formular gebaut: jede Option aufgeführt, die ungenutzten leer. Beide
dokumentierten Beispiele trugen `"workflow_comment": ""` bzw. `"comment": ""` —
und die Workflow-Historie liest

```python
comment if comment is not None else "Upload via Metadata Agent API"
```

Ein Leerstring ist nicht `None`. **Wer das dokumentierte Beispiel unverändert
abschickte, schrieb einen leeren Kommentar in die Workflow-Historie** statt des
zugesagten Standards — mit `200` und ohne Hinweis.

Umgekehrt geprüft, damit die Reichweite stimmt: Knoten `5443240c-…` auf Staging
trägt `'Upload via Metadata Agent API'`. Uploads, die den Parameter schlicht
weggelassen haben, waren also nie betroffen — nur die, die ihn leer mitschickten,
wie es die Beispiele vormachten.

Behoben an der Grenze: ein leer gelassenes Textfeld wird zu `None`, in allen
Request-Modellen, sodass `is not None` weiter drinnen seine Bedeutung behält.

Die übrigen leeren Felder wurden einzeln nachgesehen und waren in Ordnung —
`source`, `preview_url`, `extended_text`, `text`, `source_url` prüfen alle auf
Truthiness, `llm_provider`/`llm_model` fallen über `or` zurück (kosteten
allerdings pro Aufruf eine überflüssige Service-Instanz; jetzt nicht mehr).

Für Listen gilt **keine** einheitliche Regel, und das ist Absicht:

| Feld | leere Liste |
|------|-------------|
| `collection_id` | = nicht angegeben |
| `regenerate_fields` | = nicht angegeben (bereits an der Konsumstelle) |
| `workflow_receiver` / `receiver` | **niemanden benachrichtigen** — eine echte Wahl, die sonst verloren ginge |
| `workflow_steps` / `steps` | `422` mit Verweis auf `start_workflow: false` |

### Konfiguration

#### `WLO_REPOSITORY_BASE_URL` entfernt

Die Variable war in `DEPLOYMENT.md` und `INSTALL.md` als Repository-Override
dokumentiert und wurde **von niemandem gelesen** — auch vor dem Refactor nicht.
Wer sie in Produktion setzte, bekam keinen Fehler und schrieb weiter nach
Staging.

Das Ziel-Repository wird ausschließlich über
**`METADATA_AGENT_REPOSITORY_URL`** gesetzt. Bestehende `.env`-Dateien mit der
alten Variable brechen nicht (`extra: "ignore"`).

#### UTF-8-Ausgabe unter Windows

Die Fortschrittsausgabe enthält Emoji. Auf einer cp1252-Konsole brach ein Upload
damit mitten im Schreiben mit `UnicodeEncodeError` ab — ausgelöst schon von einem
Autorennamen wie „Philipp Lang". `src/__init__.py` stellt `stdout`/`stderr` beim
Import auf UTF-8. Unter Linux und Docker ändert das nichts.

---

### Intern

- **Neue Module** aus `repository_service.py` herausgelöst — reine Umzüge,
  Verhalten unverändert: `repository_values.py` (Feld-Filter, Flattening,
  Autoren, Koordinaten), `repository_licenses.py` (Lizenz-Erkennung),
  `repository_diff.py` (SOLL/IST-Vergleich), `repository_curation.py`
  (Sammlungen, Workflow).
- **`carry_schema_markers()`** in `main.py` — ein Helfer für die drei Endpoints,
  die den Body auf dieselbe Weise entgegennehmen.
- **Diff-Bericht:** Kann kein Schema geladen werden, meldet `/upload/verify` die
  Felder als `not_written` statt `missing_in_repo`. Der Schreibpfad verweigert
  in diesem Fall jeden Schreibvorgang — die Felder fehlen also *durch
  Entscheidung*, nicht weil das Repository sie verloren hätte.
- **Rücknahme-Timeout** auf 5 s begrenzt: Die Rücknahme läuft, nachdem der
  Upload sein Budget schon verbraucht hat, innerhalb einer auf 60 s gedeckelten
  Serverless-Invocation.

#### Tests und Abdeckung

789 Tests (vorher keine). Abdeckung der Service-Schicht:

| Modul | Abdeckung |
|---|---|
| `repository_values.py` | 95 % |
| `repository_licenses.py` | 95 % |
| `repository_diff.py` | 95 % |
| `repository_curation.py` | 81 % |
| `repository_service.py` | 77 % |

---

### Was noch offen ist

- **Nichts davon lief gegen ein echtes Repository.** Alle Tests arbeiten gegen
  einen aufgezeichneten HTTP-Client. Gegen die Live-Instanz wurde nur *gelesen*
  (Lizenzvokabular, gespeicherte Knoten, MDS). Ein Test-Upload nach Staging vor
  dem Deploy klärt in einem Durchgang: leeres Lizenzfeld, VCARD-Zerlegung,
  Sammlungen, Workflow-Schritte.
- **Ob edu-sharing RFC-6350-Escapes auflöst,** ist ungeprüft — auf Staging gibt
  es keinen Knoten mit `;` oder `,` im Autorennamen. Folgenlos, seit `FN` nicht
  mehr escaped wird.
- **`repository_service.py`** steht bei 1062 Zeilen und 77 % Abdeckung. Die
  Dublettenerkennung — sie entscheidet, ob ein Upload überhaupt stattfindet —
  ist ungetestet. Ein Modulschnitt ist sinnvoll, aber erst ab höherer Abdeckung
  verantwortbar.
- **`contextName`/`schemaVersion`/`metadataset`** sind keine deklarierten
  Request-Felder, sondern werden aus den Metadaten gelesen. `carry_schema_markers()`
  vereinheitlicht das Verhalten; im OpenAPI-Vertrag steht es nicht.
