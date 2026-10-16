# Umgebungsvariablen — vollständige Referenz

Alle Variablen, die `src/config.py` liest. Die Liste wird von
`tests/test_env_parameters.py` gegen das Settings-Modell geprüft — eine neue
Einstellung ohne Eintrag hier lässt den Test fehlschlagen.

**Zwei Namensschemata:** Secrets tragen ihren Namen unverändert
(`B_API_KEY`, `OPENAI_API_KEY`, `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`),
alles andere das Präfix `METADATA_AGENT_`.

Unbekannte Variablen werden ignoriert (`extra: "ignore"`) — eine alte `.env`
bricht nichts, ein Tippfehler fällt aber auch nicht auf.

---

## Secrets

| Variable | Default | Benötigt für |
|---|---|---|
| `B_API_KEY` | — | `llm_provider=b-api-openai`, `b-api-academiccloud` oder `b-api-router`; im Modus `auto` nur noch als Rückfall, im Modus `forward` gar nicht (siehe [B-API: Authentifizierung](#b-api-authentifizierung)) |
| `OPENAI_API_KEY` | — | `llm_provider=openai` |
| `WLO_GUEST_USERNAME` | — | `/upload` |
| `WLO_GUEST_PASSWORD` | — | `/upload` |

---

## LLM: Providerwahl

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | `b-api-openai`, `b-api-academiccloud`, `b-api-router` oder `openai` (ohne B-API) |

Pro Request überschreibbar: `llm_provider` und `llm_model` im Body von
`/generate`, `/detect-content-type` und `/extract-field`.

## B-API: Authentifizierung

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` | Welcher Schlüssel die KI-Aufrufe an die B-API trägt: `auto`, `forward` oder `static` |
| `METADATA_AGENT_DEBUG_ENDPOINTS` | `false` | Schaltet `GET /debug/auth` (kommt ein Schlüssel an, maskiert) und `GET /debug/dependencies` (sind B-API, Textextraktion und Repository erreichbar, wird der Schlüssel angenommen) frei |

**Es muss nichts gesetzt werden.** Der Default `auto` erkennt selbst, ob die
B-API einen Schlüssel mitschickt, und nimmt sonst `B_API_KEY` – ein bestehendes
Deployment mit `B_API_KEY` läuft unverändert, und sobald der Proxy mit
`forwardApiKey` davor sitzt, wird automatisch dessen Schlüssel genutzt. Setzen
muss man den Modus nur in zwei Fällen: `static`, wenn ein Proxy einen
`Authorization`-Header mitschickt, der kein B-API-Schlüssel ist; `forward`,
wenn ausschließlich über den Proxy abgerechnet werden soll (dann ohne
`B_API_KEY`).

Seit 2026-10-13 kann die B-API einen angebundenen Dienst über
`/api/v1/proxy/<dienst>/…` aufrufen und dabei den Schlüssel des Aufrufers als
`Authorization: Bearer <key>` mitgeben (Admin-Option „API-Key des Aufrufers
weiterreichen“, `forwardApiKey`). edu-sharing wählt den Schlüssel – einen für
eingeloggte Nutzer, einen für Gäste – und die B-API bucht Nutzung, Kosten und
Limits auf ihn. Der bisherige Weg, ein eigener `B_API_KEY` als `X-API-KEY`,
bleibt erhalten:

| Modus | Eingehender `Authorization: Bearer …` | Kein Header |
|---|---|---|
| `auto` | wird unverändert weitergereicht | `B_API_KEY` als `X-API-KEY` (wie bisher) |
| `forward` | wird unverändert weitergereicht | `502` mit Code `LLM_AUTH_MISSING`, kein Rückfall |
| `static` | wird ignoriert | `B_API_KEY` als `X-API-KEY` |

- Gelesen wird nur `Authorization` mit Schema `Bearer`; `X-API-KEY` kommt über
  den Proxy nicht an. Der Wert geht **unverändert** und als **einziger**
  Auth-Header an die B-API – ein gültiger Bearer neben einem ungültigen
  `X-API-KEY` wäre dort ein `401`.
- Der Schlüssel gilt nur für die Anfrage, die ihn brachte: Er wird nicht
  gespeichert, nicht geloggt und nicht zwischen gleichzeitigen Anfragen geteilt.
- Gilt nur für `b-api-openai` und `b-api-academiccloud`. Bei `openai` (nativ)
  zählt weiter `OPENAI_API_KEY`.
- In der Antwort von `/generate` steht unter `processing.auth_source`, woher der
  Schlüssel kam (`forwarded`, `static`) – nie der Schlüssel selbst.
- Der Dienst prüft den Header nicht selbst. Er darf daher **nur über die B-API**
  erreichbar sein (Netzwerkregel); sonst kann jeder mit seinem eigenen
  B-API-Schlüssel Aufrufe auf dessen Kosten stellen – und ohne Header läuft im
  Modus `auto` der eigene `B_API_KEY`.

Zwei Diagnose-Endpunkte gibt es nur mit `METADATA_AGENT_DEBUG_ENDPOINTS=true`;
danach wieder ausschalten. Über den B-API-Proxy müssen ihre Pfade dort
freigegeben sein. Am 14.10.2026 lehnte der Proxy `/debug/auth` noch mit `403`
ab, bevor die Anfrage den Agenten erreichte.

`GET /debug/auth` zeigt beim Einrichten des Proxys, was ankommt – nur
Vorhandensein, Schema und Länge:

```json
{"mode": "auto", "Authorization": {"present": true, "scheme": "Bearer", "length": 36},
 "X-API-KEY": {"present": false}, "b_api_key_configured": true, "llm_calls_use": "forwarded"}
```

`GET /debug/dependencies` prüft, ob die Abhängigkeiten erreichbar sind und ob
die B-API den Schlüssel annimmt.

- **KI-Dienst:** Der Agent fragt die Modellliste ab (`GET <LLM-Basis>/models`),
  mit dem Schlüssel, den die KI-Aufrufe dieser Anfrage tragen würden. Das
  verbraucht keine Tokens und wird genauso abgelehnt wie eine Generierung.
- **Textextraktion und Repository:** Sie gelten als erreichbar, sobald
  irgendeine HTTP-Antwort kommt, gleich mit welchem Status. Zeitlimit: 5 s.
- **Antwort:** Sie nennt Adressen, Status, Dauer und die Art des Fehlers, nie
  einen Schlüssel.

```json
{"version": "2.1.0", "llm_provider": "b-api-openai", "b_api_auth_mode": "auto",
 "config_warnings": [],
 "checks": {
   "llm": {"url": "https://b-api.prod.openeduhub.net/api/v1/llm/openai/models",
           "reachable": true, "status": 403, "ms": 84,
           "auth_source": "forwarded", "authorized": false},
   "text_extraction": {"url": "http://bildungs-api-services-full-text.b-api",
                       "reachable": false, "error": "ConnectTimeout", "ms": 5003},
   "repository": {"url": "https://redaktion.openeduhub.net/edu-sharing/rest",
                  "reachable": true, "status": 404, "ms": 41}}}
```

Beim Start meldet der Agent unpassende Kombinationen, etwa `static` ohne
`B_API_KEY` oder Adressen, die teils auf Staging, teils auf Produktion zeigen.

## LLM: Durchsatzgrenzen

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` | providerabhängig | Gleichzeitige Requests. Leer = Default des Providers |
| `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` | providerabhängig | Requests pro Sekunde. `0` schaltet die Ratenbegrenzung ab |

Jeder Provider hat ein eigenes Budget mit diesen Defaults:

| Provider | gleichzeitig | pro Sekunde |
|---|---:|---:|
| `b-api-openai` | 20 | keine Grenze |
| `b-api-academiccloud` | **2** | **2** |
| `b-api-router` | 20 | keine Grenze (passend zum Default `openai/gpt-6-luna`; für eine Route aus AcademicCloud-Modellen auf 2 / 2 senken) |
| `openai` (nativ) | 20 | keine Grenze |

Beide Grenzen gelten **prozessweit**, nicht pro Request, und für jeden Provider
getrennt. Gesetzte Werte überschreiben den Default für **jeden** Provider — ein
Wert über 2 hebt also auch AcademicCloud über seine gemessene Grenze.

`b-api-academiccloud` (gemessen 2026-08-12): ein dritter paralleler Request wird
sofort mit `429` abgewiesen, und zwar **ohne `retry-after`**: es gibt nichts,
woran ein Client die Wartezeit ablesen könnte, er muss unter der Grenze bleiben.
Mehr Last hilft nicht — bei 3 req/s fällt der effektive Durchsatz *unter* den
Wert bei 2 req/s.

`b-api-openai`: Die Messung vom August hatte noch ein gemeinsames Budget je Key
für beide B-API-Provider ergeben. Ein echter `/generate`-Lauf am 2026-10-06 mit
`gpt-6-luna` und 20 parallelen Requests lief ohne `429` durch. Antwortet das
Gateway doch mit `429`, `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` senken.

## LLM: Modelle und Endpunkte

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_B_API_BASE_URL` | `https://b-api.staging.openeduhub.net` | Basis; die Endpunktpfade (OpenAI, AcademicCloud, Router) werden daraus abgeleitet |
| `METADATA_AGENT_B_API_OPENAI_BASE` | abgeleitet | Nur setzen, wenn der Pfad vom Schema abweicht |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-6-luna` | Modell für `b-api-openai` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | abgeleitet | wie oben |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-v4-flash` | Modell für `b-api-academiccloud` |
| `METADATA_AGENT_B_API_ROUTER_BASE` | abgeleitet (`…/api/v1/llm/router`) | wie oben |
| `METADATA_AGENT_B_API_ROUTER_MODEL` | `openai/gpt-6-luna` | Routenname oder `provider/modell` für `b-api-router` (siehe [B-API: Routing](#b-api-routing)) |
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` | Beliebiger OpenAI-kompatibler Endpunkt |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` | Modell für `openai` |

`METADATA_AGENT_OPENAI_API_BASE` muss nicht auf OpenAI zeigen. Alles, was
`/chat/completions` bedient und einen Bearer-Token annimmt, funktioniert —
Azure-Deployment, selbst gehostetes vLLM, ein Gateway davor. Prod-Basis der
B-API: `https://b-api.prod.openeduhub.net`.

## B-API: Routing

Mit `METADATA_AGENT_LLM_PROVIDER=b-api-router` gehen die KI-Aufrufe an
`/api/v1/llm/router/chat/completions`. Das Feld `model` trägt dann einen
Routennamen; die B-API wählt nach Priorität und Gewicht ein Modell der Route
und weicht bei Fehlern auf das nächste aus. Die übrigen Provider bleiben, wie
sie sind; ohne B-API funktioniert weiter `openai`.

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_B_API_ROUTER_MODEL` | `openai/gpt-6-luna` | Routenname, oder `provider/modell` (geht ohne Route) |
| `METADATA_AGENT_LLM_REQUEST_STYLE` | `auto` | Parameterfamilie des Request-Bodys: `auto`, `reasoning` oder `classic` |
| `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT` | leer | Nur beim Router mit `classic`: sendet `reasoning_effort` (`none`, `low`, `medium`, `high`). Denkende Modelle wie `qwen3.6-35b-a3b` brauchen `none`, sonst liefern sie keinen Text. Leer: nicht senden; ältere Modelle wie `gpt-4o` lehnen den Parameter ab |
| `METADATA_AGENT_B_API_IGNORE_CACHE` | `false` | `true` hängt `?ignore-caching=true` an jeden B-API-Aufruf |

### Wo Routen definiert werden

**Routen stehen nicht in der Env, sondern in der B-API.** Dort legt ein
Administrator eine globale Route an (Oberfläche „LLM-Routing“ oder
`POST /administration/llm-routing/groups`). Ein Konto mit `LLM_ROUTE_MANAGE`
kann eine eigene Route anlegen (`POST /api/v1/llm/router/routes`). Die Route
bestimmt Modelle, Priorität und Gewicht. Der Agent nennt in der Env nur ihren
Namen und die Parameterfamilie ihrer Modelle. Ein Modelltausch in der Route
braucht daher kein neues Deployment.

**Muster 1 – ein einzelnes Modell, ohne Route** (`provider/modell`, die B-API
löst das ohne Route auf; kein Ausweichen auf andere Modelle):

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=academiccloud/qwen3-30b-a3b-instruct-2507
METADATA_AGENT_LLM_REQUEST_STYLE=classic
# AcademicCloud-Modelle teilen sich das GWDG-Kontingent: Durchsatz begrenzen
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=2
```

**Muster 2 – eine Route mit Reserve.** Zuerst legt der Administrator die Route
in der B-API an, mit Modellen **einer** Familie und `maxAttempts` mindestens 2.
Das Beispiel zeigt nur den Aufbau:

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

> **Denkende Modelle brauchen `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none`.**
> Ohne diese Einstellung denkt `qwen3.6-35b-a3b` zuerst nach, verbraucht das
> Token-Budget und liefert keinen Text; der Agent meldet das als
> `LLM_EMPTY_ANSWER`. Mit `none` antwortet es direkt. Instruct-Modelle wie
> `qwen3-30b-a3b-instruct-2507` vertragen den Parameter ebenfalls. Für
> `qwen3.8-27b` ist das noch nicht im Agenten geprüft. Ergebnisse des
> Kurztests: [UEBERGABE.md](UEBERGABE.md#3-b-api).

Dann setzt man im Agenten den Namen der Route:

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=metadata-agent
METADATA_AGENT_LLM_REQUEST_STYLE=classic
METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=2
```

Eine Route nur aus GPT-5/6-Modellen braucht `METADATA_AGENT_LLM_REQUEST_STYLE=reasoning`
und keine Drosselung. Ob die B-API die Route kennt, zeigt
`GET /debug/dependencies` (`route_listed`). Beim Ändern der Route „Cache
leeren“ wählen.

### Was der Agent dabei beachtet

- **Umstieg ohne Ausfall:** Der Default `openai/gpt-6-luna` funktioniert ohne
  angelegte Route und entspricht dem bisherigen Modell. Erst die Route anlegen,
  dann `METADATA_AGENT_B_API_ROUTER_MODEL` auf ihren Namen setzen. Ein
  unbekannter Name scheitert sofort mit `LLM_ROUTE_NOT_FOUND`.
- **Parameterfamilie:** Der Router reicht den Body unverändert an jedes Modell
  der Route weiter. GPT-5/6 und o-Modelle verlangen `max_completion_tokens` und
  keine eigene `temperature` (`reasoning`), alle anderen `max_tokens`
  (`classic`). `auto` erkennt die Familie am Modellnamen, auch hinter
  `openai/`. Ein reiner Routenname verrät sie nicht: Der Agent sendet dann
  `classic` und warnt beim Start. Eine Route darf daher nur Modelle einer
  Familie enthalten. Passt der Body nicht, endet die Erschließung sofort mit
  `LLM_ROUTE_MISCONFIGURED`. `METADATA_AGENT_LLM_REQUEST_STYLE` gilt nur für
  `b-api-router`; die übrigen Provider erkennen die Familie immer am
  Modellnamen, auch wenn eine Anfrage sie per `llm_provider` wählt.
- **Fehler:** Dauerhafte Fehler werden nicht wiederholt und beenden die
  Erschließung sofort:
  - unbekannte oder abgeschaltete Route → `LLM_ROUTE_NOT_FOUND`;
  - Modell ohne Preis, für den Endpunkt ungeeignet oder falsche
    Parameterfamilie → `LLM_ROUTE_MISCONFIGURED`.

  Die B-API meldet einige davon als `503`, deshalb prüft der Agent den
  Fehlertext. Wiederholt werden nur:
  - Drosselung durch GWDG (`503 … RATE_LIMITED`); bleibt sie, endet die
    Erschließung mit `LLM_RATE_LIMITED`;
  - eine Route ohne freies Modell (`no deployment left`); bleibt sie ohne
    freies Modell, endet die Erschließung mit `LLM_UNREACHABLE`.
- **Cache:** Wortgleiche Anfragen beantwortet die B-API aus ihrem Cache, ohne
  das zu kennzeichnen. Jede Route hat einen eigenen Cache, und Änderungen an
  der Route leeren ihn nicht. Nach einem Modelltausch in der Route daher
  „Cache leeren“ wählen. Mit `METADATA_AGENT_B_API_IGNORE_CACHE=true` fragt
  der Agent immer neu, das kostet aber jedes Mal.
- **Durchsatz:** Der Agent sieht nicht, welche Modelle hinter einer Route
  stehen; siehe [LLM: Durchsatzgrenzen](#llm-durchsatzgrenzen).
- `GET /debug/dependencies` meldet bei einem Routennamen zusätzlich
  `route_listed`, also ob die B-API die Route in `router/models` führt.

## LLM: Generierung

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_LLM_TEMPERATURE` | `0.3` | Gilt für alle drei Provider |
| `METADATA_AGENT_OPENAI_TEMPERATURE` | folgt `LLM_TEMPERATURE` | Nur nötig, wenn nativ OpenAI abweichen soll |
| `METADATA_AGENT_LLM_MAX_TOKENS` | `2000` | Bei Reasoning-Modellen als `max_completion_tokens` gesendet |
| `METADATA_AGENT_LLM_VERBOSITY` | `low` | Nur GPT-5/GPT-6 und o-Modelle. Leer = nicht senden |
| `METADATA_AGENT_LLM_REASONING_EFFORT` | `low` | Nur GPT-5/GPT-6 und o-Modelle. `none`/`low`/`medium`/`high` |
| `METADATA_AGENT_LLM_MAX_RETRIES` | `3` | Versuche bei `429`/`5xx` und Verbindungsfehlern. `401`/`403`/`404` werden nie wiederholt und beenden eine Erschließung sofort |
| `METADATA_AGENT_LLM_RETRY_DELAY` | `1.0` | Basiswert; der Abstand wächst linear mit dem Versuch |

`VERBOSITY` und `REASONING_EFFORT` werden nur an Modelle mit den Präfixen
`gpt-5`, `gpt-6`, `o1`, `o3`, `o4` gesendet — ältere Modelle antworten darauf mit `400`.
Dieselbe Erkennung stellt bei diesen Modellen `max_tokens` auf
`max_completion_tokens` um und lässt `temperature` weg. Das gilt providerunabhängig,
also auch bei nativem OpenAI.

## Worker und Zeitlimits

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `20` | Breite des parallelen Feld-Fan-outs; gilt für `/generate` ohne `max_workers` (auf 1–20 begrenzt) |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `90` | Timeout je KI-Aufruf in Sekunden (Lesezeit; Verbindungsaufbau separat 10 s). Bleibt unter dem Ingress-Timeout von 120 s für ein ganzes `/generate` |

Ein `max_workers` im Request hat Vorrang. Beides wird auf die Grenze des
Providers gedeckelt (gesetztes `LLM_MAX_CONCURRENT_REQUESTS`, sonst sein Default). Bei
AcademicCloud laufen also 2 Worker, egal was hier steht — mehr zu fordern
erzeugt nur Wartende. Beim Start wird das ausgewiesen:

```
Default Workers: 20 → 2 (limit of b-api-academiccloud)
LLM Throughput: max 2 in flight, max 2 req/s
```

## Schemata

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_DEFAULT_CONTEXT` | `default` | `default` oder `mds_oeh` |

Die Endpunkte selbst nehmen `version` aus dem Request, Default `latest`, und das
löst über `isDefault` im Manifest auf **2.0.0** auf.

## Normalisierung

| Variable | Default | Beschreibung |
|---|---|---|

## Repository und Eingabequellen

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_REPOSITORY_URL` | `https://repository.staging.openeduhub.net/edu-sharing/rest` | Ziel für Abruf **und** Upload |
| `METADATA_AGENT_WLO_INBOX_ID` | `21144164-30c0-4c01-ae16-264452197063` | Ordner, in dem neue Knoten entstehen |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | `https://text-extraction.staging.openeduhub.net` | |

Prod-Repository: `https://redaktion.openeduhub.net/edu-sharing/rest`.
Es gibt **keinen** zweiten Schalter für das Ziel-Repository — der
`repository`-Parameter im Request wird ignoriert.

## Screenshots

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_SCREENSHOT_METHOD` | `playwright` | Immer lokal (Chromium im Container). `pageshot` wird als Legacy-Alias angenommen und nimmt ebenfalls lokal auf |
| `METADATA_AGENT_SCREENSHOT_WIDTH` | `1920` | |
| `METADATA_AGENT_SCREENSHOT_HEIGHT` | `1080` | |
| `METADATA_AGENT_SCREENSHOT_FORMAT` | `png` | `png` oder `jpeg`; andere Werte (z.B. `webp`) werden als PNG geliefert |
| `METADATA_AGENT_SCREENSHOT_FULL_PAGE` | `false` | |
| `METADATA_AGENT_SCREENSHOT_DELAY` | `2000` | Millisekunden vor der Aufnahme |

Screenshots brauchen Chromium; ohne Chromium gibt es keine Screenshots — einen
externen Screenshot-Dienst nutzt der Agent nicht mehr. Aufgenommen werden nur
öffentliche http(s)-Ziele; die Prüfung löst DNS im Container auf, ein Hostname
mit privater Adresse (z.B. Split-Horizon-DNS im Cluster) wird abgelehnt.

## Anwendung

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_APP_NAME` | `Metadata Agent API` | |
| `METADATA_AGENT_APP_VERSION` | `2.1.0` | |
| `METADATA_AGENT_CORS_ORIGINS` | `*` | Komma-getrennt oder `*` |
| `METADATA_AGENT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` oder `ERROR` |
| `METADATA_AGENT_LOG_FORMAT` | `text` | `text`: nur die Meldung, wie bisher. `json`: ein Objekt je Zeile mit `time`, `level`, `logger`, `message` und `trace_id`; auch für die Server- und Zugriffslogs von uvicorn |
| `METADATA_AGENT_INTERNAL_PORT` | leer | Zweiter, interner Port mit `/health`, `/ready` und `/metrics` (Prometheus). Leer: kein zweiter Server, kein `/metrics`. Nur mit **einem Worker je Container**. Ist der Port belegt, meldet der Agent das im Log und läuft ohne internen Port weiter |
| `METADATA_AGENT_MAX_REQUEST_BYTES` | `20971520` (20 MB) | Größter angenommener Request-Body in Bytes. Größere Anfragen beantwortet der Agent mit `413`, bevor er sie liest. Platz genug für `/upload` mit Vorschaubild als Data-URL |

Logs gehen immer nach stdout. Eingehende Tracing-Header – `x-client-trace-id`,
`x-request-id`, die B3-Header `x-b3-*` und `b3` – gibt der Agent unverändert an
die eigenen Dienste weiter (B-API, Textextraktion, Repository), nie an
Fremddienste wie den Geocoder. Als `trace_id` und Anfrage-ID gilt
`x-client-trace-id`, sonst `x-request-id`, sonst eine erzeugte ID.

---

## Konfiguration je Betriebsart

**Hinter dem B-API-Proxy** (edu-sharing → B-API `/api/v1/proxy/metadata-agent-canvas` → Agent),
Nutzer- und Gastschlüssel werden durchgereicht:

```env
METADATA_AGENT_LLM_PROVIDER=b-api-openai
METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
# auto: durchgereichter Schlüssel, sonst B_API_KEY für Direktaufrufe und Tests.
# forward: nur durchgereichte Schlüssel - Direktaufrufe ohne Header scheitern mit 502 (LLM_AUTH_MISSING).
METADATA_AGENT_B_API_AUTH_MODE=auto
B_API_KEY=...
```

In der B-API muss der Dienst mit gesetztem `forwardApiKey` eingetragen sein.

**B-API AcademicCloud** — die Variante mit den engsten Grenzen:

```env
METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
B_API_KEY=...
METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL=deepseek-v4-flash
# Die Defaults 2/2 passen bereits — hier nur zur Verdeutlichung.
# Die Rate ist pro Sekunde, nicht pro Minute: 2 = 120 Aufrufe/Minute.
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=2
```

Gemessen: eine Erschließung mit 50 Feldern schwankt damit zwischen **32 s und
90 s** — die Streuung kommt von der Warteschlange am Gateway, nicht vom Modell.
`b-api-openai` mit `gpt-5.6-luna` lag im Vergleichslauf bei 25,2 s; mit dem
heutigen Standard `gpt-6-luna` dauert eine Erschließung 12–22 s (gemessen
16.10.2026).

**Nativ OpenAI, ohne Ratenbegrenzung:**

```env
METADATA_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
METADATA_AGENT_OPENAI_MODEL=gpt-4o-mini
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=20
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0
```

**Eigenes OpenAI-kompatibles Gateway:**

```env
METADATA_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=egal-was-das-gateway-erwartet
METADATA_AGENT_OPENAI_API_BASE=https://mein-gateway.example/v1
METADATA_AGENT_OPENAI_MODEL=lokales-modell
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=4
```
