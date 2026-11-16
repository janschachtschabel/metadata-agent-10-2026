# Umgebungsvariablen

[English](ENV-PARAMETER.md) · **Deutsch**

Die vollständige Referenz aller Umgebungsvariablen, die die Metadata Agent API
liest, für Version 4.0.2; die Änderungen seit 3.0.1 stehen in
[CHANGELOG.md](CHANGELOG.md). Quelle ist `src/config.py`.
`tests/test_env_parameters.py` gleicht dieses Dokument mit dem Settings-Modell
ab: Es fehlt keine Variable, und es steht keine hier, die der Agent nicht
liest.

## Inhalt

1. [Wie die Konfiguration funktioniert](#wie-die-konfiguration-funktioniert)
2. [Schlüssel und Zugangsdaten](#schlüssel-und-zugangsdaten)
3. [LLM-Provider und Modell](#llm-provider-und-modell)
4. [B-API: Authentifizierung](#b-api-authentifizierung)
5. [B-API: Routing](#b-api-routing)
6. [Textextraktion und Geocoder](#textextraktion-und-geocoder)
7. [Repository](#repository)
8. [Durchsatz](#durchsatz)
9. [Grenzen und Zeitlimits](#grenzen-und-zeitlimits)
10. [Screenshots](#screenshots)
11. [Logging und Tracing](#logging-und-tracing)
12. [Ports, CORS und Debug-Endpunkte](#ports-cors-und-debug-endpunkte)
13. [Weitere Einstellungen](#weitere-einstellungen)
14. [Fertige Konfigurationen](#fertige-konfigurationen)
15. [Helm-Chart](#helm-chart)
16. [Weitere Dokumente](#weitere-dokumente)

---

## Wie die Konfiguration funktioniert

### Namen und Quellen

Jede Einstellung ist eine Umgebungsvariable. Alle Namen tragen das Präfix
`METADATA_AGENT_`, außer den vier Schlüsseln und Zugangsdaten: `B_API_KEY`,
`OPENAI_API_KEY`, `WLO_GUEST_USERNAME` und `WLO_GUEST_PASSWORD`. Der Agent
liest diese vier auch mit Präfix; sind beide Schreibweisen gesetzt, gilt die
ohne Präfix. Üblich ist die Schreibweise ohne Präfix: So stehen die Namen in
`.env.template`, in Docker Compose und im Helm-Chart.

Der Agent nimmt einen Wert von der ersten Stelle, die einen hat:

1. die Umgebung des Prozesses (Docker `-e`, Compose `environment`,
   Kubernetes `env`),
2. eine Datei `.env` im Arbeitsverzeichnis (UTF-8; Kopie von `.env.template`),
3. der Default im Code.

- Das Docker-Image enthält keine `.env`. Im Container zählt nur die Umgebung.
  `docker-compose.yml` reicht eine `.env` neben der Datei in den Container.
- Die Einstellungen werden einmal beim Start gelesen. Eine Änderung braucht
  einen Neustart.
- Unbekannte Variablen werden ignoriert. Eine alte `.env` bricht nichts, ein
  Tippfehler im Namen fällt aber auch nicht auf. Was gilt, zeigt das Startlog.

### Leere Werte

Eine Variable, die gesetzt, aber leer ist, gilt als nicht gesetzt: Es gilt der
Default. Das trifft auf `VAR=` in einer `.env` zu und auf `""` in den
Chart-Werten. Eine leere Variable in der Umgebung verdeckt auch einen Wert aus
der `.env`.

Drei Einstellungen sind die Ausnahme. Bei ihnen ist „leer“ selbst die
Einstellung:

| Variable | Leer bedeutet |
|---|---|
| `METADATA_AGENT_LLM_VERBOSITY` | Der Parameter wird nicht gesendet |
| `METADATA_AGENT_LLM_REASONING_EFFORT` | Der Parameter wird nicht gesendet |
| `METADATA_AGENT_CORS_ORIGINS` | Kein Origin ist erlaubt |

### Wertformate

- **Auswahlwerte** (Provider, Auth-Modus, Log-Level usw.) beachten weder
  Groß- und Kleinschreibung noch Leerzeichen am Rand.
- **Schalter** nehmen `true` oder `false` (auch `1`/`0`, `yes`/`no`,
  `on`/`off`).
- **URLs** werden in eine Form gebracht: Leerzeichen am Rand und ein
  Schrägstrich am Ende entfallen, Schema und Host stehen in Kleinbuchstaben,
  und der Standard-Port des Schemas (`:80`, `:443`) entfällt. Das gilt für
  `METADATA_AGENT_B_API_BASE_URL`, die drei Variablen `…_BASE` der B-API,
  `METADATA_AGENT_OPENAI_API_BASE`, `METADATA_AGENT_REPOSITORY_URL` und
  `METADATA_AGENT_TEXT_EXTRACTION_API_URL`.
- **Ungültige Werte** beenden den Agenten beim Start mit einem
  Validierungsfehler: ein unbekannter Auswahlwert, eine Zahl außerhalb ihres
  Bereichs, Text an der Stelle einer Zahl und ein
  `METADATA_AGENT_ALLOWED_LLM_MODELS`, das kein Modell nennt. Den Bereich jeder
  Einstellung nennen die Tabellen unten. Eine Ausnahme:
  `METADATA_AGENT_DEFAULT_MAX_WORKERS` wird auf seinen Bereich begrenzt.

### Abgeleitete Werte

Einige Werte werden aus anderen berechnet. Zu setzen sind sie nur, um von der
Regel abzuweichen.

| Wert | Regel |
|---|---|
| LLM-Endpunkte der B-API | `<METADATA_AGENT_B_API_BASE_URL>/api/v1/llm/openai`, `…/academiccloud`, `…/router`, sofern die zugehörige Variable `…_BASE` nicht gesetzt ist. An jeden wird `/chat/completions` angehängt |
| Adresse der Textextraktion | Gewählt nach `METADATA_AGENT_B_API_SERVICES`, sofern `METADATA_AGENT_TEXT_EXTRACTION_API_URL` nicht gesetzt ist; siehe [Textextraktion und Geocoder](#textextraktion-und-geocoder) |
| Adresse des Geocoders | Gewählt nach `METADATA_AGENT_B_API_SERVICES`; keine eigene Variable |
| Temperatur für den Provider `openai` | `METADATA_AGENT_OPENAI_TEMPERATURE`, sonst `METADATA_AGENT_LLM_TEMPERATURE` |
| Durchsatzgrenzen | Default des Providers, sofern nichts gesetzt ist; siehe [Durchsatz](#durchsatz) |
| Wirksame Worker | Der kleinere Wert aus `METADATA_AGENT_DEFAULT_MAX_WORKERS` und der Grenze gleichzeitiger Anfragen |

### Startlog

Beim Start gibt der Agent aus, was gilt, auch die abgeleiteten Werte. Er gibt
nie einen Schlüssel aus, nur ob `B_API_KEY` gesetzt ist (`gesetzt` oder
`nicht gesetzt`). Benutzername und Passwort in einer URL-Einstellung
(`https://user:password@host`) fehlen in den Adresszeilen.

```text
Starting Metadata Agent API v4.0.2
LLM Provider: b-api-academiccloud
LLM Model: deepseek-v4-flash
LLM API Base: https://b-api.staging.openeduhub.net/api/v1/llm/academiccloud
B-API Base URL: https://b-api.staging.openeduhub.net
B-API Auth Mode: auto (B_API_KEY gesetzt)
Text Extraction URL: https://b-api.staging.openeduhub.net/api/v1/proxy/text-extraction
Geocoder URL: https://b-api.staging.openeduhub.net/api/v1/proxy/photon_geocoding/api
Repository URL: https://repository.staging.openeduhub.net/edu-sharing/rest
Default Workers: 20 → 2 (limit of b-api-academiccloud)
LLM Throughput: max 2 in flight, max 2 req/s
Internal port: 9000 (/health, /ready, /metrics)
```

Ohne Ratenbegrenzung endet die Durchsatzzeile mit `no rate cap`. Die letzte
Zeile erscheint nur mit `METADATA_AGENT_INTERNAL_PORT`.

### Warnungen beim Start

Einstellungen, die nicht zusammenpassen, meldet der Agent beim Start, zwischen
den Adresszeilen und der Worker-Zeile. Die Zeilen beginnen mit `⚠️` und sind
auf Deutsch. `GET /debug/dependencies` gibt dieselbe Liste als
`config_warnings` zurück.

| Situation | Was die Warnung sagt |
|---|---|
| B-API-Provider, Modus `static`, kein `B_API_KEY` | Jeder LLM-Aufruf scheitert |
| B-API-Provider, Modus `forward`, `B_API_KEY` gesetzt | Der Schlüssel wird nie verwendet |
| B-API-Provider, Modus `auto`, kein `B_API_KEY` | Es gelingen nur Aufrufe über den B-API-Proxy mit durchgereichtem Schlüssel |
| Provider `openai`, kein `OPENAI_API_KEY` | Jeder LLM-Aufruf scheitert, und `/ready` antwortet mit `503` |
| Provider `openai`, `METADATA_AGENT_B_API_SERVICES=proxy`, kein `B_API_KEY` | Modus `static`: Textextraktion und Geocoder über die B-API scheitern. Modus `auto`: Sie gelingen nur bei Aufrufen mit durchgereichtem Schlüssel |
| `METADATA_AGENT_DEBUG_ENDPOINTS=true` | `/debug/auth` und `/debug/dependencies` antworten jedem, der den Agenten erreicht; nach dem Einrichten oder der Fehlersuche wieder ausschalten |
| Provider `b-api-router`, ein Routenname (ohne `/`) als Modell, `METADATA_AGENT_LLM_REQUEST_STYLE=auto` | Der Name verrät nicht, welche Parameter die Modelle verstehen; gesendet wird der klassische Request-Body |
| `WLO_GUEST_USERNAME` oder `WLO_GUEST_PASSWORD` fehlt | `/upload` und `/nodes` antworten mit `503`; `/ready` ebenfalls, außer mit `METADATA_AGENT_REQUIRE_REPOSITORY=false` |
| `METADATA_AGENT_UPLOAD_TIMEOUT` plus 15 s oder `METADATA_AGENT_GENERATE_TIMEOUT` plus 10 s liegt über 320 s (das Limit selbst über 305 oder 310). Ist `METADATA_AGENT_GRACE_PERIOD_SECONDS` gesetzt, tritt dieser Wert an die Stelle der 320 s | Mit der Zeit, die danach noch nötig ist (Aufräumen, preStop), liegt das Limit über den 320 s von Ingress und Grace-Period; der Proxy bricht vorher ab. Gemessen an `METADATA_AGENT_GRACE_PERIOD_SECONDS` nennt die Warnung diesen Wert: Die Anfrage wird beim Beenden des Pods abgebrochen |
| B-API-Provider, dessen LLM-Adresse nicht unter `METADATA_AGENT_B_API_BASE_URL` liegt, und `METADATA_AGENT_B_API_SERVICES` ist nicht `direct` | `auto`: Textextraktion und Geocoder werden direkt aufgerufen, nicht über die B-API. `proxy`: Sie gehen trotzdem an `METADATA_AGENT_B_API_BASE_URL`, mit dem Schlüssel der LLM-Aufrufe |
| Es lässt sich keine Adresse der Textextraktion ableiten | Erschließen aus einer URL scheitert, bis `METADATA_AGENT_TEXT_EXTRACTION_API_URL` gesetzt ist |
| Die Adressen von B-API, Repository und Textextraktion zeigen auf verschiedene Umgebungen (Staging und Produktion von OpenEduHub) | Nennt, welche Adresse zu welcher Umgebung gehört |

### Bereitschaft

`/ready` antwortet mit `503`, solange die Konfiguration gar nicht arbeiten
kann. Geprüft wird nur die Konfiguration; ein externer Dienst wird dafür nicht
aufgerufen.

- Es konnte kein Schema-Kontext geladen werden.
- B-API-Provider im Modus `static` ohne `B_API_KEY`.
- Provider `openai` ohne `OPENAI_API_KEY`.
- `WLO_GUEST_USERNAME` oder `WLO_GUEST_PASSWORD` fehlt, und
  `METADATA_AGENT_REQUIRE_REPOSITORY` ist `true`.

Die Gründe stehen im Log und in der Antwort von `/ready` auf dem internen
Port; der öffentliche Port nennt keine. Ein fehlender `B_API_KEY` im Modus
`auto` ist kein Grund: Hinter dem B-API-Proxy bringt jeder Aufruf seinen
Schlüssel mit.

---

## Schlüssel und Zugangsdaten

Diese vier Variablen tragen kein Präfix. Sie bleiben leer, bis der echte Wert
vorliegt: `/ready` prüft nur, ob ein Wert gesetzt ist. Mit einem
Platzhalter meldet sich der Agent bereit und scheitert beim ersten Aufruf.

| Variable | Default | Bedeutung und Einsatz |
|---|---|---|
| `B_API_KEY` | leer | Der eigene B-API-Schlüssel des Agenten, gesendet als `X-API-KEY`. Genutzt mit den Providern `b-api-openai`, `b-api-academiccloud` und `b-api-router` für die LLM-Aufrufe und für Textextraktion und Geocoder über die B-API. Pflicht im Modus `static`, Rückfall im Modus `auto`, ungenutzt im Modus `forward`; siehe [B-API: Authentifizierung](#b-api-authentifizierung) |
| `OPENAI_API_KEY` | leer | Schlüssel für den Provider `openai`, gesendet als `Authorization: Bearer` an `METADATA_AGENT_OPENAI_API_BASE` |
| `WLO_GUEST_USERNAME` | leer | Benutzer des Repository-Kontos. Nötig für `/upload` und `/nodes`; auch das Lesen von Nodes nutzt ihn und weicht ohne ihn auf anonymen Zugriff aus |
| `WLO_GUEST_PASSWORD` | leer | Passwort des Repository-Kontos |

Ein Schlüssel, mit dem Textextraktion und Geocoder über die B-API aufgerufen
werden, braucht die Rechte für die Proxy-Dienste `text-extraction` und
`photon_geocoding`. Das gilt für `B_API_KEY` und für jeden durchgereichten
Schlüssel.

---

## LLM-Provider und Modell

### Provider

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | `b-api-openai`, `b-api-academiccloud`, `b-api-router`, `openai` | Welchen LLM-Zugang der Agent nutzt; siehe die Tabelle darunter |
| `METADATA_AGENT_ALLOWED_LLM_MODELS` | leer | Modellnamen, durch Komma getrennt | Modelle, die eine Anfrage in `llm_model` nennen darf. Leer: jedes Modell. Ein Wert, der kein Modell nennt (nur Kommas und Leerzeichen), beendet den Start. Sinnvoll überall, wo Aufrufe mit dem eigenen Schlüssel des Agenten laufen |

| Provider | Endpunkt | Modell-Variable | Schlüssel |
|---|---|---|---|
| `b-api-openai` | OpenAI-Modelle über die B-API | `METADATA_AGENT_B_API_OPENAI_MODEL` | B-API-Schlüssel |
| `b-api-academiccloud` | Modelle der AcademicCloud (GWDG) über die B-API | `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | B-API-Schlüssel |
| `b-api-router` | Eine Route der B-API oder ein Modell als `provider/modell`; siehe [B-API: Routing](#b-api-routing) | `METADATA_AGENT_B_API_ROUTER_MODEL` | B-API-Schlüssel |
| `openai` | OpenAI oder ein beliebiger OpenAI-kompatibler Endpunkt, ohne B-API | `METADATA_AGENT_OPENAI_MODEL` | `OPENAI_API_KEY` |

Eine Anfrage an `/generate`, `/detect-content-type` oder `/extract-field` darf
ein eigenes `llm_provider` und `llm_model` nennen.

- `llm_provider: "openai"` lehnt der Agent mit `422` ab, außer er läuft selbst
  mit dem Provider `openai`.
- Ein Modell außerhalb von `METADATA_AGENT_ALLOWED_LLM_MODELS` lehnt er mit
  `422` ab. Das konfigurierte Default-Modell des Providers ist immer erlaubt.
- Ein so gewähltes Modell läuft mit dem Schlüssel des Agenten, wo kein
  Schlüssel des Aufrufers durchgereicht wird (Modus `static`, Modus `auto`
  ohne Header).

### Endpunkte und Modelle

| Variable | Default | Bedeutung und Einsatz |
|---|---|---|
| `METADATA_AGENT_B_API_BASE_URL` | `https://b-api.staging.openeduhub.net` | Adresse der B-API. Daraus werden die LLM-Endpunkte und die Pfade für Textextraktion und Geocoder abgeleitet. Produktion: `https://b-api.prod.openeduhub.net` |
| `METADATA_AGENT_B_API_OPENAI_BASE` | leer (abgeleitet: `…/api/v1/llm/openai`) | Nur setzen, wenn der Pfad vom Schema abweicht |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-6-luna` | Modell für `b-api-openai` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | leer (abgeleitet: `…/api/v1/llm/academiccloud`) | Nur setzen, wenn der Pfad vom Schema abweicht |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-v4-flash` | Modell für `b-api-academiccloud` |
| `METADATA_AGENT_B_API_ROUTER_BASE` | leer (abgeleitet: `…/api/v1/llm/router`) | Nur setzen, wenn der Pfad vom Schema abweicht |
| `METADATA_AGENT_B_API_ROUTER_MODEL` | `openai/gpt-6-luna` | Routenname oder `provider/modell` für `b-api-router` |
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` | Endpunkt für den Provider `openai`. Es funktioniert alles, was `/chat/completions` bedient und einen Bearer-Token annimmt: ein Azure-Deployment, ein selbst gehostetes vLLM, ein Gateway |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` | Modell für `openai` |

Zeigt eine der drei Variablen `…_BASE` auf eine Adresse außerhalb von
`METADATA_AGENT_B_API_BASE_URL`, ruft der Agent Textextraktion und Geocoder im
Modus `auto` nicht mehr über die B-API auf. So erreicht der Schlüssel nie eine
zweite B-API. Der Agent warnt beim Start.

### Anfrageparameter

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_LLM_TEMPERATURE` | `0.3` | Zahl von `0` bis `2` | Temperatur für alle Provider. Wird nur mit dem klassischen Request-Body gesendet; Reasoning-Modelle bekommen keine |
| `METADATA_AGENT_OPENAI_TEMPERATURE` | leer (folgt `METADATA_AGENT_LLM_TEMPERATURE`) | Zahl von `0` bis `2` | Nur wenn der Provider `openai` eine andere Temperatur nutzen soll |
| `METADATA_AGENT_LLM_MAX_TOKENS` | `2000` | Ganze Zahl, mindestens `1` | Token-Grenze einer Antwort. An Reasoning-Modelle als `max_completion_tokens` gesendet, an alle anderen als `max_tokens` |
| `METADATA_AGENT_LLM_VERBOSITY` | `low` | `low`, `medium`, `high`; leer: nicht senden | `verbosity`, nur an Reasoning-Modelle gesendet. Jeder andere Wert beendet den Start |
| `METADATA_AGENT_LLM_REASONING_EFFORT` | `low` | `none`, `low`, `medium`, `high`; leer: nicht senden | `reasoning_effort`, nur an Reasoning-Modelle gesendet. Jeder andere Wert beendet den Start, auch `minimal`, das die API ablehnt. `none` war im Test etwa 40 % schneller, verlor aber Veranstaltungsdaten |
| `METADATA_AGENT_B_API_IGNORE_CACHE` | `false` | `true`, `false` | `true` hängt `?ignore-caching=true` an jeden LLM-Aufruf an die B-API. Die B-API beantwortet wortgleiche Anfragen aus ihrem Cache, ohne das zu kennzeichnen; mit `true` ist jeder Aufruf neu und kostet jedes Mal |
| `METADATA_AGENT_LLM_CACHE_BREAKPOINT` | `auto` | `auto`, `on`, `off` | Ausdrücklicher Haltepunkt des OpenAI-Prompt-Caches nach dem Seitentext (`prompt_cache_options`, `prompt_cache_breakpoint`), für `b-api-openai`, `openai` und die Modelle `openai/…` von `b-api-router`. Mit ihm lesen die Feld-Aufrufe den Text nur noch aus dem Cache; ohne ihn schreibt jeder auch seine Feldanweisung in den Cache, zum 1,25-Fachen des Eingabepreises und ohne dass ein späterer Aufruf sie liest. `auto`: nur für Modelle `gpt-6…`; andere Modelle beantworten den Parameter mit `400` (gemessen 2026-11-15: `gpt-4.1-mini`, `gpt-5-mini`, `gpt-5.4-mini`). `on`: für jedes Modell dieser Provider, für eine Modellfamilie, die die Regel noch nicht kennt. `off`: nie, der bisherige Request-Body. Der Preis des Haltepunkts: Derselbe Text, innerhalb der Lebensdauer des Caches erneut erschlossen, kostet so viel wie beim ersten Mal (gemessen 0,0065 $ statt 0,0018 $), weil die Feldanweisungen nicht mehr im Cache liegen. Über die B-API beantwortet ohnehin deren eigener Cache eine wortgleiche Wiederholung; für den Provider `openai` mit vielen wiederholten Erschließungen desselben Texts ist `off` günstiger |

**Reasoning-Modelle** erkennt der Agent am Modellnamen: Er beginnt mit `gpt-5`,
`gpt-6`, `o1`, `o3` oder `o4`, auch hinter einem Provider-Präfix wie `openai/`.
Sie bekommen `max_completion_tokens`, `verbosity` und `reasoning_effort` und
keine Temperatur. Alle anderen Modelle bekommen `max_tokens` und `temperature`
(der klassische Body); auf die Reasoning-Parameter antworten sie mit `400`. Das
gilt für jeden Provider. Nur für `b-api-router` lässt sich der Body mit
`METADATA_AGENT_LLM_REQUEST_STYLE` festlegen.

### Wiederholungen

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_LLM_MAX_RETRIES` | `3` | Ganze Zahl, mindestens `1` | Versuche je LLM-Aufruf (`1`: keine Wiederholung). Zugleich die Zahl zusätzlicher Aufrufe, die eine Antwort außerhalb eines geschlossenen Vokabulars kosten darf. Ein Pflichtfeld ohne Wert kostet unabhängig von dieser Einstellung genau einen weiteren Aufruf, bei geschlossenem Vokabular keinen |
| `METADATA_AGENT_LLM_RETRY_DELAY` | `1.0` | Sekunden, mindestens `0` | Basis der Wartezeit vor einer Wiederholung: Sie wächst linear mit dem Versuch. Nach einem `429` beträgt sie mindestens das `Retry-After` der Antwort, höchstens 30 s |

- Wiederholt werden: `429`, `500`, `502`, `503`, `504`, ein `200` ohne
  JSON-Objekt und Verbindungsfehler.
- Ein Aufruf, der innerhalb von `METADATA_AGENT_REQUEST_TIMEOUT` keine Antwort
  bekam, wird höchstens zweimal gesendet.
- Nie wiederholt wird jeder andere Status. Ein abgelehnter Schlüssel (`401`,
  `403`) oder ein unbekanntes Modell (`404`) beendet die ganze Erschließung
  sofort.

---

## B-API: Authentifizierung

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` | `auto`, `forward`, `static` | Welchen Schlüssel die Aufrufe an die B-API tragen: LLM-Aufrufe, Textextraktion und Geocoder |

Die B-API kann einen eingetragenen Dienst über `/api/v1/proxy/<dienst>/…`
aufrufen und dabei den Schlüssel des Aufrufers als `Authorization: Bearer <key>`
mitgeben (Admin-Option „API-Key des Aufrufers weiterreichen“, `forwardApiKey`).
Der Proxy-Eintrag dieses Dienstes heißt `metadata-agent-canvas`. edu-sharing
wählt den Schlüssel, einen für eingeloggte Nutzer und einen für Gäste, und die
B-API bucht Nutzung, Kosten und Limits auf ihn. Der andere Weg ist der eigene
`B_API_KEY` des Agenten, gesendet als `X-API-KEY`.

| Modus | Eingehender `Authorization: Bearer …` | Kein Header |
|---|---|---|
| `auto` | Wird unverändert weitergereicht | `B_API_KEY` als `X-API-KEY` |
| `forward` | Wird unverändert weitergereicht | `502` mit Code `LLM_AUTH_MISSING`, kein Rückfall |
| `static` | Wird ignoriert | `B_API_KEY` als `X-API-KEY` |

Für den Default `auto` muss nichts gesetzt werden: Ein Deployment mit
`B_API_KEY` läuft, und sobald der Proxy einen Schlüssel durchreicht, wird
dieser genutzt. Zu setzen ist der Modus nur in zwei Fällen:

- `static`, wenn ein Proxy davor einen `Authorization`-Header mitschickt, der
  kein B-API-Schlüssel ist.
- `forward`, wenn alles über die Schlüssel der Aufrufer abgerechnet werden
  soll. Dann keinen `B_API_KEY` setzen.

Was dabei gilt:

- Gelesen wird nur `Authorization` mit dem Schema `Bearer`; ein eingehender
  `X-API-KEY` wird nicht verwendet. Der Wert geht unverändert und als einziger
  Auth-Header an die B-API.
- Der Schlüssel gilt nur für die Anfrage, die ihn brachte. Er wird nicht
  gespeichert, nicht geloggt und nicht zwischen gleichzeitigen Anfragen
  geteilt.
- Der Modus gilt für alle drei B-API-Provider. Der Provider `openai` nutzt
  `OPENAI_API_KEY`.
- Die Antwort von `/generate` nennt unter `processing.auth_source` die
  Herkunft des Schlüssels (`forwarded` oder `static`), nie den Schlüssel.
- Der Agent prüft den Header nicht selbst. Er darf **nur über die B-API**
  erreichbar sein (Netzwerkregel). Sonst kann jeder mit einem beliebigen
  B-API-Schlüssel Aufrufe stellen, und im Modus `auto` läuft ein Aufruf ohne
  Header mit `B_API_KEY`.

Was ankommt, zeigen die [Debug-Endpunkte](#debug-endpunkte).

---

## B-API: Routing

Mit `METADATA_AGENT_LLM_PROVIDER=b-api-router` gehen die LLM-Aufrufe an
`/api/v1/llm/router/chat/completions`. Das Feld `model` nennt dann eine Route.
Die B-API wählt nach Priorität und Gewicht ein Modell der Route und weicht bei
Fehlern auf das nächste aus.

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_LLM_REQUEST_STYLE` | `auto` | `auto`, `reasoning`, `classic` | Request-Body der Router-Aufrufe. `auto`: nach dem Modellnamen; ein reiner Routenname gilt als klassisch. `reasoning`: für Routen aus GPT-5/6- und o-Modellen. `classic`: für alle anderen. Nur für `b-api-router` |
| `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT` | leer (nicht senden) | leer, `none`, `low`, `medium`, `high` | `reasoning_effort` im klassischen Body der Router-Aufrufe. Denkende Modelle wie `qwen3.6-35b-a3b` brauchen `none`, sonst liefern sie keinen Text. Ältere Modelle wie `gpt-4o` lehnen den Parameter ab |

Den Routennamen oder `provider/modell` setzt
`METADATA_AGENT_B_API_ROUTER_MODEL`; siehe
[Endpunkte und Modelle](#endpunkte-und-modelle).

### Wo Routen definiert werden

**Routen stehen in der B-API, nicht in der Umgebung.** Dort legt ein
Administrator eine globale Route an (Oberfläche „LLM-Routing“ oder
`POST /administration/llm-routing/groups`). Ein Konto mit `LLM_ROUTE_MANAGE`
kann eine eigene Route anlegen (`POST /api/v1/llm/router/routes`). Die Route
bestimmt Modelle, Priorität und Gewicht. Der Agent nennt nur die Route und den
Request-Stil ihrer Modelle. Ein Modelltausch in der Route braucht daher kein
neues Deployment.

Der Router lässt sich auf zwei Arten nutzen:

- **Ein Modell, keine Route.** Das Modell als `provider/modell` setzen. Die
  B-API löst es ohne Route auf und weicht nicht auf andere Modelle aus.
- **Eine Route mit Reserve.** Zuerst legt der Administrator die Route an, mit
  Modellen **einer** Familie und `maxAttempts` von mindestens 2. Danach den
  Routennamen im Agenten setzen.

Das Beispiel zeigt nur den Aufbau einer Route:

```json
{"logicalModel": "metadata-agent",
 "description": "Metadata Agent: Qwen 3, Reserve qwen3.8",
 "deployments": [
   {"id": "qwen36", "providerId": "academiccloud", "upstreamModel": "qwen3.6-35b-a3b", "priorityTier": 0, "weight": 2},
   {"id": "qwen30", "providerId": "academiccloud", "upstreamModel": "qwen3-30b-a3b-instruct-2507", "priorityTier": 0, "weight": 1},
   {"id": "qwen38", "providerId": "academiccloud", "upstreamModel": "qwen3.8-27b", "priorityTier": 1, "weight": 1}
 ],
 "maxAttempts": 2}
```

Die Variablen für beide Arten stehen unter
[Fertige Konfigurationen](#fertige-konfigurationen).

### Was der Agent dabei beachtet

- **Umstieg ohne Ausfall.** Der Default `openai/gpt-6-luna` funktioniert ohne
  Route. Erst die Route anlegen, dann
  `METADATA_AGENT_B_API_ROUTER_MODEL` auf ihren Namen setzen. Ein unbekannter
  Name scheitert sofort mit `LLM_ROUTE_NOT_FOUND`.
- **Request-Stil.** Der Router reicht den Body unverändert an jedes Modell der
  Route weiter, eine Route darf daher nur Modelle einer Familie enthalten. Ein
  Routenname verrät die Familie nicht: Mit `auto` sendet der Agent den
  klassischen Body und warnt beim Start. Passt der Body nicht, endet die
  Erschließung sofort mit `LLM_ROUTE_MISCONFIGURED`.
- **Denkende Modelle.** Ohne
  `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none` verbraucht
  `qwen3.6-35b-a3b` das Token-Budget mit Nachdenken und liefert keinen Text;
  der Agent meldet `LLM_EMPTY_ANSWER`. Instruct-Modelle wie
  `qwen3-30b-a3b-instruct-2507` vertragen den Parameter ebenfalls.
  `qwen3.8-27b` ist im Agenten nicht geprüft. Testergebnisse:
  [UEBERGABE.md](UEBERGABE.md).
- **Fehler.** Dauerhafte Fehler werden nicht wiederholt und beenden die
  Erschließung sofort: eine unbekannte oder abgeschaltete Route
  (`LLM_ROUTE_NOT_FOUND`); ein Modell ohne Preis, ein für den Endpunkt
  ungeeignetes Modell oder der falsche Request-Stil
  (`LLM_ROUTE_MISCONFIGURED`). Die B-API meldet einige davon als `503`,
  deshalb liest der Agent den Fehlertext. Wiederholt werden die Drosselung
  durch GWDG (`503 … RATE_LIMITED`; bleibt sie: `LLM_RATE_LIMITED`) und eine
  Route ohne freies Modell (`no deployment left`; bleibt sie:
  `LLM_UNREACHABLE`).
- **Cache.** Jede Route hat in der B-API einen eigenen Cache, und eine
  Änderung der Route leert ihn nicht. Nach einem Modelltausch den Cache in der
  B-API leeren. `METADATA_AGENT_B_API_IGNORE_CACHE=true` umgeht den Cache.
- **Durchsatz.** Der Agent sieht nicht, welche Modelle hinter einer Route
  stehen. Die Default-Grenzen passen zu `openai/gpt-6-luna`; eine Route aus
  AcademicCloud-Modellen braucht die niedrigeren Werte, siehe
  [Durchsatz](#durchsatz).
- **Prüfen.** Bei einem Routennamen meldet `GET /debug/dependencies`
  zusätzlich `route_listed`: ob die B-API die Route führt.

---

## Textextraktion und Geocoder

Der Agent braucht zwei weitere Dienste: die Textextraktion für Eingaben aus
einer URL und den Geocoder für die Koordinaten von Orten. Beide gibt es einmal
je Umgebung. Über die B-API sind es immer die, die zu dieser B-API gehören.

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_B_API_SERVICES` | `auto` | `auto`, `proxy`, `direct` | Wie Textextraktion und Geocoder erreicht werden. `auto`: über die B-API, wenn der Provider ein B-API-Provider ist und seine LLM-Aufrufe an `METADATA_AGENT_B_API_BASE_URL` gehen, sonst direkt. `proxy`: immer über die B-API. `direct`: nie über die B-API |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | leer (gewählt nach `METADATA_AGENT_B_API_SERVICES`) | URL | Benennt selbst eine Textextraktion, zum Beispiel den Dienst im Cluster. Pflicht, wenn sich keine Adresse ableiten lässt: ohne B-API und mit einem Repository, das weder Staging noch Produktion von OpenEduHub ist |

| Weg | Textextraktion | Geocoder |
|---|---|---|
| Über die B-API | `<B-API>/api/v1/proxy/text-extraction` | `<B-API>/api/v1/proxy/photon_geocoding/api` |
| Direkt | Nach der Repository-Adresse: `https://text-extraction.staging.openeduhub.net` für Staging von OpenEduHub, `https://text-extraction.prod.openeduhub.net` für Produktion, keine für jedes andere Repository | `https://photon.komoot.io/api` (Photon, ein Fremddienst) |

- **Schlüssel.** Ein Aufruf an eine Adresse unter
  `METADATA_AGENT_B_API_BASE_URL` trägt denselben Schlüssel wie die
  LLM-Aufrufe. Jede andere Adresse bekommt nie einen Schlüssel. Das gilt auch
  für eine in `METADATA_AGENT_TEXT_EXTRACTION_API_URL` gesetzte Adresse.
- **Rechte.** Der Schlüssel braucht die Proxy-Dienste `text-extraction` und
  `photon_geocoding`. Ohne das Recht scheitert das Erschließen aus einer URL
  mit `502` `TEXT_EXTRACTION_AUTH_REFUSED`. Lehnt die B-API den Geocoder ab
  oder antwortet sie nicht, fragt der Agent mit `auto` Photon direkt (wie vor
  4.0.0, Warnung im Log); mit `proxy` bleiben Orte ohne Koordinaten (Hinweis
  in `processing.warnings`). Ganz ohne Schlüssel lautet der Code
  `TEXT_EXTRACTION_AUTH_MISSING` (`502`).
- **Keine Adresse.** Ohne Adresse der Textextraktion antwortet das Erschließen
  aus einer URL mit `503` `TEXT_EXTRACTION_NOT_CONFIGURED`. Der Agent warnt
  beim Start.
- **Umgebung des Repositorys.** Erkannt werden nur Hosts von `openeduhub.net`:
  Ein Host mit `staging` im Namen ist Staging; ein Host mit `.prod.` im Namen
  oder `redaktion.openeduhub.net` ist Produktion.

Geändert in 4.0.0: `METADATA_AGENT_B_API_SERVICES` ist neu,
und der Default von `METADATA_AGENT_TEXT_EXTRACTION_API_URL` ist jetzt leer.
Beide Dienste laufen standardmäßig über die B-API, die Schlüssel brauchen also
die beiden Proxy-Rechte. `METADATA_AGENT_B_API_SERVICES=direct` oder eine
gesetzte Adresse behält den früheren Weg bei.

---

## Repository

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_REPOSITORY_URL` | `https://repository.staging.openeduhub.net/edu-sharing/rest` | URL | REST-Adresse des edu-sharing-Repositorys, für das Lesen von Nodes **und** für das Schreiben. Mit oder ohne `/rest` am Ende; beides bedeutet dasselbe. Produktion: `https://redaktion.openeduhub.net/edu-sharing/rest` |
| `METADATA_AGENT_WLO_INBOX_ID` | `21144164-30c0-4c01-ae16-264452197063` | Node-ID | Ordner, in dem neue Nodes entstehen |
| `METADATA_AGENT_REQUIRE_REPOSITORY` | `true` | `true`, `false` | Ob `/ready` `WLO_GUEST_USERNAME` und `WLO_GUEST_PASSWORD` verlangt. `false` für ein Deployment, das nur erschließt: Es wird ohne sie bereit, `/upload` und `/nodes` antworten mit `503`, und das Startlog warnt |

Das Konto setzen `WLO_GUEST_USERNAME` und `WLO_GUEST_PASSWORD`; siehe
[Schlüssel und Zugangsdaten](#schlüssel-und-zugangsdaten). Es gibt keinen
zweiten Schalter für das Ziel-Repository: Der Parameter `repository` einer
Anfrage wird ignoriert.

---

## Durchsatz

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` | leer (Default des Providers) | Ganze Zahl, mindestens `1` | Gleichzeitige LLM-Anfragen |
| `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` | leer (Default des Providers) | Zahl, mindestens `0`; `0`: keine Ratenbegrenzung | LLM-Anfragen pro Sekunde |
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `20` | 1 bis 20; andere Werte werden begrenzt | Parallele Feld-Erschließungen eines `/generate` ohne `max_workers`. Ein `max_workers` in der Anfrage hat Vorrang. Gemessen am 2026-11-15 mit `b-api-openai` (47 Felder, Median aus sechs Läufen): 10 Worker 7,8 s, 20 Worker 5,2 s |
| `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` | leer (keine Grenze) | Ganze Zahl, mindestens `1` | Wie viele `/generate` eine Instanz zugleich annimmt. Eine weitere Anfrage bekommt sofort `503` `TOO_MANY_GENERATIONS` mit `Retry-After`, statt sich das LLM-Budget mit den laufenden zu teilen, bis alle am Zeitlimit enden. Die richtige Zahl liefert ein Lasttest |

Defaults der Provider:

| Provider | Gleichzeitig | Pro Sekunde |
|---|---:|---:|
| `b-api-openai` | 20 | keine Grenze |
| `b-api-academiccloud` | 2 | 2 |
| `b-api-router` | 20 | keine Grenze |
| `openai` | 20 | keine Grenze |

- Beide LLM-Grenzen gelten für den **ganzen Prozess**, nicht pro Anfrage, und
  für jeden Provider getrennt. Eine Wiederholung ist eine eigene Anfrage.
- Ein gesetzter Wert gilt für **jeden** Provider. Ein Wert über 2 hebt also
  auch AcademicCloud über seine gemessene Grenze.
- Die Worker sind durch die Grenze gleichzeitiger Anfragen gedeckelt. Bei
  AcademicCloud laufen 2 Worker, egal was gesetzt ist; mehr zu fordern erzeugt
  nur Wartende. Das Startlog zeigt die wirksame Zahl.
- Bei mehreren Instanzen wendet jede die Grenzen für sich an. Eine Grenze,
  die alle Instanzen gemeinsam trifft, ist durch deren Zahl zu teilen. Der
  Helm-Chart erledigt das mit `sharedLlmLimit`, siehe
  [Helm-Chart](#helm-chart).

**AcademicCloud (GWDG)** hat zwei gemessene Grenzen. Beide gelten für den
B-API-Schlüssel, nicht für einen Prozess:

- Zwei gleichzeitige Anfragen (gemessen 2026-08-12). Eine dritte parallele
  Anfrage wird sofort mit `429` abgewiesen, und zwar **ohne `retry-after`**;
  ein Client muss also unter der Grenze bleiben. Mehr Last hilft nicht: Bei
  3 Anfragen pro Sekunde fiel der effektive Durchsatz unter den bei 2.
- Etwa 10 Aufrufe pro Minute und etwa 200 pro Stunde (gemessen 2026-10-15 über
  den Router mit Qwen-Modellen). Für ein ganzes `/generate` mit etwa
  50 Aufrufen ist das die engere Grenze. Für `deepseek-v4-flash` über
  `b-api-academiccloud` ist sie nicht nachgemessen.

Die Defaults decken nur die erste Grenze ab. Für die zweite gilt
`METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16` (etwa 10 pro Minute). Das
gilt auch für eine Route von `b-api-router` aus AcademicCloud-Modellen. Bei
dieser Rate dauert ein `/generate` mit etwa 50 Aufrufen etwa 5 Minuten und
endet an `METADATA_AGENT_GENERATE_TIMEOUT` mit einem Teilergebnis.

**`b-api-openai`:** Ein echter `/generate`-Lauf am 2026-10-06 mit `gpt-6-luna`
und 20 gleichzeitigen Anfragen lief ohne `429` durch. Am 2026-11-15 wurden
auch 60 und 100 Aufrufe im selben Moment alle mit `200` beantwortet, 60 auch
über `b-api-router`. Die Grenze bleibt trotzdem bei 20: 60 Worker brauchten
4,6 s, 20 brauchen 5,2 s, und eine halbe Sekunde ist die dreifache Last auf
einem geteilten Schlüssel nicht wert, bevor das im Betrieb erprobt ist.
Antwortet das Gateway mit `429`, `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS`
senken. Mit `gpt-6-luna` dauerte die Erschließung einer Webseite 12 bis 22 s
(gemessen 2026-10-16). Ein Text mit 4.400 Zeichen dauerte vor den Änderungen
vom 2026-11-15 9,3 s und dauert seitdem 5,2 s (Median aus sechs Läufen): Der
Cache-Vorlauf läuft jetzt parallel zur Erkennung der Inhaltsart.

---

## Grenzen und Zeitlimits

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_REQUEST_TIMEOUT` | `90` | Sekunden, ganze Zahl, mindestens `1` | Lese-Timeout eines LLM-Aufrufs. Der Verbindungsaufbau hat eigene 10 s |
| `METADATA_AGENT_GENERATE_TIMEOUT` | `300` | Sekunden, über 0 | Zeitlimit für ein ganzes `/generate`: Abruf der Quelle, alle Feldaufrufe, Wiederholungen, Geocoding. Gilt auch für `/detect-content-type` und `/extract-field` |
| `METADATA_AGENT_UPLOAD_TIMEOUT` | `100` | Sekunden, über 0 | Zeitlimit für einen Schreibvorgang: `/upload`, `/nodes`, `/node`, `/workflow/{node_id}` und `/screenshot` mit Node |
| `METADATA_AGENT_MAX_REQUEST_BYTES` | `20971520` (20 MB) | Bytes, mindestens `1024` | Größter Request-Body. Ein größerer bekommt `413`, bevor er gelesen wird. Der Default lässt Platz für `/upload` mit Vorschaubild als Data-URL |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS` | leer (angenommen werden 320 s) | Sekunden, ganze Zahl, mindestens `1` | Zeit, die die Plattform dem Prozess nach SIGTERM lässt, wenn es nicht 320 s sind. Nur die Warnungen beim Start nutzen den Wert: Sie vergleichen `METADATA_AGENT_GENERATE_TIMEOUT` und `METADATA_AGENT_UPLOAD_TIMEOUT` damit. Sonst ändert er nichts. Der Helm-Chart setzt ihn aus `terminationGracePeriodSeconds`; mit Docker auf den Wert von `--stop-timeout` oder `stop_grace_period` setzen |

Die Zeitlimits bauen aufeinander auf: Ein LLM-Aufruf (90 s) bleibt unter einem
ganzen `/generate` (300 s), und das bleibt unter den 320 s von Ingress und
Grace-Period.

- **`/generate` am Limit.** Feldaufrufe, die 5 s vor dem Limit noch laufen,
  werden beendet. Was bis dahin erschlossen ist, kommt als `partial` zurück;
  die späten Felder stehen in `processing.errors`. Wird das Limit trotzdem
  erreicht, etwa während die Quelle noch abgerufen wird, lautet die Antwort
  `504` mit Code `GENERATION_TIMEOUT` und Request-ID, und die laufenden
  Aufrufe werden abgebrochen.
- **Ein Proxy davor braucht mindestens etwa 310 s.** Das gilt für einen
  B-API-Proxy und für edu-sharing; sonst brechen sie lange Läufe vorher ab.
  Über 310 warnt der Agent beim Start: Mit 10 s für Antwort und preStop liegt
  das Limit über 320 s.
- **Ein Schreibvorgang am Limit.** Die Antwort ist `504` mit Code
  `UPLOAD_TIMEOUT` und sagt, was mit dem Node geschah: `discarded_node`
  (verworfen, im Papierkorb) oder `node`, wenn er nicht verworfen werden
  konnte, schon im Workflow ist oder alle Schreibschritte gelaufen sind. Ein
  neuer Node bleibt auch erhalten und steht als `node` in der Antwort, sobald
  das Verknüpfen mit Sammlungen begonnen hat; `collections` nennt dann die
  entstandenen Verknüpfungen. Ein aktualisierter bestehender Node kann
  teilweise geschrieben sein. Das Limit plus 5 s Suche, 5 s Verwerfen und 5 s
  preStop bleibt unter 320 s; über 305 warnt der Agent beim Start.
- **Eine andere Grace-Period.** Beide Warnungen vergleichen mit
  `METADATA_AGENT_GRACE_PERIOD_SECONDS` statt mit 320 s, wenn der Wert gesetzt
  ist.
- **Quelltext.** Der Quelltext wird auf 150.000 Zeichen gekürzt;
  `processing.warnings` nennt die Originallänge. Diese Grenze ist fest.
- **`/validate` und `/export/markdown`.** Ihr Request-Body ist auf 2 MiB
  begrenzt, gleich was `METADATA_AGENT_MAX_REQUEST_BYTES` sagt; ein größerer
  bekommt `413`. Diese Grenze ist fest.

Geändert in 4.0.0:
`METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` und
`METADATA_AGENT_GRACE_PERIOD_SECONDS` sind neu, ein Lauf am Limit liefert ein
Teilergebnis statt `504`, und ein Lese-Timeout wird nur einmal wiederholt.
Werte außerhalb der Bereiche in den Tabellen beenden jetzt den Start.

---

## Screenshots

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_SCREENSHOT_WIDTH` | `1920` | Pixel, `320` bis `3840` | Breite des Browserfensters |
| `METADATA_AGENT_SCREENSHOT_HEIGHT` | `1080` | Pixel, `200` bis `2160` | Höhe des Browserfensters |
| `METADATA_AGENT_SCREENSHOT_FORMAT` | `png` | `png`, `jpeg` | Bildformat. Jeder andere Wert (zum Beispiel `webp`) wird als PNG geliefert |
| `METADATA_AGENT_SCREENSHOT_FULL_PAGE` | `false` | `true`, `false` | `true`: die ganze scrollbare Seite statt des Fensters |
| `METADATA_AGENT_SCREENSHOT_DELAY` | `2000` | Millisekunden, `0` bis `10000` | Wartezeit vor der Aufnahme |

- **Wo sie gelten.** Breite, Höhe, Format, ganze Seite und Wartezeit gelten
  für die Vorschaubilder, die `/generate` und `/upload` aufnehmen.
  `/screenshot` nimmt alle Werte aus seiner Anfrage; deren Defaults und
  Bereiche sind dieselben Zahlen und werden von diesen Variablen nicht
  geändert.
- **Methode.** Keine Einstellung wählt die Aufnahmemethode: Eine Anfrage nennt
  sie (`playwright`, der Default, oder den Legacy-Alias `pageshot`; beide
  nehmen lokal auf).
- **Chromium.** Screenshots entstehen immer lokal mit dem Chromium im
  Docker-Image. Einen externen Screenshot-Dienst nutzt der Agent nicht.
- **Ziele.** Aufgenommen werden nur öffentliche http(s)-Ziele. Die Prüfung
  löst DNS im Container auf: Ein Hostname mit privater Adresse (zum Beispiel
  Split-Horizon-DNS im Cluster) wird abgelehnt.

---

## Logging und Tracing

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Schwelle des Logs |
| `METADATA_AGENT_LOG_FORMAT` | `text` | `text`, `json` | `text`: nur die Meldung, je eine Zeile. `json`: ein Objekt je Zeile, für einen Log-Sammler; auch für das Server-Log von uvicorn |
| `METADATA_AGENT_SERVICE_NAME` | `metadata-agent` | Text | Feld `service` jeder JSON-Logzeile: die Dienst-ID, unter der der Betrieb den Dienst führt (Chart, Release, Dashboards). Der Proxy-Eintrag der B-API heißt abweichend `metadata-agent-canvas` |

Logs gehen immer nach stdout.

- **JSON-Felder.** `timestamp` (UTC), `level`, `service`, `logger`, `message`,
  `trace_id`, `span_id` und `request_id`; dazu `client_trace_id`, wenn der
  Aufrufer `x-client-trace-id` schickt.
- **Eine Zeile je Anfrage** mit Methode, Pfad, Status und Dauer; in JSON
  zusätzlich als Felder `method`, `path`, `status` und `duration_ms`. Die
  Zeile enthält weder Query-String noch Header, und der Pfad wird nach
  2.000 Zeichen abgeschnitten. `/health` und `/ready` erscheinen nur mit
  `METADATA_AGENT_LOG_LEVEL=DEBUG`. Die Adresse des Aufrufers fehlt bewusst:
  Hinter dem Proxy wäre das die Adresse der Nutzerin oder des Nutzers. Das
  Access-Log von uvicorn ist aus.
- **Textzeilen.** Im Format `text` bleibt eine Meldung auf einer Zeile, und
  Steuerzeichen aus Werten einer Anfrage werden entfernt. Python-Warnungen von
  Bibliotheken erscheinen als Logzeilen des Loggers `py.warnings`.
- **IDs.** `trace_id` und `span_id` sind die B3-Werte. `request_id` ist
  `x-client-trace-id`, sonst `x-request-id`, sonst die Trace-ID; ein
  eingehender Wert zählt nur mit höchstens 64 Zeichen aus Buchstaben, Ziffern,
  `.`, `_` und `-`. Derselbe Wert steht im Antwort-Header `X-Request-ID` und
  in Fehlerantworten (`error.request_id`).
- **Tracing-Header.** Eingehende `x-client-trace-id`, `x-request-id`, die
  B3-Header `x-b3-*` und `b3` gibt der Agent unverändert an die eigenen
  Dienste weiter: die B-API und das Repository, einschließlich Textextraktion
  und Geocoder über die B-API. An Fremddienste wie Photon gibt er sie nie
  weiter. Ein Wert mit mehr als 128 Zeichen oder mit anderen Zeichen entfällt.
  Ein `x-request-id`, das nicht die Request-ID sein kann (65 bis 128 Zeichen),
  wird ebenfalls nicht weitergegeben; an seiner Stelle geht die Request-ID
  hinaus. Ohne gültige B3-Trace-ID (16 oder 32 Hex-Zeichen, Span 16) legt er
  eine an (`x-b3-traceid`, `x-b3-spanid`) und gibt sie mit. Ein `b3`, das nur
  die Sampling-Entscheidung trägt (`0`, `1`, `d`), geht als `x-b3-sampled`
  oder `x-b3-flags` weiter. Eigene Spans meldet der Agent nicht.

Geändert in 4.0.0: `METADATA_AGENT_SERVICE_NAME` ist neu. In
den JSON-Zeilen heißt `time` jetzt `timestamp`, und `trace_id` ist die
B3-Trace-ID; der frühere Wert steht in `request_id`.

---

## Ports, CORS und Debug-Endpunkte

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_INTERNAL_PORT` | leer (kein interner Port) | 1 bis 65535 | Zweiter, interner Port mit `/health`, `/ready` und `/metrics` (Prometheus). Leer: kein zweiter Server und kein `/metrics`. Nicht über den Ingress freigeben |
| `METADATA_AGENT_INTERNAL_HOST` | `0.0.0.0` | Adresse | Adresse, auf der der interne Port lauscht. `::` in einem Cluster, dessen Pods IPv6-Adressen haben (Probes über IPv6); `UVICORN_HOST` auf dieselbe Adresse setzen, siehe [unten](#proxy-header-uvicorn). Der Helm-Chart setzt beide aus `listenHost` |
| `METADATA_AGENT_CORS_ORIGINS` | `*` | `*`, Origins durch Komma getrennt, oder leer | Origins, die die API aus einem Browser aufrufen dürfen. Leer: kein Origin |
| `METADATA_AGENT_DEBUG_ENDPOINTS` | `false` | `true`, `false` | Schaltet `GET /debug/auth` und `GET /debug/dependencies` frei. Ausgeschaltet antworten beide mit `404`. Nur zum Einrichten oder zur Fehlersuche einschalten |

- **Öffentlicher Port.** Die API lauscht auf Port 8000; das ist im Image
  festgelegt. Die Adresse, auf der sie lauscht, ist `UVICORN_HOST`, siehe
  [unten](#proxy-header-uvicorn). `/health` und `/ready` gibt es auch dort,
  ohne Details. Auf dem internen Port nennt `/health` zusätzlich Provider und
  Auth-Modus und `/ready` die Gründe.
- **Interner Port.** Er braucht **einen Worker je Container**. Ist der Port
  belegt oder kennt der Host die Adresse aus `METADATA_AGENT_INTERNAL_HOST`
  nicht, meldet der Agent den Fehler im Log und läuft ohne internen Port
  weiter.
- **CORS.** Credentials sind nie erlaubt. Die Antwort-Header `X-Request-ID`
  und `Retry-After` kann die aufrufende Seite lesen.

### Proxy-Header (uvicorn)

Diese Variablen liest uvicorn, nicht der Agent. Sie tragen nicht das Präfix
des Agenten, und `tests/test_env_parameters.py` prüft sie nicht.

| Variable | Default | Bedeutung und Einsatz |
|---|---|---|
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Adressen, deren `X-Forwarded-Proto` und `X-Forwarded-For` uvicorn übernimmt (durch Komma getrennt, oder `*`). Der Agent bildet aus diesen Headern keine Adresse: `/widget/info` liefert relative URLs, und ein Schrägstrich am Ende wird nicht umgeleitet. Der Helm-Chart setzt sie aus `forwardedAllowIps`; mit Docker `-e FORWARDED_ALLOW_IPS=<Proxy-Adresse>`. Mehr in [DEPLOYMENT.de.md](DEPLOYMENT.de.md) |
| `UVICORN_HOST` | `0.0.0.0` im Docker-Image | Adresse, auf der der API-Port lauscht. `::` in einem Cluster, dessen Pods IPv6-Adressen haben, zusammen mit `METADATA_AGENT_INTERNAL_HOST`. Der Helm-Chart setzt beide aus `listenHost`; mit Docker `-e UVICORN_HOST=<Adresse>`. Ohne das Image gestartet lauscht uvicorn auf `127.0.0.1`, solange weder diese Variable noch `--host` etwas anderes sagt |

### Debug-Endpunkte

Beide gibt es nur mit `METADATA_AGENT_DEBUG_ENDPOINTS=true`. Danach wieder
ausschalten. Über den B-API-Proxy müssen ihre Pfade dort freigegeben sein.

`GET /debug/auth` zeigt, welcher Schlüssel ankommt: nur Vorhandensein, Schema
und Länge.

```json
{"mode": "auto", "Authorization": {"present": true, "scheme": "Bearer", "length": 36},
 "X-API-KEY": {"present": false}, "b_api_key_configured": true, "llm_calls_use": "forwarded"}
```

`GET /debug/dependencies` prüft, ob die Abhängigkeiten erreichbar sind und ob
die B-API den Schlüssel annimmt. Das Zeitlimit je Prüfung beträgt 5 s.

- **LLM-Dienst.** Der Agent fragt die Modellliste ab
  (`GET <LLM-Basis>/models`), mit dem Schlüssel, den die LLM-Aufrufe dieser
  Anfrage tragen würden. Das verbraucht keine Tokens und wird genauso
  abgelehnt wie eine Erschließung. Entfällt beim Provider `openai`.
- **Textextraktion und Geocoder über die B-API.** Gefragt wird mit demselben
  Schlüssel (`…/proxy/text-extraction/metrics` und eine Ortssuche nach
  „Berlin“). `authorized: false` heißt: Dem Schlüssel fehlt das Recht für den
  Proxy-Dienst (`401` oder `403`). Ohne Schlüssel wird nichts gesendet.
- **Repository und eine direkt aufgerufene Textextraktion.** Sie gelten als
  erreichbar, sobald irgendeine HTTP-Antwort kommt, gleich mit welchem Status.
  Photon direkt wird nicht geprüft (Fremddienst).
- **Antwort.** Sie nennt Adressen, Status, Dauer und die Art des Fehlers, nie
  einen Schlüssel.

```json
{"version": "4.0.2", "llm_provider": "b-api-openai", "b_api_auth_mode": "auto",
 "config_warnings": [],
 "checks": {
   "llm": {"url": "https://b-api.prod.openeduhub.net/api/v1/llm/openai/models",
           "reachable": true, "status": 403, "ms": 84, "model": "gpt-6-luna",
           "auth_source": "forwarded", "authorized": false},
   "text_extraction": {"url": "https://b-api.prod.openeduhub.net/api/v1/proxy/text-extraction",
                       "reachable": true, "status": 403, "ms": 61,
                       "auth_source": "forwarded", "authorized": false},
   "geocoder": {"url": "https://b-api.prod.openeduhub.net/api/v1/proxy/photon_geocoding/api",
                "reachable": true, "status": 200, "ms": 240,
                "auth_source": "forwarded", "authorized": true},
   "repository": {"url": "https://redaktion.openeduhub.net/edu-sharing/rest",
                  "reachable": true, "status": 404, "ms": 41}}}
```

---

## Weitere Einstellungen

| Variable | Default | Werte | Bedeutung und Einsatz |
|---|---|---|---|
| `METADATA_AGENT_APP_NAME` | `Metadata Agent API` | Text | Name im Startlog und Titel des OpenAPI-Dokuments. Wird normalerweise nicht gesetzt |
| `METADATA_AGENT_APP_VERSION` | `4.0.2` | Text | Version, die der Agent meldet (`/health`, OpenAPI, `/widget/info`). Nicht setzen: Der Wert ändert nur die gemeldete Version, nicht die Software |

Keine Einstellung wählt das Schema: Eine Anfrage nennt ihren eigenen
`context`, Default `default` (der Wert, den `GET /info/schemata` als
`default_context` meldet), und ihre eigene `version`, Default `latest`. Für
beide Kontexte löst `latest` auf **2.0.0** auf.

---

## Fertige Konfigurationen

Jeder Block nennt nur, was von den Defaults abweicht. Werte in spitzen
Klammern sind zu ersetzen. Die Adressen sind die der Produktion; die Defaults
zeigen auf Staging.

### Hinter dem B-API-Proxy

edu-sharing → B-API `/api/v1/proxy/metadata-agent-canvas` → Agent. Die
Schlüssel von Nutzern und Gästen werden durchgereicht.

```env
METADATA_AGENT_LLM_PROVIDER=b-api-openai
METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
# auto: der durchgereichte Schlüssel, sonst B_API_KEY (Direktaufrufe, Tests).
# forward: nur durchgereichte Schlüssel; ein Aufruf ohne Header endet mit 502 (LLM_AUTH_MISSING).
METADATA_AGENT_B_API_AUTH_MODE=auto
B_API_KEY=<B-API-Schlüssel>
WLO_GUEST_USERNAME=<Repository-Benutzer>
WLO_GUEST_PASSWORD=<Repository-Passwort>
```

In der B-API muss der Dienst mit gesetztem `forwardApiKey` eingetragen sein.
Die durchgereichten Schlüssel brauchen die LLM-Endpunkte und die Proxy-Dienste
`text-extraction` und `photon_geocoding`. Der Agent darf nur über die B-API
erreichbar sein, und der Proxy braucht ein Timeout von mindestens etwa 310 s.

### Eigenständig mit B-API-Schlüssel

Ohne den Proxy läuft jeder Aufruf mit dem eigenen Schlüssel des Agenten.

```env
METADATA_AGENT_LLM_PROVIDER=b-api-openai
METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
# static: nur B_API_KEY; ein eingehender Authorization-Header wird ignoriert.
METADATA_AGENT_B_API_AUTH_MODE=static
B_API_KEY=<B-API-Schlüssel>
WLO_GUEST_USERNAME=<Repository-Benutzer>
WLO_GUEST_PASSWORD=<Repository-Passwort>
```

Der Default `auto` funktioniert ebenfalls; er würde einen eingehenden
Bearer-Header an die B-API weiterreichen. Wer den Port erreicht, erschließt
auf Kosten von `B_API_KEY`; davor gehört daher eine Zugangsbeschränkung.
Ein Deployment, das nur erschließt und nie ins Repository schreibt, lässt die
beiden Zeilen `WLO_GUEST_…` weg und setzt
`METADATA_AGENT_REQUIRE_REPOSITORY=false`.

### OpenAI direkt

```env
METADATA_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=<OpenAI-Schlüssel>
METADATA_AGENT_OPENAI_MODEL=gpt-4o-mini
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
WLO_GUEST_USERNAME=<Repository-Benutzer>
WLO_GUEST_PASSWORD=<Repository-Passwort>
```

Es gelten die Defaults des Providers: 20 gleichzeitige Anfragen, keine
Ratenbegrenzung. Ohne B-API werden Textextraktion und Geocoder direkt
aufgerufen.

Für ein eigenes OpenAI-kompatibles Gateway:

```env
METADATA_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=<Schlüssel, den das Gateway erwartet>
METADATA_AGENT_OPENAI_API_BASE=https://llm-gateway.example/v1
METADATA_AGENT_OPENAI_MODEL=<Modellname>
# Was das Gateway verträgt.
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=4
```

### AcademicCloud

Die Variante mit den engsten Grenzen; siehe [Durchsatz](#durchsatz).

```env
METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
B_API_KEY=<B-API-Schlüssel>
METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL=deepseek-v4-flash
# Das Kontingent des Schlüssels bei GWDG liegt bei etwa 10 Aufrufen pro Minute.
# Die Rate gilt pro Sekunde: 0.16 = etwa 10 Aufrufe pro Minute.
# Beide Werte gelten je Instanz: bei mehreren durch deren Zahl teilen
# (Helm-Chart: sharedLlmLimit.enabled=true).
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16
```

### B-API-Router: ein Modell, keine Route

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=academiccloud/qwen3-30b-a3b-instruct-2507
METADATA_AGENT_LLM_REQUEST_STYLE=classic
# AcademicCloud-Modelle teilen sich das GWDG-Kontingent des Schlüssels: Durchsatz begrenzen.
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16
B_API_KEY=<B-API-Schlüssel>
```

### B-API-Router: eine Route

Die Route muss zuerst in der B-API bestehen; siehe
[Wo Routen definiert werden](#wo-routen-definiert-werden).

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=<Routenname>
METADATA_AGENT_LLM_REQUEST_STYLE=classic
# Denkende Modelle in der Route brauchen "none".
METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none
# AcademicCloud-Modelle teilen sich das GWDG-Kontingent des Schlüssels: Durchsatz begrenzen.
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16
B_API_KEY=<B-API-Schlüssel>
```

Eine Route nur aus GPT-5/6-Modellen braucht
`METADATA_AGENT_LLM_REQUEST_STYLE=reasoning` und keine Durchsatzgrenze.

---

## Helm-Chart

Der Chart in `helm/metadata-agent/` gibt die Einstellungen als
Umgebungsvariablen an die Pods. Mehr zum Chart:
[helm/metadata-agent/README.md](helm/metadata-agent/README.md) und
[DEPLOYMENT.de.md](DEPLOYMENT.de.md).

### Was der Chart setzt

Aus dem Block `env` in `values.yaml`:

| Variable | Wert im Chart | Weicht vom Default im Code ab |
|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | nein |
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` | nein |
| `METADATA_AGENT_B_API_BASE_URL` | `https://b-api.prod.openeduhub.net` | **ja**: Produktion statt Staging |
| `METADATA_AGENT_REPOSITORY_URL` | `https://redaktion.openeduhub.net/edu-sharing/rest` | **ja**: Produktion statt Staging |
| `METADATA_AGENT_WLO_INBOX_ID` | `21144164-30c0-4c01-ae16-264452197063` | nein; vor der ersten Installation prüfen, ob der Ordner im Ziel-Repository besteht und das Konto dorthin schreiben darf |
| `METADATA_AGENT_B_API_SERVICES` | `auto` | nein |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | leer | nein |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `90` | nein |
| `METADATA_AGENT_GENERATE_TIMEOUT` | `300` | nein |
| `METADATA_AGENT_UPLOAD_TIMEOUT` | `100` | nein |
| `METADATA_AGENT_MAX_REQUEST_BYTES` | `20971520` | nein |
| `METADATA_AGENT_DEBUG_ENDPOINTS` | `false` | nein |
| `METADATA_AGENT_REQUIRE_REPOSITORY` | `true` | nein |
| `METADATA_AGENT_LOG_FORMAT` | `json` | **ja**: `json` statt `text` |
| `METADATA_AGENT_LOG_LEVEL` | `INFO` | nein |
| `METADATA_AGENT_SERVICE_NAME` | `metadata-agent` | nein |

Vom Template `templates/deployment.yaml` aus anderen Chart-Werten gesetzt:

| Variable | Quelle im Chart | Wert im Standard |
|---|---|---|
| `METADATA_AGENT_INTERNAL_PORT` | `service.internalPort` | `9000`; die Probes nutzen diesen Port, und der ServiceMonitor erreicht ihn über den Service `<name>-internal`, der immer `ClusterIP` ist. Der Haupt-Service trägt nur den API-Port |
| `FORWARDED_ALLOW_IPS` | `forwardedAllowIps` | `*`; vertretbar nur mit der NetworkPolicy des Charts |
| `UVICORN_HOST`, `METADATA_AGENT_INTERNAL_HOST` | `listenHost` | `0.0.0.0`; eine Adresse für den API-Port und den internen Port. `"::"` in einem Cluster, dessen Pods IPv6-Adressen haben |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS` | `terminationGracePeriodSeconds` | `320` |
| `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` | `sharedLlmLimit`, nur wenn eingeschaltet | `concurrentRequests` (2) geteilt durch `replicaCount` (2), mindestens 1: `1` |
| `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` | `sharedLlmLimit`, nur wenn eingeschaltet | `requestsPerMinute` (10) geteilt durch 60 und durch `replicaCount`: `0.0833` |
| `B_API_KEY`, `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` | Das Secret, ein Eintrag je Name in `secret.keys` | Jeder Eintrag ist optional eingebunden: Der Pod startet auch ohne ihn. Ob die Konfiguration arbeiten kann, meldet `/ready`, siehe [Bereitschaft](#bereitschaft) |

Jede andere Variable behält den Default des Codes.

### Überschreiben

**Jede Variable dieser Referenz** gehört unter `env`, in einer Values-Datei
oder mit `--set`. Ein Eintrag dort ersetzt den Chart-Wert; `""` ergibt den
Default des Codes (die drei Ausnahmen stehen unter
[Leere Werte](#leere-werte)). Die beiden Adressen
`METADATA_AGENT_B_API_BASE_URL` und `METADATA_AGENT_REPOSITORY_URL` dürfen
nicht leer sein: Der Chart bricht mit einem Fehler ab, weil der Default des
Codes Staging ist.

```yaml
# values-staging.yaml
env:
  METADATA_AGENT_B_API_BASE_URL: https://b-api.staging.openeduhub.net
  METADATA_AGENT_REPOSITORY_URL: https://repository.staging.openeduhub.net/edu-sharing/rest
  METADATA_AGENT_LOG_LEVEL: DEBUG
  # Eine Variable, die der Chart nicht nennt:
  METADATA_AGENT_ALLOWED_LLM_MODELS: "gpt-6-luna"
```

```bash
helm upgrade --install metadata-agent helm/metadata-agent \
  --set image.repository=<Registry-Pfad des Images> \
  --set secret.existingSecret=<Name des Secrets> \
  -f values-staging.yaml \
  --set env.METADATA_AGENT_B_API_AUTH_MODE=forward
```

**Nicht unter `env`:** Der Chart bricht mit einem Fehler ab bei

- `METADATA_AGENT_INTERNAL_PORT` und `FORWARDED_ALLOW_IPS`: stattdessen
  `service.internalPort` und `forwardedAllowIps` setzen;
- `UVICORN_HOST` und `METADATA_AGENT_INTERNAL_HOST`: stattdessen `listenHost`
  setzen;
- `METADATA_AGENT_GRACE_PERIOD_SECONDS`: stattdessen
  `terminationGracePeriodSeconds` setzen;
- `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` und
  `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND`, solange
  `sharedLlmLimit.enabled` `true` ist: `sharedLlmLimit.concurrentRequests` und
  `sharedLlmLimit.requestsPerMinute` ändern oder `sharedLlmLimit` ausschalten.

**Schlüssel und Zugangsdaten** gehören nie in `values.yaml`. Ein vorhandenes
Secret nennt `secret.existingSecret`; alternativ legt der Chart eines an, mit
`secret.create=true` und `--set secret.values.<KEY>=…`. Nur die Namen in
`secret.keys` erreichen den Container. Für den Provider `openai` kommt
`OPENAI_API_KEY` in `secret.keys` hinzu. Pods lesen ein Secret nur beim
Start: nach einer Änderung `kubectl rollout restart` ausführen.

**`sharedLlmLimit`** ist im Standard aus. Einzuschalten ist es für
AcademicCloud oder für eine Router-Route aus AcademicCloud-Modellen, sobald
mehr als ein Pod läuft. Skalieren von Hand (`kubectl scale`, ein HPA) umgeht die Teilung.
`requestsPerMinute` muss über 0 liegen, ebenso die Rate je Pod nach der
Teilung, gerundet auf vier Nachkommastellen; sonst bricht der Chart mit einem
Fehler ab.

**Werte, die zusammengehören.** Wer `METADATA_AGENT_GENERATE_TIMEOUT` oder
`METADATA_AGENT_UPLOAD_TIMEOUT` erhöht, erhöht auch
`terminationGracePeriodSeconds` und die beiden Timeout-Annotationen des
Ingress (je 320 s). Der Agent bekommt die Grace-Period als
`METADATA_AGENT_GRACE_PERIOD_SECONDS` und warnt beim Start, wenn ein Zeitlimit
nicht hineinpasst. Wer `METADATA_AGENT_MAX_REQUEST_BYTES` erhöht, erhöht auch
die Annotation `proxy-body-size` des Ingress (`20m`): Bei eingeschaltetem
Ingress bricht der Chart mit einem Fehler ab, solange die Annotation eine
andere Größe nennt.

---

## Weitere Dokumente

| Dokument | Inhalt |
|---|---|
| [README.de.md](README.de.md) | Überblick und Schnellstart |
| [INSTALL.de.md](INSTALL.de.md) | Installation |
| [DEPLOYMENT.de.md](DEPLOYMENT.de.md) | Deployment mit Docker und Kubernetes |
| [docs/API.de.md](docs/API.de.md) | Endpunkte, Anfragen und Fehlercodes |
| [UEBERGABE.md](UEBERGABE.md) | Übergabe an den Betrieb |
| [CHANGELOG.md](CHANGELOG.md) | Änderungen je Version |
| [docs/steckbrief.md](docs/steckbrief.md) | Steckbrief des Dienstes |
| [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md) | Kurzinfo zu B-API-Routing und B-API-Authentifizierung |
| [helm/metadata-agent/README.md](helm/metadata-agent/README.md) | Helm-Chart |
