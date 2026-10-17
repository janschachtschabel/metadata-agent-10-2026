# Änderungen

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
> Einzelheiten: [DEPLOYMENT.md](DEPLOYMENT.md#update-von-20x-auf-210).

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

Stand: 2026-08-18 · gegenüber Commit `9abc2db`

Im Arbeitsbaum, noch nicht committet. Die Testsuite umfasst **1177 Tests**
(vorher 1176, davon 7 rot); `ruff check` und `ruff format --diff` sind sauber.

---

## Objektfelder: ein String in einem Objektfeld erreicht das Repository nicht mehr

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

## Intern (Objektfelder)

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

# Frühere Änderungen

Stand: 2026-08-12 · gegenüber Commit `52b008c` (CORS Fix) · **789 Tests**

Der folgende Abschnitt beschreibt den Stand von August 2026 vor der oben
beschriebenen Arbeit. Er ist inzwischen committet; die Testzahl und die
Referenz auf `52b008c` beziehen sich auf jenen Zeitpunkt.

---

## Was sich für Aufrufer ändert

### 1. Lizenz: kein `COPYRIGHT_FREE` mehr, wenn keine erkannt wurde

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

### 2. Lizenz: nicht zuordenbare Angaben gehen nicht mehr verloren

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

### 3. Lizenz: `creativecommons.org`-Links werden abgebildet (neu)

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

### 4. `collection_id` ist immer ein Array

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

### 5. Body-Format: alle drei Formen sind jetzt gleichwertig

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

### 6. Die Antwort sagt, gegen welches Schema geschrieben wurde (neu)

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

### 7. `workflow_steps`: leere Liste wird abgelehnt

`[]` liest sich wie „keine Schritte", hätte aber beim Weglassen auf den Standard
`200_tocheck` zurückgefallen — das Gegenteil. Jetzt `422`. Für einen Upload ohne
Workflow: `start_workflow: false`. Unbekannte Status geben ebenfalls `422`,
bevor etwas ins Repository geht.

### 8. `discarded_node` in der Antwort (neu)

Bricht ein Upload ab, nachdem der Node schon angelegt war, wird er
zurückgenommen (Papierkorb, wiederherstellbar) und seine ID steht in
`discarded_node`. Scheitert auch die Rücknahme, bleibt das Feld gesetzt — dann
existiert der Node und muss von Hand weg.

### 9. Autoren-VCARD folgt dem Repository-Format

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

### 10. Standard-LLM ist `gpt-5.6-luna`

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

### 11. Sechs Felder erreichen das Repository, die vorher weggefiltert wurden

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

### 12. Durchsatzgrenzen des LLM-Gateways sind einstellbar (neu)

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

### 13. AcademicCloud-Default ist ein Modell, das es gibt

`deepseek-r1` → **`openai-gpt-oss-120b`**. Der alte Default antwortete mit
`404 Model Not Found`; ein Default, der nicht funktionieren kann, ist schlechter
als keiner, weil nichts am Fehler auf die Einstellung zeigt. Das neue Modell
lieferte in der Messung vom 12.08.2026 die gleichmäßigste Latenz der
AcademicCloud-Modelle (4,99 s ± 2,31 s bei der Extraktion, p95 6,59 s im Chat).

### 14. Nativ OpenAI zieht mit der B-API-Implementierung gleich

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

### 15. `ENV-PARAMETER.md` — vollständige Referenz (neu)

Alle 43 Umgebungsvariablen an einer Stelle, mit Defaults und Betriebsbeispielen.
`README.md`, `INSTALL.md` und `DEPLOYMENT.md` verweisen darauf, statt jeweils
eine eigene, unvollständige Teilliste zu führen.

Zwei Tests halten das Dokument ehrlich: eine Einstellung im Code ohne Eintrag
lässt den Test fehlschlagen, und ein dokumentierter Name, den `Settings` nicht
mehr liest, ebenfalls — letzteres ist der unangenehmere Fall, weil das Setzen
dann so aussieht, als wirke es.

### 16. Der Lernressourcentyp kommt an — im richtigen Feld, aus dem vollen Vokabular

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

### 17. Zweistufiger Upload: `POST /node` und `node_id` am `/upload` (neu)

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

### 18. `cm:name` wird gesetzt, wenn das Repository keinen ableiten kann

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

### 19. Lizenzschlüssel: die Schreibweise entschied, ob die Lizenz existiert

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

### 20. Der Lernressourcentyp: das Modell sah die falschen 20

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

### 21. `/upload/verify` prüft jetzt, ob ein Wert überhaupt ein Wert ist

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

### 22. `/upload` zeigt seine Optionen — und ein leeres Feld heißt jetzt überall dasselbe

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

## Konfiguration

### `WLO_REPOSITORY_BASE_URL` entfernt

Die Variable war in `DEPLOYMENT.md` und `INSTALL.md` als Repository-Override
dokumentiert und wurde **von niemandem gelesen** — auch vor dem Refactor nicht.
Wer sie in Produktion setzte, bekam keinen Fehler und schrieb weiter nach
Staging.

Das Ziel-Repository wird ausschließlich über
**`METADATA_AGENT_REPOSITORY_URL`** gesetzt. Bestehende `.env`-Dateien mit der
alten Variable brechen nicht (`extra: "ignore"`).

### UTF-8-Ausgabe unter Windows

Die Fortschrittsausgabe enthält Emoji. Auf einer cp1252-Konsole brach ein Upload
damit mitten im Schreiben mit `UnicodeEncodeError` ab — ausgelöst schon von einem
Autorennamen wie „Philipp Lang". `src/__init__.py` stellt `stdout`/`stderr` beim
Import auf UTF-8. Unter Linux und Docker ändert das nichts.

---

## Intern

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

### Tests und Abdeckung

789 Tests (vorher keine). Abdeckung der Service-Schicht:

| Modul | Abdeckung |
|---|---|
| `repository_values.py` | 95 % |
| `repository_licenses.py` | 95 % |
| `repository_diff.py` | 95 % |
| `repository_curation.py` | 81 % |
| `repository_service.py` | 77 % |

---

## Was noch offen ist

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
