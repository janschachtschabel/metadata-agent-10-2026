# Übergabe an den Betrieb (Team 6)

Stand 3.0.1. Gegliedert nach der Checkliste „Übergabepaket“ aus den
Anforderungen von Team 6. Einzelheiten stehen in
[ENV-PARAMETER.md](ENV-PARAMETER.md) und [DEPLOYMENT.md](DEPLOYMENT.md);
dieses Dokument verweist dorthin, statt sie zu wiederholen. Felder in
**[eckigen Klammern]** muss T4 noch ausfüllen. Eine Kurzübersicht, welche
Anforderungen erfüllt sind, steht in
[docs/team6-anforderungen.md](docs/team6-anforderungen.md).

## 1. Repo, CI, Image

| | |
|---|---|
| Repo | API: <https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent>; Webkomponente: <https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent-webcomponent> (das gebaute Widget liegt im API-Repo unter `src/static/widget/`). Ob T6 Zugriff hat, ist noch zu bestätigen. |
| CI | `.gitlab-ci.yml` baut bei jedem Push auf `main`/`develop` ein Image mit dem Branch als Tag und bei jedem Git-Tag `v*` ein Image mit der Version (z.B. `v3.0.1`); andere Tags veröffentlichen nichts. Produktion nur mit Versions-Tags. |
| GitLab-CI/CD-Variablen | Die Docker-Jobs in `.gitlab-ci.yml` brauchen: `DIND_IMAGE`, `DIND_HOST`, `DIND_DRIVER`, `DIND_TLS_CERTDIR` (Docker-in-Docker), `DOCKER_REGISTRY`, `DOCKER_USERNAME`, `DOCKER_PASSWORD` (maskiert). Test- und Ruff-Jobs brauchen keine. Die Webkomponente braucht keine eigenen Variablen. |
| GitHub-Spiegel | `.github/workflows/docker-publish.yml`: Tests, Chart-Prüfung und Image-Bau bei Pushes auf `main`, Tags `v*` und Pull Requests auf `main`; nach DockerHub nur außerhalb von Pull Requests und mit Secret `DOCKERHUB_PASSWORD`. Dependabot (`.github/dependabot.yml`) schlägt Updates für `uv.lock` (damit Playwright/Chromium), Basis-Images und Actions vor |
| Helm-Chart | [`helm/metadata-agent/`](helm/metadata-agent/README.md); die CI prüft ihn mit `helm lint` und die Alarmregeln mit `promtool` (Jobs `helm lint`, `alert rules`) |
| Prüfungen vor einem Image | Tests mit Coverage-Untergrenze, mypy, `pip-audit` (Job `pip audit`); das gebaute Image startet schreibgeschützt ohne Schlüssel und muss `/health` beantworten (`scripts/smoke-image.sh`), Trivy bricht bei kritischen, behebbaren Lücken ab. In Merge Requests baut und prüft der Job `check docker image in merge requests`, ohne zu veröffentlichen |
| Image | Multi-Stage auf `python:3.13-slim` (per Digest gepinnt, Aktualisierung im Kopf des Dockerfiles), läuft als Nutzer `appuser` (UID 1000), Code gehört root, `.dockerignore` vorhanden, HEALTHCHECK auf `/health`. Enthält Chromium für Screenshots (~400 MB), daher größer als ein reiner API-Dienst. Läuft mit schreibgeschütztem Root-Dateisystem, wenn `/tmp` und `/home/appuser` beschreibbar sind; der CI-Smoke-Test startet das Image so und lässt darin Chromium starten (lokal nicht geprüft, weil das Bauen Downloads braucht). |
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
| `Authorization: Bearer <key>` | ein | Schlüssel des Aufrufers, den der B-API-Proxy weiterreicht (`forwardApiKey`). Er wird für die KI-Aufrufe unverändert weitergegeben. |
| `x-client-trace-id`, `x-request-id`, `x-b3-*`, `b3` | ein → weiter | Tracing. Wird unverändert an B-API, Textextraktion und Repository weitergegeben, nie an Fremddienste. |
| `X-Request-ID` | aus | Anfrage-ID (gleich `x-client-trace-id`, wenn vorhanden). Steht auch in Fehlerantworten als `error.request_id` und in der Logzeile zum Fehler. Ohne diesen Header können Nutzer einen Fehler nicht eindeutig melden. |

### Content-Types und Antwortzeiten

- **Content-Types:** Die API spricht nur `application/json`; Bilder kommen
  base64-kodiert im JSON. Das Widget liefert statische Dateien aus: JS, CSS,
  `woff2`-Schriften, HTML-Demoseiten und JSON-Übersetzungen. Es gibt keine
  Streams und keine Datei-Uploads per multipart.
- **Antwortzeiten:**
  - `/generate` mit etwa 50 Feldern: mit `gpt-6-luna` 12–22 s (gemessen
    16.10.2026), mit AcademicCloud bis 90 s (Warteschlange am Gateway).
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
- **Statuscodes:** `4xx` = die Anfrage ist falsch (fehlender Parameter, Node
  außerhalb des Bearbeitungsfensters `403`, Node, Kontext oder Schema unbekannt
  `404`, Body zu groß `413`, gültiges JSON mit falschem Inhalt `422`, auch
  `SCHEMA_UNAVAILABLE` beim Schreiben); `502` = ein abhängiger Dienst (KI,
  Textextraktion, Repository) hat versagt; `503` = Repository-Zugang nicht
  konfiguriert (`/upload`, `/nodes`, `/workflow`) bzw. `/ready` nicht bereit;
  `504` = Zeitlimit. Jeder Fehler hat `detail`, `error.code` und
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
  Routing `router`). **Kein** `LLM_ROUTE_MANAGE`.
- **Modell-Routing** (`METADATA_AGENT_LLM_PROVIDER=b-api-router`):
  - Der Default `openai/gpt-6-luna` braucht keine Route.
  - Die Route legt ein Administrator **in der B-API** an; in der Env steht nur
    ihr Name. Muster in
    [ENV-PARAMETER.md](ENV-PARAMETER.md#wo-routen-definiert-werden).
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
| OPS-11 Annotation `checksum/secret` | entfernt statt gesalzen | Ein Salz, das sich nicht aus dem Secret ableiten lässt, kann `helm template` nicht stabil erzeugen; ein zufälliges würde jeden Upgrade die Pods neu starten lassen; mit `existingSecret` änderte sich die Annotation ohnehin nie. Nach einer Änderung des Secrets: `kubectl rollout restart deployment/<name>` (DEPLOYMENT.md). |
| OPS-09 `$DIND_IMAGE` ohne Digest | Projektvariable | Das Image kommt aus den GitLab-Gruppenvariablen, nicht aus dem Repo; dort per Digest setzen. |
| S-08 Testseiten unter `/widget/examples` | bleiben | `test.html`, `uri-test.html`, `dual-instance-test.html` sind dokumentierte Werkzeuge für Integratoren (README, `/widget/info`) und rufen nur die öffentliche API auf. Das öffentliche `/health` nennt keine Konfiguration mehr. |

## 4. Monitoring und Logging

- **`/metrics`** (Prometheus) auf dem internen Port:
  - Prozess- und Python-Laufzeitmetriken;
  - `metadata_agent_http_requests_total` und `…_duration_seconds` (nach
    Methode, Routen-Vorlage, Status);
  - `metadata_agent_llm_requests_total` und `…_duration_seconds` (nach
    Provider und Ergebnis: `ok` oder Fehlercode); der Zähler zusätzlich nach
    `auth_source` (`forwarded`, `static`, `openai`, `none`) und `config`
    (`default`, oder `override`, wenn die Anfrage Provider/Modell gewählt hat);
  - `metadata_agent_generations_total` (nach `success`, `partial`, `failed`);
  - `metadata_agent_upstream_requests_total` (gesendet),
    `…_responses_total` (beantwortet, nach HTTP-Status) und
    `…_response_duration_seconds` je abhängigem Dienst (`llm`,
    `text-extraction`, `repository`, `geocoder`); gesendet minus beantwortet
    sind Aufrufe ohne Antwort;
  - `metadata_agent_llm_tokens_total` (nach Provider und `prompt`, `cached`,
    `completion`), auch für den Cache-Vorlauf.
- **Logs** gehen nach stdout. `METADATA_AGENT_LOG_FORMAT=json` schreibt ein
  Objekt je Zeile mit `time`, `level`, `logger`, `message` und `trace_id`.
  `METADATA_AGENT_LOG_LEVEL` steuert die Schwelle.
- **Alarmregeln:** zwölf Regeln in
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
  | `MetadataAgentGenerationsFailing` | warning | über 50 % `failed` in 30 min (ab 3 Läufen) |
  | `MetadataAgentUpstreamUnanswered` | warning | mehr als 3 Aufrufen ohne Antwort in 10 min (je Dienst, ohne `llm`), 5 min lang – laufende Aufrufe zählen anfangs als unbeantwortet |
  | `MetadataAgentUpstreamServerErrors` | warning | mehr als 5 Antworten mit 5xx in 10 min (je Dienst) |
  | `MetadataAgentGenerateSlow` | warning | p95 von `/generate` über 270 s (Zeitlimit 300 s), 15 min lang |
  | `MetadataAgentServerErrors` | warning | mehr als 5 eigenen Antworten mit 5xx in 10 min (je Route, ohne `/health`, `/ready`) |
  | `MetadataAgentInternalError` | critical | einem `500` (`INTERNAL_ERROR`, ein Fehler im Agent selbst) in 10 min, auch dem ersten nach dem Start eines Pods |

  `promtool test rules helm/metadata-agent/tests/alerts.test.yaml` prüft jede
  Regel mit Testdaten (auslösend und nicht auslösend). Pod-Ausfälle melden die
  Cluster-Regeln (`TargetDown`, `KubePodCrashLooping`). Wer den Alarm bekommt,
  legt T6 im Alertmanager fest.

## 5. Zustand, Skalierung, Architektur

![Architektur-Map](docs/architektur/metadata-agent-architektur.png)

Als Bild für Dokumente: [PNG](docs/architektur/metadata-agent-architektur.png)
oder [SVG](docs/architektur/metadata-agent-architektur.svg); interaktiv:
[HTML](docs/architektur/metadata-agent-architektur.html).

Ablauf einer Anfrage über die B-API (Schlüssel, Tracing-Header, Timeouts):
[PNG](docs/architektur/metadata-agent-ablauf.png),
[SVG](docs/architektur/metadata-agent-ablauf.svg),
[HTML](docs/architektur/metadata-agent-ablauf.html). Kurzfassung der Übergabe
als Steckbrief: [docs/steckbrief.md](docs/steckbrief.md).

- **Zustandslos:** keine Datenbank, keine Sessions, keine Dateien zur Laufzeit.
  Jeder Pod kann jede Anfrage beantworten, auch mehrere Pods nebeneinander.
- **Grenzen je Pod:** gleichzeitige KI-Aufrufe (Default 20) und
  Screenshot-Plätze (2) gelten pro Prozess. Bei N Pods addieren sie sich.
  Das gemeinsame GWDG-Limit (~10 Aufrufe/min, höchstens 2 gleichzeitig) gilt
  dagegen für den Schlüssel und damit für alle Pods zusammen, deshalb dort
  nicht über Replicas skalieren. Im Helm-Chart teilt
  `sharedLlmLimit.enabled=true` das Limit durch `replicaCount`
  (DEPLOYMENT.md, Durchsatzgrenzen).
- **Ein Worker je Container:** Der interne Port und die Metriken setzen das
  voraus. Skaliert wird über Replicas.

```
Browser ─ edu-sharing ─ B-API-Proxy ─┐
                                      ▼
                         Metadata Agent (FastAPI, 1 Prozess)
            ┌───────────────┬──────────┴────────┬────────────────┬──────────────┐
            ▼               ▼                   ▼                ▼              ▼
   B-API LLM (OpenAI/   Textextraktion    edu-sharing      Photon-Geocoder   Chromium im
   AcademicCloud/Router)  (/from-url)      Repository       (photon.komoot.io) Container (Screenshots)
```

## 6. Ressourcen, Daten, Konfiguration

- **Ressourcen:** Request 500m CPU / 512Mi, Limit 2 CPU / 2Gi. Das ist
  geschätzt und **nicht unter Last gemessen**; empfohlen ist ein Lauf mit
  `docker stats` während eines `/generate` mit Screenshot. Speicherspitzen
  entstehen durch Chromium.
- **Datenbanken, Redis, Volumes:** keine. Im Helm-Chart gibt es nichts
  abzuschalten.
- **Konfiguration:** alles per Env, mit Defaults in
  [ENV-PARAMETER.md](ENV-PARAMETER.md). Secrets: `B_API_KEY` (im Modus
  `forward` nicht nötig), `OPENAI_API_KEY` (nur Variante B),
  `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`. Referenz: `Dockerfile`,
  `docker-compose.yml`. `METADATA_AGENT_REQUEST_TIMEOUT` (Timeout je
  KI-Aufruf, 90 s) und `METADATA_AGENT_GENERATE_TIMEOUT` (ganzes `/generate`,
  300 s, samt Abruf der Quelle) sind in Code, Compose, Chart und Template
  einheitlich, ein Puffer unter dem Ingress-Timeout von 320 s. Geocoding:
  höchstens 10 Abfragen je Anfrage.

Helm-Chart: [`helm/metadata-agent/`](helm/metadata-agent/README.md) mit
Deployment (Probes auf dem internen Port, 320 s Grace-Period für laufende
Erschließungen), Service, PodDisruptionBudget, NetworkPolicy, Secret,
`ServiceMonitor` und `PrometheusRule`. Ein Ingress (Annotationen `proxy-read-timeout 320`,
`proxy-body-size 20m`) ist abschaltbar vorhanden, gehört aber nicht vor einen
Dienst hinter der B-API: Der Proxy ruft den Service im Cluster auf; bei
aktivem Ingress im Modus `auto` warnt `helm install`.
Die Werte in `values.yaml` sind per Test mit dem Code abgeglichen. Geprüft mit
`helm lint --strict` und kubeconform gegen Kubernetes 1.33 (16.10.2026; seit
18.10. läuft kubeconform in beiden CI), nicht in einem echten Cluster
installiert – die Prüfliste für die erste Installation auf Staging steht in
DEPLOYMENT.md, ebenso der Abschnitt Rollback. `/debug/*` ist per
`METADATA_AGENT_DEBUG_ENDPOINTS=false` aus; der interne Port ist nicht im
Ingress.

**Schreibgeschütztes Root-Dateisystem** (`securityContext.readOnlyRootFilesystem`):
`/tmp` und `/home/appuser` sind `emptyDir` (Chromium). **TLS:** Der Chart setzt
`FORWARDED_ALLOW_IPS` aus `forwardedAllowIps` (Standard `"*"`, vertretbar nur
mit der NetworkPolicy), damit uvicorn `X-Forwarded-Proto` übernimmt und
`/widget/info` `https://` liefert (DEPLOYMENT.md, „Hinter TLS“).

**NetworkPolicy (`networkPolicy.*`, Standard an):** Port 8000 nur aus den
Namespaces in `ingressNamespaces` (Standard `b-api`), Port 9000 nur aus
`prometheusNamespaces` (Standard `monitoring`). Ausgehend: DNS nur zum
Cluster-DNS (`networkPolicy.dns`), die Namespaces in `egressNamespaces`
(Standard `b-api`) nur auf `egressPorts` (Ports der Ziel-Pods, Standard 80,
443, 8000, 8080) und das öffentliche Internet (IPv4) ohne private Netze
(`egressExceptCidrs`), damit Vorschau- und Screenshot-URLs keine
Cluster-Adressen erreichen. Grenzen (IPv6, öffentliche Namen mit Ziel im
selben Cluster per LoadBalancer/DNAT, NodeLocal DNSCache): DEPLOYMENT.md,
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
  0,002 €.
- Gebucht wird auf den Schlüssel des Aufrufers (Variante A). Die Nutzungsdaten
  der B-API zeigen nach den ersten Produktivwochen die tatsächlichen Kosten.
- **Externe Dienste:** Photon-Geocoder (frei, ohne Schlüssel),
  Textextraktion und Repository (eigene Dienste).

## 8. Ansprechperson und Wartung

- Ansprechperson T4: **[Name, Kontakt]**
- Updates (Abhängigkeiten, Sicherheitspatches, Modellwechsel) stößt
  **Team 4** an. Modelle wechseln ohne Code-Änderung über Env oder Route.
- Update-Vorschläge kommen auf GitHub von Dependabot (`.github/dependabot.yml`:
  `uv.lock`, Basis-Images, Actions). Auf GitLab ist keine Automatik
  eingerichtet (Renovate bräuchte einen eigenen Runner) – dort prüft **Team 4**
  regelmäßig. Das Alter des Chromium meldet die CI (`scripts/check_browser_age.py`,
  Job `run tests`; Abbruch bei mehr als zwei Hauptversionen Rückstand). Dependabot wartet eine
  Woche auf neue Versionen. Es aktualisiert `uv.lock` nur für die Pakete, die es
  anhebt, erzeugt `requirements.txt` aber selbst neu – vor dem Merge auf dem
  Branch `uv lock` und `uv export --frozen --no-dev --format requirements-txt
  --no-emit-project > requirements.txt` ausführen (bis dahin schlägt
  `tests/test_requirements_in_step.py` fehl). NOTICE nennt Pakete ohne Version
  und braucht nur bei einem neuen Paket eine Zeile. Ein Python-Versionswechsel
  (3.13 → 3.14) kommt nicht von Dependabot, sondern bewusst in Dockerfile und
  beiden CIs (`CI_PYTHON_IMAGE`) zugleich.
