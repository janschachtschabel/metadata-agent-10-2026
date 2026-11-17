# Übergabe an den Betrieb (Team 6)

Stand 4.0.4. Gegliedert nach der Checkliste „Übergabepaket“ aus den
Anforderungen von Team 6. Einzelheiten stehen in
[ENV-PARAMETER.de.md](ENV-PARAMETER.de.md) und [DEPLOYMENT.de.md](DEPLOYMENT.de.md);
dieses Dokument verweist dorthin, statt sie zu wiederholen. Felder in
**[eckigen Klammern]** muss T4 noch ausfüllen. Eine Kurzübersicht, welche
Anforderungen erfüllt sind, steht in
[docs/team6-anforderungen.md](docs/team6-anforderungen.md).

## 1. Repo, CI, Image

| | |
|---|---|
| Repo | API: <https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent>; Webkomponente: <https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent-webcomponent> (das gebaute Widget liegt im API-Repo unter `src/static/widget/`). Ob T6 Zugriff hat, ist noch zu bestätigen. |
| CI | `.gitlab-ci.yml` baut bei jedem Push auf `main`/`develop` ein Image mit dem Branch als Tag und bei jedem Git-Tag `v*` ein Image mit der Version (z.B. `v4.0.4`); andere Tags veröffentlichen nichts. Produktion nur mit Versions-Tags. Eine geplante Pipeline (in GitLab unter CI/CD > Schedules auf `main` anzulegen) führt nur die Jobs `scheduled dependency checks` (`pip-audit`, Alter des Chromium) und `scheduled image scan` (Trivy über das neueste Release-Image oder `SCAN_TAG`, SBOM als Artefakt) aus: Lücken, die nach einem Release bekannt werden, fallen so ohne neuen Push auf. |
| GitLab-CI/CD-Variablen | Die Docker-Jobs in `.gitlab-ci.yml` brauchen: `DIND_IMAGE`, `DIND_HOST`, `DIND_DRIVER`, `DIND_TLS_CERTDIR` (Docker-in-Docker), `DOCKER_REGISTRY`, `DOCKER_USERNAME`, `DOCKER_PASSWORD` (maskiert). Test- und Ruff-Jobs brauchen keine. Die Webkomponente braucht keine eigenen Variablen. |
| GitHub-Spiegel | `.github/workflows/docker-publish.yml`: Tests, Chart-Prüfung und Image-Bau bei Pushes auf `main`, Tags `v*` und Pull Requests auf `main`; nach DockerHub nur außerhalb von Pull Requests und mit Secret `DOCKERHUB_PASSWORD`. `.github/workflows/scheduled-checks.yml` wiederholt täglich (04:23 UTC) und auf Knopfdruck `pip-audit`, die Altersprüfung des Chromium und den Trivy-Scan des neuesten Release-Images. Dependabot (`.github/dependabot.yml`) schlägt Updates für `uv.lock` (damit Playwright/Chromium), Basis-Images und Actions vor |
| Helm-Chart | [`helm/metadata-agent/`](helm/metadata-agent/README.md); die CI prüft ihn mit `helm lint` und die Alarmregeln mit `promtool` (Jobs `helm lint`, `alert rules`) |
| Prüfungen vor einem Image | Tests mit Coverage-Untergrenze, mypy, `pip-audit` (Job `pip audit`); das gebaute Image startet schreibgeschützt ohne Schlüssel und muss `/health` und `/ready` beantworten, auf dem internen Port `/ready` und `/metrics` liefern, `tini` als PID 1 haben und Chromium starten (`scripts/smoke-image.sh`), Trivy bricht bei kritischen, behebbaren Lücken ab. Zu jedem Versions-Tag schreibt die GitLab-CI eine Stückliste (`sbom.cdx.json`, CycloneDX) als Artefakt. In Merge Requests baut und prüft der Job `check docker image in merge requests`, ohne zu veröffentlichen |
| Image | Multi-Stage auf `python:3.13-slim` (per Digest gepinnt, Aktualisierung im Kopf des Dockerfiles), läuft als Nutzer `appuser` (UID 1000), Code gehört root, `.dockerignore` vorhanden, HEALTHCHECK auf `/health`. `tini` ist der Init-Prozess (ENTRYPOINT) und räumt Prozesse ab, die ein beendetes Chromium hinterlässt; uvicorn hält Verbindungen 75 s offen (`--timeout-keep-alive 75`) und lauscht auf der Adresse aus `UVICORN_HOST` (im Image `0.0.0.0`). Enthält für Screenshots Chromium, nur die Headless-Shell (`playwright install --only-shell`), und ist daher größer als ein reiner API-Dienst. Läuft mit schreibgeschütztem Root-Dateisystem, wenn `/tmp` und `/home/appuser` beschreibbar sind; der CI-Smoke-Test startet das Image so und lässt darin Chromium starten (lokal nicht geprüft, weil das Bauen Downloads braucht). |
| Daten im Image | nur die Schemata (wenige MB); keine Modelle oder Korpora |

## 2. API

- **OpenAPI-Spec:** `/openapi.json`, Swagger-UI unter `/docs`. Die Spec wird
  aus dem Code erzeugt. Swagger UI liefert der Agent selbst aus (keine Anfragen
  an CDNs oder Google Fonts); `/redoc` gibt es nicht mehr.
- **Anzeigename:** `Metadata Agent API`.
- **Beschreibung für Endnutzende** (steht als `info.summary` in der Spec):
  „Erschließt Bildungsinhalte mit KI: Aus einem Text, einer Webseite oder einem
  Repository-Eintrag erzeugt der Dienst passende Metadaten wie Titel,
  Beschreibung, Schlagworte, Bildungsstufe und Inhaltsart.“

### Ports und auszublendende Pfade

| Port | Inhalt | Ingress |
|---|---|---|
| 8000 | öffentliche API, `/docs`, `/openapi.json`, Widget unter `/widget/*` | ja |
| `METADATA_AGENT_INTERNAL_PORT` (z.B. 9000) | `/health`, `/ready`, `/metrics` | **nein** |

- Nicht in der Spec stehen `/health`, `/ready`, `/debug/auth` und
  `/debug/dependencies`.
- `/health` und `/ready` gibt es zusätzlich auf Port 8000, damit bestehende
  Probes und der Docker-HEALTHCHECK weiter funktionieren.
- `/debug/*` antwortet nur mit `METADATA_AGENT_DEBUG_ENDPOINTS=true`, sonst mit
  `404`. Diese Pfade bitte zusätzlich am Ingress sperren.
- `/metrics` gibt es nur auf dem internen Port.

### Header

| Header | Richtung | Wozu |
|---|---|---|
| `Authorization: Bearer <key>` | ein | Vom B-API-Proxy: die Anmeldung des Aufrufers: sein Schlüssel (`forwardApiKey`, ältere B-API) oder ein Delegations-Token dafür (`forwardDelegation`). Er wird für die KI-Aufrufe unverändert weitergegeben. |
| `x-client-trace-id`, `x-request-id`, `x-b3-*`, `b3` | ein → weiter | Tracing. Wird unverändert an B-API und Repository weitergegeben (Textextraktion und Geocoder laufen über die B-API), nie direkt an Fremddienste wie Photon. Ein Wert mit mehr als 128 Zeichen oder anderen Zeichen als Buchstaben, Ziffern, `.`, `_`, `-` entfällt; anstelle eines `x-request-id` mit mehr als 64 Zeichen geht die Anfrage-ID des Agenten weiter. Fehlt eine B3-Trace-ID oder ist sie keine (16 oder 32 Hex-Zeichen), legt der Agent eine an. |
| `X-Request-ID` | aus | Anfrage-ID (gleich `x-client-trace-id`, wenn vorhanden, sonst `x-request-id`, sonst die Trace-ID). Steht auch in Fehlerantworten als `error.request_id` und in der Logzeile zum Fehler. Ohne diesen Header können Nutzer einen Fehler nicht eindeutig melden. |

### Content-Types und Antwortzeiten

- **Content-Types:** Die API spricht nur `application/json`; Bilder kommen
  base64-kodiert im JSON. Das Widget liefert statische Dateien aus: JS, CSS,
  `woff2`-Schriften, HTML-Demoseiten und JSON-Übersetzungen. Es gibt keine
  Streams und keine Datei-Uploads per multipart.
- **Antwortzeiten:**
  - `/generate` mit etwa 50 Feldern: mit `gpt-6-luna` rund 5 s für einen
    Text mit 4.400 Zeichen (gemessen 15.11.2026; vorher 9,3 s), Webseiten
    12–22 s (gemessen 16.10.2026), mit AcademicCloud bis 90 s (Warteschlange
    am Gateway).
  - `/extract-field`, `/detect-content-type`: wenige Sekunden.
  - Screenshots: höchstens 90 s.
  - Sehr lange Quellen: ein Wikipedia-Artikel mit 412.000 Zeichen (Goethe)
    brauchte ungekürzt 56 KI-Aufrufe à Ø 34 s, zusammen 126 s (19.10.). Der
    Quelltext wird seitdem auf 150.000 Zeichen gekürzt (`processing.warnings`
    sagt es dann); derselbe Artikel braucht so 35–42 s.
  - **Proxy- und Ingress-Timeout für `/generate`: mindestens ~310 s** – im
    B-API-Proxy, in edu-sharing und in einem eigenen Ingress (der Chart setzt
    320 s). Der Agent bricht einen Lauf nach `METADATA_AGENT_GENERATE_TIMEOUT`
    (300 s) selbst ab und antwortet `504` mit `error.code: GENERATION_TIMEOUT`
    und Request-ID. Ein kürzeres Timeout davor schneidet lange Erschließungen
    ab, bevor der Agent fertig ist.
- **Statuscodes:** `4xx` = die Anfrage ist falsch (fehlender Parameter oder
  ungültiges JSON `400` – bei jedem Endpunkt mit Body –, Node außerhalb des
  Bearbeitungsfensters `403`, Node, Kontext oder Schema unbekannt `404`, auch bei
  `/validate` und `/export/markdown`, ebenso ein Pfad mit angehängtem Schrägstrich
  (der Agent leitet nicht weiter), Body zu groß `413`, gültiges JSON mit falschem
  Inhalt `422`, auch `SCHEMA_UNAVAILABLE` beim Schreiben); `502` = ein abhängiger Dienst (KI,
  Textextraktion, Repository) hat versagt – dazu zählt ein Schlüssel, den die
  B-API für die Textextraktion ablehnt (`TEXT_EXTRACTION_AUTH_REFUSED`) oder
  der fehlt (`TEXT_EXTRACTION_AUTH_MISSING`); `503` = Repository-Zugang nicht
  konfiguriert (`/upload`, `/nodes`, `/workflow`), keine Textextraktion
  eingerichtet (`TEXT_EXTRACTION_NOT_CONFIGURED`) bzw. `/ready` nicht bereit;
  `504` = Zeitlimit; `499` im Log und in den Metriken = der Aufrufer hat
  die Verbindung geschlossen, der Lauf wurde beendet (in
  `metadata_agent_generations_total` als `cancelled` gezählt). Jeder Fehler hat `detail`, `error.code` und
  `error.request_id`, auch eine unerwartete Ausnahme (`500`, `INTERNAL_ERROR`).
  `/upload`, `/nodes` und `/workflow` melden mit `200` und `success:false` nur
  noch Teilergebnisse (der Node existiert) und Dubletten; ein Schreiben ohne
  Ergebnis ist `422`/`502`.
- **Async-Jobs** (Job anlegen, Status abfragen) gibt es nicht. Das wäre der
  nächste Schritt, falls ~310 s im Proxy nicht gehen.

## 3. B-API

**Variante A (Standard): über die B-API geroutet.**

- **Virtueller Key:** wird gebraucht, denn der Dienst ruft selbst LLMs auf.
  `METADATA_AGENT_B_API_AUTH_MODE=auto` nutzt den weitergereichten Schlüssel
  und fällt sonst auf `B_API_KEY` zurück; `forward` nutzt nur den
  weitergereichten.
- **Rechte des Keys:** Zugriff auf die LLM-Endpunkte der B-API (`openai`, bei
  Routing `router`) **und** auf die Proxy-Dienste `text-extraction` (Erschließen aus einer URL) und `photon_geocoding` (Koordinaten von Orten).
  **Kein** `LLM_ROUTE_MANAGE`. Das gilt für jeden Schlüssel, mit dem der Dienst
  aufgerufen wird: die Nutzer- und Gast-Schlüssel aus edu-sharing und
  `B_API_KEY`. Fehlt das Recht, versucht der Agent mit `auto` seinen eigenen
  `B_API_KEY` und dann den Dienst direkt (Warnung im Log, 10 Minuten gemerkt).
  Erst wenn alle Wege scheitern, oder mit `proxy`, endet das Erschließen aus
  einer URL mit `502` `TEXT_EXTRACTION_AUTH_REFUSED`, und Orte bleiben ohne
  Koordinaten (`processing.warnings`).
- **Dienste über die B-API** (`METADATA_AGENT_B_API_SERVICES=auto`):
  Textextraktion und Geocoder ruft der Agent über die Pfade der B-API auf
  (`/api/v1/proxy/text-extraction`, `/api/v1/proxy/photon_geocoding`), mit
  demselben Schlüssel wie die LLM-Aufrufe – aber nur, wenn die LLM-Aufrufe an
  `METADATA_AGENT_B_API_BASE_URL` gehen. `direct` ruft beide direkt auf.
- **Modell-Routing** (`METADATA_AGENT_LLM_PROVIDER=b-api-router`):
  - Der Default `openai/gpt-6-luna` braucht keine Route.
  - Die Route legt ein Administrator **in der B-API** an; in der Env steht nur
    ihr Name. Muster in
    [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md#wo-routen-definiert-werden).
  - **Qualität reicht, Durchsatz nicht:** Laut Kurztest vom 15.10.2026 (siehe
    unten) genügen die Qwen-Modelle inhaltlich. Das gemeinsame GWDG-Kontingent
    (~10 Aufrufe pro Minute) reicht für den Produktivbetrieb aber nicht. Bis
    das Kontingent steigt, bleibt `b-api-openai` mit `gpt-6-luna` der
    Standard.

**Kurztest Qwen gegen `gpt-6-luna`, 15.10.2026.** Getestet wurden zwei Seiten,
LEIFIphysik „Gleichförmige Bewegung“ (Lernmaterial) und DHM „Ausstellungen“
(Veranstaltung). Gemessen wurden die Erkennung der Inhaltsart und 13 Felder je
Seite, über den Router mit `academiccloud/<modell>` und dem Code des Agenten.

Erster Lauf, mit den damaligen Fehlern im Agenten:

| | `gpt-6-luna` | `qwen3-30b-a3b-instruct-2507` | `qwen3.6-35b-a3b` |
|---|---|---|---|
| Inhaltsart erkannt | 2/2 | 2/2 | 0/2 (Antwort unter falschem Schlüssel, s.u.) |
| Felder gefüllt | 10 + 10 von 13 | 11 + 11 von 13 | 10 + 4 von 13; mit `reasoning_effort: none` 9 + 9 |
| Titel, Beschreibung, Schlagworte | gut | gut | gut, sobald es antwortet |
| Daten (Beginn/Ende) | richtig | richtig | richtig |
| Ort (DHM) | „Zeughaus“ (dort geschlossen) | „Pei-Bau“ (richtig) | – |
| Fach (Physik-Seite) | Physik | Chemie ✗ | Chemie ✗ |
| Dauer für 14 Aufrufe | ~4 s | ~90 s | ~90 s |

Nachtest nach den Korrekturen: vollständiges Vokabular, Schlüssel `value`
angenommen, `qwen3.6` mit `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none`.

| | `gpt-6-luna` | `qwen3-30b-a3b-instruct-2507` | `qwen3.6-35b-a3b` |
|---|---|---|---|
| Inhaltsart erkannt | 2/2 | 2/2 | 1/2 (eine Antwort als Liste, inzwischen behoben) |
| Felder gefüllt | 12 + 9 von 13 | 11 + 11 von 13 | 10 + 9 von 13 |
| Fach | Physik, Geschichte | Physik, Geschichte | Physik, Geschichte |
| Leere Antworten | – | – | keine |

Bei Qwen bestimmt die Drosselung auf 9 Aufrufe pro Minute die Dauer, nicht
das Modell.

- **Qualität der Texte:** Bei Titel, Beschreibung, Schlagworten und Daten
  reicht `qwen3-30b-a3b-instruct-2507`. Es rät aber eher Werte, die nicht im
  Text stehen, etwa Bildungsstufe und Fach für die Ausstellung.
- **Ergebnis:** Mit den Korrekturen erreichen beide Qwen-Modelle bei den
  getesteten Feldern etwa das Niveau von `gpt-6-luna`. `qwen3-30b` füllt
  etwas mehr Felder, rät aber eher Werte, die nicht im Text stehen.
- **Drei Ursachen lagen im Agenten, nicht im Modell; alle sind behoben:**
  1. Der Prompt zeigte nur die ersten 20 Begriffe eines Vokabulars. „Physik“
     steht in der Fächerliste an Position 46 von 69, „Chemie“ an Position 10.
     Wer sich an die Liste hielt, konnte Physik gar nicht wählen. Jetzt stehen
     bis zu 300 Begriffe im Prompt.
  2. Für die Inhaltsart nutzt der Agent den System-Prompt der Feldextraktion.
     Der verlangt den Schlüssel `value`, der Nutzer-Prompt `content_type`.
     `qwen3.6` folgte dem System-Prompt (`{"value": "event.json"}`), und der
     Agent verwarf die richtige Antwort. Jetzt gilt beides, auch eine Antwort
     als Liste mit einem Eintrag.
  3. Denkende Modelle wie `qwen3.6` brauchen `reasoning_effort: "none"`. Ohne
     den Parameter verbrauchen sie das Token-Budget fürs Nachdenken und liefern
     `content: null`, was der Agent als „kein Wert“ zählte. Jetzt sendet ihn
     `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT`, und eine leere Antwort
     ist der Fehler `LLM_EMPTY_ANSWER`.
- **Durchsatz:** Ein vollständiges `/generate` braucht ~50 Aufrufe. Beim
  gemeinsamen GWDG-Limit von ~10 pro Minute sind das 5–6 Minuten je
  Erschließung, unabhängig von der Qualität. Für die interaktive Nutzung
  reicht das ohne höheres GWDG-Kontingent nicht.

**Variante B (ohne B-API).**

- **LLM-Zugang per Env (ausgehend):** wie in 2.0.x `B_API_KEY` für die
  LLM-Endpunkte der B-API (`METADATA_AGENT_B_API_AUTH_MODE=static`, oder
  `auto` ohne weitergereichten Header). Alternativ
  `METADATA_AGENT_LLM_PROVIDER=openai` mit `OPENAI_API_KEY` und optional
  `METADATA_AGENT_OPENAI_API_BASE` für einen anderen OpenAI-kompatiblen
  Endpunkt.
- **Lücke, Auth für Aufrufer (eingehend):** Der Dienst prüft nicht, wer ihn
  aufruft. Ohne B-API muss ein anderer Proxy den Zugang absichern.

**Netzwerk:** In Variante A darf der Dienst nur über die B-API erreichbar sein.
Sonst laufen Anfragen ohne Header im Modus `auto` auf den eigenen `B_API_KEY`.
Der Helm-Chart erzwingt das mit einer NetworkPolicy (Standard an, siehe §6).

**Offene Entscheidung, Lesezugriffe mit dem Service-Konto (Audit S-05):**
`node_id`/`node_url` bei `/generate` (Volltext als `_source_text`),
`/upload/verify` und `/upload` mit erkannter Dublette (`return_full_node`)
lesen mit den Rechten von `WLO_GUEST_USERNAME` – auch nicht öffentliche Nodes,
etwa aus der Inbox. Wer den Dienst erreicht, bekommt diese Inhalte. Solange nur
die B-API den Dienst erreicht, gehört das zur akzeptierten Lücke „Auth für
Aufrufer“. Alternativen: Lesezugriffe anonym oder mit dem Ticket des Aufrufers,
`node_full` bei Dubletten weglassen.

**Bewusst so belassen (Audit vom 18.10.):**

| Befund | Entscheidung | Begründung |
|---|---|---|
| B-02 DNS-Rebinding im Screenshot-Browser | Schutz über die Egress-NetworkPolicy | Chromium löst Namen selbst auf; der Agent prüft jede Anfrage (`route`), jede Weiterleitung und jede WebSocket-Verbindung (S-09), aber gegen eine zweite DNS-Antwort hilft nur die Netzebene. Alle Seitenressourcen durch den Agenten zu leiten, würde Screenshots echter Seiten verlangsamen und brechen (Streaming, Encodings). Die Policy sperrt private Netze (Chart 0.3.0). Der Vorschau-Abruf des Agenten selbst ist an die geprüfte Adresse gebunden (S-04). |
| S-11 Chromium ohne Sandbox | bleibt (Playwright-Standard im Container) | Die Chromium-Sandbox braucht im Container unprivilegierte User-Namespaces oder ein eigenes seccomp-Profil; beides ist im Cluster nicht zugesichert, die Sandbox würde Screenshots dort abbrechen. Ausgleich: nicht-root, `readOnlyRootFilesystem`, keine Service-Account-Tokens, Egress-Policy. Ein eigener Screenshot-Pod ohne Secrets wäre der nächste Schritt. |
| P-04 HTTP-Client je Repository-Aufruf | bleibt | Ein Upload nutzt einen Client für alle seine Aufrufe; ein Client je Anfrage kostet einen TLS-Aufbau (~50 ms) gegenüber Abläufen von Sekunden, bindet Verbindungen an die Lebensdauer der Anfrage und ist die Naht, an der die Tests das Repository ersetzen. |
| S-12 CORS-Default `*` | bleibt | `allow_credentials` ist aus und der Dienst nimmt keine Cookies an: Eine fremde Seite kann nichts im Namen eines Nutzers tun, was sie nicht auch direkt könnte. Das Widget wird in beliebige Seiten eingebettet und ruft den Agenten von dort auf; eine Liste der Ursprünge müsste jede Einbettung kennen. Hinter dem B-API-Proxy gilt dessen CORS; einschränken lässt es sich mit `METADATA_AGENT_CORS_ORIGINS`. Neu sind `X-Content-Type-Options: nosniff` und `Referrer-Policy: no-referrer`. |
| OPS-11 Annotation `checksum/secret` | entfernt statt gesalzen | Ein Salz, das sich nicht aus dem Secret ableiten lässt, kann `helm template` nicht stabil erzeugen; ein zufälliges würde jeden Upgrade die Pods neu starten lassen; mit `existingSecret` änderte sich die Annotation ohnehin nie. Nach einer Änderung des Secrets: `kubectl rollout restart deployment/<name>` (DEPLOYMENT.de.md). |
| OPS-09 `$DIND_IMAGE` ohne Digest | Projektvariable | Das Image kommt aus den GitLab-Gruppenvariablen, nicht aus dem Repo; dort per Digest setzen. |
| S-08 Testseiten unter `/widget/examples` | bleiben | `test.html`, `uri-test.html`, `dual-instance-test.html` sind dokumentierte Werkzeuge für Integratoren (README.de.md, `/widget/info`) und rufen nur die öffentliche API auf. Das öffentliche `/health` nennt keine Konfiguration mehr. |

## 4. Monitoring und Logging

- **`/metrics`** (Prometheus) auf dem internen Port:
  - Prozess- und Python-Laufzeitmetriken;
  - `metadata_agent_http_requests_total` und `…_duration_seconds` (nach
    Methode, Routen-Vorlage, Status; Buckets bis 300 s, ab 120 s in Schritten
    von 30 s);
  - `metadata_agent_llm_requests_total` (nach Provider und Ergebnis: `ok`,
    `cancelled` oder Fehlercode; zusätzlich nach `auth_source` – `forwarded`,
    `static`, `openai`, `none` – und `config` – `default`, oder `override`,
    wenn die Anfrage Provider/Modell gewählt hat) und `…_duration_seconds`
    (nach Provider; gemessen ist, was der Aufrufer wartet: auf einen freien
    Platz innerhalb der LLM-Grenzen, die Anfrage, ihre Wiederholungen und die
    Pausen dazwischen);
  - `metadata_agent_llm_last_failure_timestamp_seconds` (Labels des
    LLM-Zählers) und `metadata_agent_http_last_internal_error_timestamp_seconds`
    (nach Route): wann zuletzt ein KI-Aufruf mit einem Fehler endete bzw. der
    Agent mit `500` antwortete – daran erkennen die kritischen Regeln den
    ersten Fehler;
  - `metadata_agent_generations_total` (nach `success`, `partial`, `failed`,
    `cancelled` – Aufrufer hat abgebrochen –, `refused` – `503`
    `TOO_MANY_GENERATIONS`);
  - `metadata_agent_upstream_requests_total` (gesendet),
    `…_responses_total` (beantwortet, nach HTTP-Status; für `other` nur nach
    Statusklasse wie `2xx`) und `…_response_duration_seconds` je abhängigem
    Dienst (`llm`, `text-extraction`, `repository`, `geocoder`, `other`);
    gesendet minus beantwortet sind Aufrufe ohne Antwort;
  - `metadata_agent_llm_tokens_total` (nach Provider und `prompt`, `cached`,
    `cache_write`, `completion`), auch für den Cache-Vorlauf.
- **Logs** gehen nach stdout. `METADATA_AGENT_LOG_FORMAT=json` schreibt ein
  Objekt je Zeile mit `timestamp`, `level`, `service`, `logger`, `message`, `trace_id`, `span_id` und `request_id`
  (`service` aus `METADATA_AGENT_SERVICE_NAME`, `trace_id` und `span_id` nach B3).
  Je Anfrage steht eine Zeile mit Methode, Pfad, Status und Dauer im Log, ohne
  Query-String und Header; das Access-Log von uvicorn ist aus.
  `METADATA_AGENT_LOG_LEVEL` steuert die Schwelle.
- **Alarmregeln:** dreizehn Regeln in
  [`helm/metadata-agent/files/alerts.yaml`](helm/metadata-agent/files/alerts.yaml),
  vom Chart als `PrometheusRule` installiert und je Namespace ausgewertet.
  Kritisch sind nur Fehler mit dem eigenen Schlüssel und dem konfigurierten
  Modell; Fehler mit dem weitergereichten Schlüssel eines Aufrufers oder einem
  per Anfrage gewählten Modell warnen nur.
  | Regel | Schwere | Löst aus bei |
  |---|---|---|
  | `MetadataAgentLlmKeyRefused` | critical | einem `LLM_AUTH_*` in 10 min mit eigenem oder fehlendem Schlüssel und konfiguriertem Modell |
  | `MetadataAgentLlmRouteMisconfigured` | critical | einem `LLM_ROUTE_*` oder `LLM_MODEL_NOT_FOUND` in 10 min mit konfiguriertem Modell |
  | `MetadataAgentLlmCallerKeyRefused` | warning | mehr als 5 × `LLM_AUTH_*` in 15 min mit weitergereichtem Schlüssel |
  | `MetadataAgentLlmRequestedModelRejected` | warning | mehr als 5 Fehlern zu Route, Modell oder Berechtigung in 15 min mit per Anfrage gewähltem Modell |
  | `MetadataAgentLlmThrottled` | warning | mehr als 5 × `LLM_RATE_LIMITED` in 15 min |
  | `MetadataAgentLlmUnreachable` | warning | mehr als 3 × `LLM_UNREACHABLE` in 10 min |
  | `MetadataAgentGenerationsFailing` | warning | über 50 % `failed` in 30 min (ab 3 beendeten Läufen; `cancelled` und `refused` zählen nicht mit) |
  | `MetadataAgentUpstreamUnanswered` | warning | mehr als 3 Aufrufen ohne Antwort in 10 min (je Dienst, ohne `llm` und `other`), 5 min lang – laufende Aufrufe zählen anfangs als unbeantwortet |
  | `MetadataAgentUpstreamServerErrors` | warning | mehr als 5 Antworten mit 5xx in 10 min (je Dienst, ohne `other`) |
  | `MetadataAgentUpstreamKeyRefused` | warning | mehr als 3 Antworten mit 401 oder 403 in 10 min von Textextraktion oder Geocoder: über die B-API fehlt dem Schlüssel das Recht für den Proxy-Dienst; direkt aufgerufen verlangt der Dienst selbst eine Anmeldung |
  | `MetadataAgentGenerateSlow` | warning | mehr als 5 % der `/generate`-Läufe über 270 s (Zeitlimit 300 s), 15 min lang; es zählen nur Pods, die den 270-s-Bucket ausgeben |
  | `MetadataAgentServerErrors` | warning | mehr als 5 eigenen Antworten mit 5xx in 10 min (je Route, ohne `/health`, `/ready` und ohne die `503` `TOO_MANY_GENERATIONS` von `/generate`) |
  | `MetadataAgentInternalError` | critical | einem `500` (`INTERNAL_ERROR`, ein Fehler im Agent selbst) in 10 min, auch dem ersten nach dem Start eines Pods |

  Die drei kritischen Regeln erkennen den ersten Fehler an den beiden
  Zeitstempel-Metriken (letzter Fehler vor weniger als 10 Minuten); eine
  Abruflücke lässt einen alten Fehler nicht erneut auslösen. Dafür müssen die
  Uhren von Pod und Prometheus auf deutlich weniger als 10 Minuten
  übereinstimmen. Ein Pod eines älteren Images hat die beiden Metriken nicht;
  bei ihm entscheidet allein der Zähler.

  `promtool test rules helm/metadata-agent/tests/alerts.test.yaml` prüft jede
  Regel mit Testdaten (auslösend und nicht auslösend). Pod-Ausfälle melden die
  Cluster-Regeln (`TargetDown`, `KubePodCrashLooping`). Wer den Alarm bekommt,
  legt T6 im Alertmanager fest.

## 5. Zustand, Skalierung, Architektur

![Architektur-Map, Betrieb über die B-API](docs/architektur/metadata-agent-architektur-b-api.png)

Betrieb über die B-API (Standard: LLM, Textextraktion und Geocoder gehen über
die B-API): [PNG](docs/architektur/metadata-agent-architektur-b-api.png),
[SVG](docs/architektur/metadata-agent-architektur-b-api.svg), interaktiv
[HTML](docs/architektur/metadata-agent-architektur-b-api.html). Direkter
Betrieb ohne B-API:
[PNG](docs/architektur/metadata-agent-architektur.png),
[SVG](docs/architektur/metadata-agent-architektur.svg),
[HTML](docs/architektur/metadata-agent-architektur.html).

Ablauf einer Anfrage über die B-API (Schlüssel, Tracing-Header, Timeouts):
[PNG](docs/architektur/metadata-agent-ablauf-b-api.png),
[SVG](docs/architektur/metadata-agent-ablauf-b-api.svg),
[HTML](docs/architektur/metadata-agent-ablauf-b-api.html); direkter Betrieb:
[PNG](docs/architektur/metadata-agent-ablauf.png),
[SVG](docs/architektur/metadata-agent-ablauf.svg),
[HTML](docs/architektur/metadata-agent-ablauf.html). Kurzfassung der Übergabe
als Steckbrief im gemeinsamen Schema für Dienste:
[docs/steckbrief.md](docs/steckbrief.md).

- **Zustandslos:** keine Datenbank, keine Sessions, keine Dateien zur Laufzeit.
  Jeder Pod kann jede Anfrage beantworten, auch mehrere Pods nebeneinander.
- **Grenzen je Pod:** gleichzeitige KI-Aufrufe (Default 20) und
  Screenshot-Plätze (2) gelten pro Prozess. Bei N Pods addieren sie sich.
  Das gemeinsame GWDG-Limit (~10 Aufrufe/min, höchstens 2 gleichzeitig) gilt
  dagegen für den Schlüssel und damit für alle Pods zusammen, deshalb dort
  nicht über Replicas skalieren. Im Helm-Chart teilt
  `sharedLlmLimit.enabled=true` das Limit durch `replicaCount`
  (DEPLOYMENT.de.md, „Skalierung, Durchsatz und Ressourcen“).
- **Ein Worker je Container:** Der interne Port und die Metriken setzen das
  voraus. Skaliert wird über Replicas.

```
Browser ─ edu-sharing ─ B-API-Proxy ─┐
                                      ▼
                         Metadata Agent (FastAPI, 1 Prozess)
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
          B-API                   edu-sharing               Chromium im
   (ein Schlüssel für alles)      Repository          Container (Screenshots)
     ├─ LLM (OpenAI, AcademicCloud, Router)
     ├─ Textextraktion  /api/v1/proxy/text-extraction
     └─ Photon-Geocoder /api/v1/proxy/photon_geocoding
```

So ist es im Betrieb über die B-API (Standard). Ohne B-API oder mit
`METADATA_AGENT_B_API_SERVICES=direct` ruft der Agent die Textextraktion und
`photon.komoot.io` direkt auf, ohne Schlüssel. Mit `auto` fragt er
`photon.komoot.io` auch dann direkt, wenn der Weg über die B-API zum Geocoder
scheitert (Adressen gehen dann ohne Schlüssel an den Fremddienst Photon). Grafiken zu beiden Betriebsarten:
[docs/architektur](docs/architektur/README.md).

## 6. Ressourcen, Daten, Konfiguration

- **Ressourcen:** Request 500m CPU / 512Mi, Limit 2 CPU / 2Gi. Das ist
  geschätzt und **nicht unter Last gemessen**; empfohlen ist ein Lauf mit
  `docker stats` während eines `/generate` mit Screenshot. Speicherspitzen
  entstehen durch Chromium.
- **Datenbanken, Redis, Volumes:** keine. Im Helm-Chart gibt es nichts
  abzuschalten.
- **Konfiguration:** alles per Env, mit Defaults in
  [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md). Secrets: `B_API_KEY` (im Modus
  `forward` nicht nötig), `OPENAI_API_KEY` (nur Variante B),
  `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`. Referenz: `Dockerfile`,
  `docker-compose.yml`. `METADATA_AGENT_REQUEST_TIMEOUT` (Timeout je
  KI-Aufruf, 90 s) und `METADATA_AGENT_GENERATE_TIMEOUT` (ganzes `/generate`,
  300 s, samt Abruf der Quelle) sind in Code, Compose, Chart und Template
  einheitlich, ein Puffer unter dem Ingress-Timeout von 320 s. Geocoding:
  höchstens 10 Abfragen je Anfrage. Eine Einstellung mit ungültigem Wert
  (unbekannte Auswahl, Zahl außerhalb ihres Bereichs) beendet den Start mit
  einer Fehlermeldung, statt still einen anderen Wert zu nehmen; die Bereiche
  stehen in ENV-PARAMETER.de.md.

Helm-Chart: [`helm/metadata-agent/`](helm/metadata-agent/README.md) mit
Deployment (Probes auf dem internen Port, 320 s Grace-Period für laufende
Erschließungen), zwei Services (`<name>` mit Port 8000 für die API,
`<name>-internal` als ClusterIP mit dem internen Port, den der ServiceMonitor
abfragt), PodDisruptionBudget, NetworkPolicy, Secret, `ServiceMonitor` und
`PrometheusRule`. Der Chart bricht das Rendern ab, wenn Werte nicht
zusammenpassen (z.B. leere B-API- oder Repository-Adresse, `proxy-body-size`
ungleich `METADATA_AGENT_MAX_REQUEST_BYTES`; Liste in der README des Charts). Ein Ingress (Annotationen `proxy-read-timeout 320`,
`proxy-body-size 20m`) ist abschaltbar vorhanden, gehört aber nicht vor einen
Dienst hinter der B-API: Der Proxy ruft den Service im Cluster auf; bei
aktivem Ingress im Modus `auto` warnt `helm install`.
Die Werte in `values.yaml` sind per Test mit dem Code abgeglichen. Geprüft mit
`helm lint --strict` und kubeconform gegen Kubernetes 1.33 (16.10.2026; seit
18.10. läuft kubeconform in beiden CI), nicht in einem echten Cluster
installiert – die Prüfliste für die erste Installation im Cluster steht in
DEPLOYMENT.de.md, ebenso der Abschnitt Rollback. `/debug/*` ist per
`METADATA_AGENT_DEBUG_ENDPOINTS=false` aus; der interne Port ist nicht im
Ingress.

**Schreibgeschütztes Root-Dateisystem** (`securityContext.readOnlyRootFilesystem`):
`/tmp` und `/home/appuser` sind `emptyDir` (Chromium). **TLS:** Der Chart setzt
`FORWARDED_ALLOW_IPS` aus `forwardedAllowIps` (Standard `"*"`, vertretbar nur
mit der NetworkPolicy), damit uvicorn `X-Forwarded-Proto` und
`X-Forwarded-For` übernimmt. Keine Antwort hängt davon ab: `/widget/info`
liefert relative Verweise, und der Agent leitet nicht weiter (DEPLOYMENT.de.md,
„Hinter TLS“).

**NetworkPolicy (`networkPolicy.*`, Standard an):** Port 8000 nur aus den
Namespaces in `ingressNamespaces` (Standard `b-api`), Port 9000 nur aus
`prometheusNamespaces` (Standard `monitoring`). Ausgehend: DNS nur zum
Cluster-DNS (`networkPolicy.dns`), die Namespaces in `egressNamespaces`
(Standard `b-api`) nur auf `egressPorts` (Ports der Ziel-Pods, Standard 80,
443, 8000, 8080) und das öffentliche Internet (IPv4) ohne private Netze
(`egressExceptCidrs`), damit Vorschau- und Screenshot-URLs keine
Cluster-Adressen erreichen. Grenzen (IPv6, öffentliche Namen mit Ziel im
selben Cluster per LoadBalancer/DNAT, NodeLocal DNSCache): DEPLOYMENT.de.md,
„Grenzen der NetworkPolicy“. Der Pod bekommt kein Service-Account-Token
(`automountServiceAccountToken: false`). **Vor der Installation anpassen:** die
Namespace-Namen des Clusters; läuft die Textextraktion oder edu-sharing im
Cluster, deren Namespace in `egressNamespaces`; mit Ingress (Variante B) den
Namespace des Ingress-Controllers in `ingressNamespaces`. Die Namespaces werden
über das Label `kubernetes.io/metadata.name` gewählt, das Kubernetes selbst
setzt. Die Policy wirkt nur mit einem CNI, das NetworkPolicies durchsetzt
(Calico, Cilium u.a.).

## 7. Kosten

**Gemessen am 16.10.2026** mit `gpt-6-luna` über die B-API (Staging), echte
Webseiten als Eingabe, Standardeinstellungen des Agenten. Ein `/generate`
macht 52 KI-Aufrufe (Inhaltsart, ein Cache-Vorlauf, ein Aufruf je Feld; mit
AcademicCloud ohne Vorlauf 51) und dauert 12–22 s.

| Seite (Textlänge) | aus dem Cache | Kosten je Erschließung | je 1.000 | ohne Caching |
|---|---:|---:|---:|---:|
| LEIFIphysik „Kreisbewegung“ (6.100 Zeichen) | 66 % | 0,007 € | 7 € | 0,017 € |
| Klexikon „Saturn“ (12.900 Zeichen) | 79 % | 0,008 € | 8 € | 0,029 € |
| Wikipedia „Atomkern“ (66.600 Zeichen) | 93 % | 0,017 € | 17 € | 0,115 € |

**Kontrolle am 15.11.2026** (lokaler Agent gegen Staging, zwei
Erschließungen, Zähler aus `/metrics`): 95 KI-Aufrufe, 223.526 Eingabe-Tokens,
davon 106.878 aus dem Cache (48 %), 3.100 Ausgabe-Tokens, im Mittel 9,4 s je
`/generate`. Nach Listenpreis sind das rund 0,009 $ je Erschließung. Die Preise
sind gegenüber dem 16.10. unverändert.

- **Preisbasis:** OpenAI-Listenpreis „Standard, Short context“
  (developers.openai.com/api/docs/pricing, abgerufen 16.10.2026), je 1 Mio.
  Tokens: Eingabe 0,10 $, gecachte Eingabe 0,01 $, Cache-Schreiben 0,125 $,
  Ausgabe 0,50 $; umgerechnet mit 1 € = 1,1186 $ (EZB, 08.10.2026). Für
  Datenresidenz-Endpunkte verlangt OpenAI 10 % Aufschlag; welchen Weg die
  B-API nutzt, ist zu klären.
- **Input-Caching:** Jeder Feld-Aufruf schickt den ganzen Seitentext mit. Der
  Cache der B-API trifft aber nur dort, wo ein früherer Prompt endete. Deshalb
  steht der Text als eigene Nachricht vor der Feldanweisung, und ein kurzer
  Vorlauf-Aufruf, der genau nach dem Text endet, läuft vor den Feld-Aufrufen
  (`LLMService._prime_text_cache`, nur für `b-api-openai` und `openai`). Ohne
  Vorlauf kamen 0 % aus dem Cache, und jede Eingabe wurde als
  Cache-Schreibvorgang (0,125 $) berechnet; das ist die Spalte „ohne Caching“.
- **Die Kosten hängen weiter vor allem an der Textlänge**, aber deutlich
  schwächer: Die lange Wikipedia-Seite kostet jetzt etwa das 2,5-Fache der
  kurzen statt des 7-Fachen. Ausgabe-Tokens (inklusive Reasoning) machen
  unter 10 % aus. Dieselbe Seite kurz danach erneut erschlossen: gemessen
  0,002 € – das gilt ohne den Cache-Haltepunkt vom 15.11. (siehe unten).
- Gebucht wird auf den Schlüssel des Aufrufers (Variante A). Die Nutzungsdaten
  der B-API zeigen nach den ersten Produktivwochen die tatsächlichen Kosten.
- **Externe Dienste:** Photon-Geocoder und Textextraktion über die B-API
  (mit dem Schlüssel des Aufrufers; direkt aufgerufen frei und ohne
  Schlüssel; der Geocoder mit `auto` auch als Ausweg, wenn der Weg über die
  B-API scheitert), Repository (eigener Dienst).

**Stand des Prompt-Cachings (umgesetzt und gemessen am 15.11.2026,
`gpt-6-luna` über die B-API):**

- **Haltepunkt nach dem Text.** Der Seitentext endet mit einem ausdrücklichen
  Cache-Haltepunkt (`prompt_cache_options.mode: "explicit"`,
  `prompt_cache_breakpoint`). Der Vorlauf schreibt den Text einmal in den
  Cache, die Feld-Aufrufe lesen ihn nur noch. Vorher schrieb jeder Feld-Aufruf
  auch seine Feldanweisung in den Cache, zum 1,25-Fachen des Eingabepreises
  und ohne dass sie je gelesen wurde. Die B-API reicht beides unverändert
  durch, auch über den Router. Geschaltet über
  `METADATA_AGENT_LLM_CACHE_BREAKPOINT` (Default `auto`: nur Modelle `gpt-6…`;
  `gpt-4.1-mini`, `gpt-5-mini` und `gpt-5.4-mini` beantworten den Parameter
  mit `400`).
- **Vorlauf parallel.** Der Vorlauf läuft gleichzeitig mit der Erkennung der
  Inhaltsart statt danach. Bringt die Anfrage schon Metadaten mit, bleibt es
  bei der Reihenfolge von vorher, weil dann erst nach der Erkennung feststeht,
  ob mehr als ein Feld zu erschließen ist.
- **Metrik.** `metadata_agent_llm_tokens_total` zählt zusätzlich
  `kind="cache_write"`.
- **Gleichzeitigkeit unverändert.** Es bleibt bei 20 gleichzeitigen Aufrufen
  (`b-api-openai`, `b-api-router`, `openai`) und `max_workers` von 1 bis 20.
  Gemessen wurde mehr: 60 und 100 Aufrufe im selben Moment, alle `200`, kein
  `429`; 60 Worker brauchten im Median 4,6 s, 20 brauchen 5,2 s, 10 brauchen
  7,8 s (je sechs Läufe im Wechsel). Eine halbe Sekunde ist die dreifache Last
  auf einem geteilten Schlüssel nicht wert, bevor das im Betrieb erprobt ist.
  Einstellbar wie bisher: je Anfrage (`max_workers`), je Dienst
  (`METADATA_AGENT_DEFAULT_MAX_WORKERS`) und als Grenze des Prozesses
  (`METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS`).

Gemessen mit einem Text von 4.400 Zeichen (Inhaltsart Veranstaltung, 47
Felder, 48 KI-Aufrufe, 112.700 Eingabe-Tokens je Lauf):

| Stand | Dauer | in den Cache geschrieben | aus dem Cache gelesen | Kosten je Lauf |
|---|---:|---:|---:|---:|
| vorher: Vorlauf nach der Erkennung, ohne Haltepunkt | 9,2–9,5 s | 53.300 Tokens | 59.200 Tokens | 0,0083 $ |
| jetzt: Vorlauf parallel, mit Haltepunkt | 4,5–6,0 s, Median 5,2 s | 3.000 Tokens | 59.200 Tokens | 0,0070 $ |

- Der Cache trifft bei allen 47 Feld-Aufrufen (59.200 Tokens sind 47-mal der
  gemeinsame Anfang).
- Die 3.000 geschriebenen Tokens sind der Vorlauf und der Aufruf für die
  Inhaltsart, der ohne Haltepunkt weiter im Standardmodus schreibt.
- Nicht gecacht werden die Feldanweisungen (rund 53.000 Tokens je Lauf, jetzt
  zum einfachen Eingabepreis). Sie sind je Feld verschieden.
- **Preis des Haltepunkts:** Derselbe Text, kurz danach wortgleich erneut
  erschlossen, kostet mit Haltepunkt so viel wie beim ersten Mal (gemessen
  0,0065 $; ohne Haltepunkt 0,0018 $), weil die Feldanweisungen nicht mehr im
  Cache liegen. Beim ersten Lauf spart der Haltepunkt 17 % (0,0065 $ statt
  0,0078 $). Über die B-API beantwortet deren eigener Cache eine wortgleiche
  Wiederholung ohnehin; wer mit `openai` oft denselben Text erschließt, setzt
  `METADATA_AGENT_LLM_CACHE_BREAKPOINT=off`.
- Mehrere Felder je Aufruf lohnen nicht: längere Antworten, schlechtere
  Treffer bei großen Vokabularen, und der wiederholte Text kostet aus dem
  Cache nur ein Zehntel.

## 8. Ansprechperson und Wartung

- Ansprechperson T4: **[Name, Kontakt]**
- Updates (Abhängigkeiten, Sicherheitspatches, Modellwechsel) stößt
  **Team 4** an. Modelle wechseln ohne Code-Änderung über Env oder Route.
- Update-Vorschläge kommen auf GitHub von Dependabot (`.github/dependabot.yml`:
  `uv.lock`, Basis-Images, Actions). Auf GitLab ist keine Automatik
  eingerichtet (Renovate bräuchte einen eigenen Runner) – dort prüft **Team 4**
  regelmäßig. Das Alter des Chromium meldet die CI (`scripts/check_browser_age.py`,
  Job `run tests` und täglich die geplanten Prüfungen; Abbruch bei mehr als
  zwei Hauptversionen Rückstand und, mit `REQUIRE_BROWSER_AGE=1` wie in beiden
  CIs, auch wenn sich das Alter nicht bestimmen lässt). Dependabot wartet eine
  Woche auf neue Versionen. Es aktualisiert `uv.lock` nur für die Pakete, die es
  anhebt, erzeugt `requirements.txt` aber selbst neu – vor dem Merge auf dem
  Branch `uv lock` und `uv export --frozen --no-dev --format requirements-txt
  --no-emit-project > requirements.txt` ausführen (bis dahin schlägt
  `tests/test_requirements_in_step.py` fehl). NOTICE nennt Pakete ohne Version
  und braucht nur bei einem neuen Paket eine Zeile. Ein Python-Versionswechsel
  (3.13 → 3.14) kommt nicht von Dependabot, sondern bewusst in Dockerfile und
  beiden CIs (`CI_PYTHON_IMAGE`) zugleich.
