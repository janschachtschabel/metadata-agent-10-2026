# Metadata Agent API – API-Referenz

[English](API.md) · **Deutsch**

Referenz für alle, die die Metadata Agent API aufrufen (Dienst-ID
`metadata-agent`, Version 4.0.4). Was sich für Aufrufer seit dem vorigen
Release geändert hat, steht unter
[Änderungen seit Release 3.0.1](#änderungen-seit-release-301).

Dieselben Angaben liefert der Agent interaktiv: `/docs` (Swagger UI) und
`/openapi.json`. Wo diese Referenz eine Feldliste kürzt, steht unter `/docs`
jedes Feld.

Verwandte Dokumente: [README](../README.de.md) (Überblick),
[INSTALL](../INSTALL.de.md), [DEPLOYMENT](../DEPLOYMENT.de.md),
[ENV-PARAMETER](../ENV-PARAMETER.de.md) (alle Einstellungen),
[CHANGELOG](../CHANGELOG.md), [steckbrief.md](steckbrief.md) (Steckbrief des
Diensts).

## Inhalt

- [Konventionen](#konventionen)
  - [Basis-URL und Proxy-Pfad](#basis-url-und-proxy-pfad)
  - [Authentifizierung](#authentifizierung)
  - [Anfrage-IDs und Tracing](#anfrage-ids-und-tracing)
  - [Fehler](#fehler)
  - [Grenzen und Zeitlimits](#grenzen-und-zeitlimits)
  - [Änderungen seit Release 3.0.1](#änderungen-seit-release-301)
- [Gemeinsame Bausteine](#gemeinsame-bausteine)
  - [Eingabequelle](#eingabequelle)
  - [Nur öffentliche URLs](#nur-öffentliche-urls)
  - [Provider und Modell je Anfrage](#provider-und-modell-je-anfrage)
  - [Schemata und Inhaltstypen](#schemata-und-inhaltstypen)
  - [Metadaten-Body](#metadaten-body)
- [Erschließung](#erschließung)
  - [POST /generate](#post-generate)
  - [POST /extract-field](#post-extract-field)
  - [POST /detect-content-type](#post-detect-content-type)
- [Validierung und Export](#validierung-und-export)
  - [POST /validate](#post-validate)
  - [POST /export/markdown](#post-exportmarkdown)
- [Repository](#repository)
  - [Gemeinsame Regeln beim Schreiben](#gemeinsame-regeln-beim-schreiben)
  - [POST /node](#post-node)
  - [POST /nodes](#post-nodes)
  - [POST /upload](#post-upload)
  - [POST /workflow/{node_id}](#post-workflownode_id)
  - [POST /upload/verify/{node_id}](#post-uploadverifynode_id)
- [Screenshot](#screenshot)
  - [POST /screenshot](#post-screenshot)
- [Schema-Informationen](#schema-informationen)
- [Endpunkte der Webkomponente](#endpunkte-der-webkomponente)
- [Betriebsendpunkte](#betriebsendpunkte)
- [Nutzungsbeispiele](#nutzungsbeispiele)

Alle Operationen auf einen Blick:

| Methode und Pfad | Zweck | In `/openapi.json` |
|---|---|---|
| `POST /generate` | Einen vollständigen Metadatensatz erschließen | ja |
| `POST /extract-field` | Ein einzelnes Feld erschließen | ja |
| `POST /detect-content-type` | Den Inhaltstyp eines Textes erkennen | ja |
| `POST /validate` | Einen Datensatz gegen sein Schema prüfen | ja |
| `POST /export/markdown` | Einen Datensatz als Markdown ausgeben | ja |
| `POST /node` | Einen Node im Repository anlegen, ohne Metadaten | ja |
| `POST /nodes` | Einen Node anlegen oder aktualisieren, vollständigen Node zurückgeben | ja |
| `POST /upload` | Einen Datensatz hochladen: Dublettenprüfung, Vorschau, Workflow | ja |
| `POST /workflow/{node_id}` | Workflow-Status eines Nodes setzen | ja |
| `POST /upload/verify/{node_id}` | Einen Node mit dem Gesendeten vergleichen | ja |
| `POST /screenshot` | Eine Webseite aufnehmen | ja |
| `GET /info/schemata` | Kontexte und Versionen | ja |
| `GET /info/schemas/{context}/{version}` | Schemata einer Version | ja |
| `GET /info/schema/{context}/{version}/{schema_file}` | Eine Schemadefinition | ja |
| `GET /widget/info` | Einbindung der Webkomponente | ja |
| `GET /widget/i18n/{lang}.json` | Oberflächentexte der Webkomponente | ja |
| `GET /widget/dist/…`, `GET /widget/examples/…` | Dateien der Webkomponente | nein (statisch) |
| `GET /docs`, `GET /docs-assets/…`, `GET /openapi.json` | Interaktive Dokumentation | nein |
| `GET /health`, `GET /ready` | Liveness und Readiness | nein |
| `GET /metrics` | Prometheus-Metriken, nur auf dem internen Port | nein |
| `GET /debug/auth`, `GET /debug/dependencies` | Prüfungen beim Einrichten, standardmäßig aus | nein |

## Konventionen

### Basis-URL und Proxy-Pfad

| Weg | Basis-URL |
|---|---|
| Direkt (lokal, im Cluster) | `http://<host>:8000` |
| Über den B-API-Proxy | `https://<b-api-host>/api/v1/proxy/metadata-agent-canvas` |

Die Pfade dieses Dokuments folgen unverändert auf die Basis-URL, zum Beispiel
`https://b-api.staging.openeduhub.net/api/v1/proxy/metadata-agent-canvas/generate`.
Der Proxy-Eintrag der B-API heißt `metadata-agent-canvas`; der Dienst selbst
heißt `metadata-agent`. Die Beispiele nutzen eine Shell-Variable:

```bash
API=http://localhost:8000
```

Request-Bodys sind JSON (`Content-Type: application/json`); `/generate` nimmt
auch `text/plain` an. Antworten sind JSON, ab 500 Bytes mit gzip komprimiert.
Andere Origins dürfen die API aufrufen (CORS, `METADATA_AGENT_CORS_ORIGINS`,
Standard `*`) und die Antwort-Header `X-Request-ID` und `Retry-After` lesen.
Kein Pfad endet auf einen Schrägstrich, und ein angehängter Schrägstrich wird
nicht umgeleitet: `/generate/` wird mit `404` beantwortet.

`/docs` zeigt zu jeder Operation eine Beispielantwort. Es ist das JSON, das
diese Referenz nach „Antwort `200`“ zeigt; `/generate` und `/upload` haben je
zwei benannte Beispiele (vollständiges Ergebnis und Teilergebnis, geschriebener
Datensatz und Dublette). Die Beispiele lassen optionale Felder ohne Wert weg,
die die Antwort selbst als `null` liefert, und kürzen lange Listen auf wenige
Einträge.

### Authentifizierung

Der Agent prüft keine Zugangsdaten seiner Aufrufer. Er ist für den Betrieb
hinter dem B-API-Proxy gedacht, der den Schlüssel des Aufrufers prüft, und darf
von außen auf keinem anderen Weg erreichbar sein. Siehe
[DEPLOYMENT](../DEPLOYMENT.de.md) und [b-api-kurzinfo.md](b-api-kurzinfo.md).

Welchen Schlüssel der Agent für seine eigenen Aufrufe nutzt:

| Aufruf des Agents | Schlüssel | Gesendet als |
|---|---|---|
| LLM, Textextraktion und Geocoder über die B-API | Die Anmeldung des Aufrufers, wenn der Proxy sie durchreicht (Schlüssel per `forwardApiKey` oder Delegations-Token per `forwardDelegation`), sonst der `B_API_KEY` des Agents. Es entscheidet `METADATA_AGENT_B_API_AUTH_MODE`, siehe unten | `Authorization: Bearer <key>` (durchgereicht, unverändert) oder `X-API-KEY` (eigener Schlüssel) |
| LLM mit `llm_provider=openai` | Der `OPENAI_API_KEY` des Agents | `Authorization: Bearer` |
| Repository (edu-sharing), lesend und schreibend | Dienstkonto `WLO_GUEST_USERNAME` / `WLO_GUEST_PASSWORD` | Basic-Authentifizierung |

`METADATA_AGENT_B_API_AUTH_MODE`:

| Modus | Anfrage mit `Authorization: Bearer …` | Anfrage ohne |
|---|---|---|
| `auto` (Standard) | Der Header geht an die B-API weiter | Der `B_API_KEY` des Agents |
| `forward` | Der Header geht weiter | `502` `LLM_AUTH_MISSING` |
| `static` | Der Header wird ignoriert, es gilt `B_API_KEY` | Der `B_API_KEY` des Agents |

Was ein Aufrufer wissen muss:

- Soll der Agent bei einem direkten Aufruf Ihren Schlüssel nutzen, senden Sie
  `Authorization: Bearer <B-API-Schlüssel>`. Andere Schemata (etwa `Basic`) und
  ein eingehender Header `X-API-KEY` werden ignoriert.
- Der Schlüssel braucht die Rechte für die LLM-Endpunkte der B-API und für die
  Proxy-Dienste `text-extraction` und `photon_geocoding`. Ohne das erste
  scheitert die Erschließung mit `LLM_AUTH_FORBIDDEN`. Ohne das zweite oder
  dritte versucht der Agent mit `METADATA_AGENT_B_API_SERVICES=auto` seinen
  eigenen `B_API_KEY` und dann den Dienst direkt (Textextraktion der
  Repository-Umgebung, Photon). Scheitern alle Wege, endet die Erschließung
  aus einer URL mit `TEXT_EXTRACTION_AUTH_REFUSED`; mit `proxy` bleiben Orte ohne
  Koordinaten, und `processing.warnings` sagt das.
- Der Agent antwortet nie mit `401` oder `403`, wenn ein nachgelagerter Dienst
  einen Schlüssel ablehnt. Er antwortet mit `502` und einem Fehlercode, weil
  edu-sharing ein `401` oder `403` als abgelaufene Sitzung des Nutzers läse.
- `processing.auth_source` in der Antwort von `/generate` nennt die Herkunft
  des Schlüssels (`forwarded`, `static`, `openai`), nie den Schlüssel.
- Repository: Ohne das Dienstkonto werden die Quellen `node_id` und `node_url`
  anonym gelesen (nur öffentliche Nodes werden gefunden), und jede schreibende
  Operation antwortet mit `503`. Geschriebenes steht in der Historie des Nodes
  unter dem Dienstkonto.

### Anfrage-IDs und Tracing

Jede Antwort trägt den Header `X-Request-ID`. Fehlerantworten wiederholen den
Wert als `error.request_id`, und jede Logzeile der Anfrage trägt ihn. Nennen
Sie ihn, wenn Sie ein Problem melden.

| Eingehender Header | Wirkung |
|---|---|
| `x-client-trace-id` | Wird zur Anfrage-ID (erste Wahl) und zum Logfeld `client_trace_id` |
| `X-Request-ID` | Wird zur Anfrage-ID, wenn `x-client-trace-id` fehlt |
| `x-b3-traceid`, `x-b3-spanid`, `x-b3-parentspanid`, `x-b3-sampled`, `x-b3-flags`, `b3` | B3-Trace-Kontext. Die Trace-ID wird zur Anfrage-ID, wenn keiner der beiden Header darüber vorhanden ist |
| keiner davon | Der Agent beginnt eine neue Trace. Die Anfrage-ID ist ihre Trace-ID (32 Hex-Zeichen) |

Regeln:

- Eine eingehende ID gilt nur, wenn sie ein einfaches Token ist: Buchstaben,
  Ziffern, `.`, `_`, `-`, höchstens 64 Zeichen. Alles andere wird ignoriert.
- Eine B3-Trace-ID braucht 16 oder 32 Hex-Zeichen, eine Span-ID 16. Andere
  Werte beginnen eine neue Trace.
- Der Agent gibt die Tracing-Header an B-API, Textextraktion und Repository
  weiter. Ein `X-Request-ID`, das kein solches Token ist, geht nicht weiter;
  an seine Stelle tritt die Anfrage-ID der Antwort. Eigene Spans meldet der
  Agent nicht.

### Fehler

Jede Fehlerantwort jedes Endpunkts hat dieselbe Form (`ErrorResponse` in der
OpenAPI-Spezifikation):

```json
{
  "detail": "Schema not found: nope.json (default, version 2.0.0)",
  "error": {
    "code": "NOT_FOUND",
    "message": "Schema not found: nope.json (default, version 2.0.0)",
    "request_id": "d8102e2566914e85a8b363a942574d6c"
  }
}
```

| Teil | Bedeutung |
|---|---|
| `detail` | Text für Menschen. Bei `422` von `/extract-field`, `/detect-content-type`, `/node` und `/screenshot` und bei einem zu langen Pfadparameter die Liste der Verstöße, je Eintrag nur `loc`, `msg` und `type` (die abgelehnte Eingabe wird nicht wiederholt); bei den anderen Endpunkten ein String, der jedes abgelehnte Feld nennt |
| `error.code` | Stabiler Code für Programme, siehe die Tabellen unten |
| `error.message` | Kurzer Text |
| `error.request_id` | ID der Anfrage, gleich dem Header `X-Request-ID` |
| `error.upstream_status`, `error.auth_source` | Nur bei `LLM_*`-Codes: HTTP-Status des KI-Diensts und Herkunft des Schlüssels |
| weitere Schlüssel neben `error` | Nur wo eine Operation es sagt: `processing` (gescheitertes `/generate`), `node`, `discarded_node`, `collections`, `step`, `field_errors` (Schreiben) |

Die Texte in `detail` und `error.message` sind überwiegend deutsch und können
sich ändern. Programme werten `error.code` aus.

Allgemeine Codes:

| Status | `error.code` | Bedeutung |
|---|---|---|
| `400` | `BAD_REQUEST` | Ungültiges JSON (auch `NaN` und `Infinity`) oder ein Body, der kein JSON-Objekt ist, bei jedem Endpunkt mit Body; nötige Eingabe fehlt; URL nicht öffentlich; `node_id` im Pfad ist keine UUID |
| `403` | `FORBIDDEN` | Der Node liegt außerhalb seines Bearbeitungsfensters |
| `403` | `SCREENSHOT_BROWSER_REFUSED` | Die Anfrage kam vom Screenshot-Browser des Agents selbst |
| `404` | `NOT_FOUND` | Pfad, Kontext, Version, Schema, Feld oder Node unbekannt |
| `405` | `METHOD_NOT_ALLOWED` | Falsche HTTP-Methode |
| `413` | `REQUEST_TOO_LARGE` | Body größer als `METADATA_AGENT_MAX_REQUEST_BYTES`; bei `/validate` und `/export/markdown` größer als 2 MiB |
| `422` | `VALIDATION_ERROR` | Der Body passt nicht zum Request-Modell, oder ein Wert ist länger als seine Grenze, siehe [Grenzen und Zeitlimits](#grenzen-und-zeitlimits) |
| `422` | `SCHEMA_UNAVAILABLE` | Schreiben: Das Schema des Datensatzes lässt sich nicht laden, weil Kontext, Version oder `metadataset` unbekannt ist |
| `499` | keiner | Der Aufrufer hat die Verbindung geschlossen, siehe [Grenzen und Zeitlimits](#grenzen-und-zeitlimits) |
| `500` | `INTERNAL_ERROR` | Unerwarteter Fehler; die Ursache steht im Log unter der Anfrage-ID |
| `502` | `UPSTREAM_ERROR` | Das Repository oder die Quelle des Textes ist ausgefallen |
| `503` | `SERVICE_UNAVAILABLE` | Die Zugangsdaten des Repositorys sind nicht konfiguriert |
| `503` | `NOT_READY` | `/ready`: Die Konfiguration ist unvollständig |
| `503` | `TOO_MANY_GENERATIONS` | `/generate`: zu viele Läufe zugleich, mit `Retry-After` |
| `504` | `GENERATION_TIMEOUT` | Die Erschließung hat `METADATA_AGENT_GENERATE_TIMEOUT` überschritten |
| `504` | `UPLOAD_TIMEOUT` | Das Schreiben hat `METADATA_AGENT_UPLOAD_TIMEOUT` überschritten |

Codes der LLM-Aufrufe (`/generate`, `/extract-field`, `/detect-content-type`):

| `error.code` | Status | Bedeutung |
|---|---|---|
| `LLM_AUTH_MISSING` | `502` | Kein Schlüssel: keiner durchgereicht und keiner konfiguriert |
| `LLM_AUTH_REJECTED` | `502` | Der KI-Dienst nimmt den Schlüssel nicht an (dessen `401`) |
| `LLM_AUTH_FORBIDDEN` | `502` | Dem Schlüssel fehlt das Recht für diesen Aufruf (dessen `403`) |
| `LLM_MODEL_NOT_FOUND` | `502` | Modell beim Provider unbekannt |
| `LLM_RATE_LIMITED` | `503` | Nach den Wiederholungen weiter gedrosselt |
| `LLM_UNREACHABLE` | `502` | Keine Verbindung zum KI-Dienst |
| `LLM_ROUTE_NOT_FOUND` | `502` | Der B-API-Router kennt keine aktive Route dieses Namens |
| `LLM_ROUTE_MISCONFIGURED` | `502` | Die Route kann die Anfrage so, wie sie eingerichtet ist, nicht bedienen |
| `LLM_UPSTREAM_ERROR` | `502` | Serverfehler des KI-Diensts, der die Wiederholungen überdauert hat |
| `LLM_REQUEST_REJECTED` | `502` | Der KI-Dienst hat diese Anfrage abgelehnt (anderes `4xx`) |
| `LLM_EMPTY_ANSWER` | `502` | Antwort ohne Text |
| `LLM_ANSWER_TRUNCATED` | `502` | Antwort an der Tokengrenze abgeschnitten |
| `GENERATION_FAILED` | `502` | Der Lauf endete vor den Feld-Aufrufen, etwa weil das Schema nicht geladen werden konnte |
| `EXTRACTION_FAILED` | `502` | Kein Feld bekam einen Wert, und es lässt sich keine einzelne Ursache nennen, etwa weil alle Feld-Aufrufe am Zeitlimit endeten |

Codes der Textextraktion (Eingabe aus einer URL):

| `error.code` | Status | Bedeutung |
|---|---|---|
| `TEXT_EXTRACTION_NOT_CONFIGURED` | `503` | Die Installation hat keinen Textextraktionsdienst |
| `TEXT_EXTRACTION_AUTH_MISSING` | `502` | Kein Schlüssel für die Textextraktion über die B-API |
| `TEXT_EXTRACTION_AUTH_REFUSED` | `502` | Die B-API hat den Schlüssel für den Proxy-Dienst `text-extraction` abgelehnt |
| `TEXT_EXTRACTION_RATE_LIMITED` | `503` | Der Dienst ist gedrosselt (`429`); später erneut versuchen |
| `TEXT_EXTRACTION_CONNECT_TIMEOUT` | `504` | Kein Verbindungsaufbau innerhalb von 10 s |
| `TEXT_EXTRACTION_TIMEOUT` | `504` | Keine Antwort innerhalb von 30 s |
| `TEXT_EXTRACTION_UNREACHABLE` | `502` | Verbindung abgelehnt oder Name nicht auflösbar |
| `TEXT_EXTRACTION_HTTP_ERROR` | `502` | Der Dienst antwortete mit einem Fehlerstatus |
| `TEXT_EXTRACTION_FAILED` | `502` | Der Aufruf scheiterte auf andere Weise, oder die Antwort war kein lesbares JSON |
| `TEXT_EXTRACTION_EMPTY` | `422` | Der Dienst lieferte für die URL keinen Text |
| `TEXT_EXTRACTION_PAGE_UNAVAILABLE` | `422` | Der Dienst konnte die Seite nicht abrufen (sein Status `424`): gesperrt, nicht mehr da oder abgewiesen; Adresse prüfen oder den Text übergeben |

### Grenzen und Zeitlimits

| Grenze | Standard | Einstellung | Was passiert |
|---|---|---|---|
| Request-Body | 20 MiB (20.971.520 Bytes) | `METADATA_AGENT_MAX_REQUEST_BYTES` | `413` `REQUEST_TOO_LARGE` |
| Request-Body von `/validate` und `/export/markdown` | 2 MiB (2.097.152 Bytes) | fest | `413` `REQUEST_TOO_LARGE` |
| Namen in einer Anfrage | `context` 64, `version` 32, `schema_file` 300, `field_id` 200, `language` 8 Zeichen; ein Eintrag von `collection_id`, `workflow_steps` oder `steps` 200 | fest | `422`. Dieselben Grenzen gelten für `contextName`, `schemaVersion`, `metadataset` und `language` eines Datensatzes und für die Pfadparameter |
| URLs in einer Anfrage (`url`, `source_url`, `preview_url`) | 8.192 Zeichen | fest | `422` |
| Quelltext | 150.000 Zeichen | fest | Der Rest wird abgeschnitten. Die Antwort sagt das in ihren Warnungen (`processing.warnings`; `warnings` von `/detect-content-type`) |
| Korrekturanweisung | 10.000 Zeichen | fest | Bei `input_source=text` gilt ein längerer Text als neue Quelle; bei einer URL- oder Node-Quelle `400`. Siehe [POST /generate](#post-generate) |
| Befunde von `/validate` | 20 je Feld und Prüfung; 500 Fehler und 500 Warnungen je Antwort | fest | Ein Schlusseintrag zählt, was ausgelassen wurde, siehe [POST /validate](#post-validate) |
| Erschließung (`/generate`, `/extract-field`, `/detect-content-type`), Abruf der Quelle eingeschlossen | 300 s | `METADATA_AGENT_GENERATE_TIMEOUT` | `504` `GENERATION_TIMEOUT`; `/generate` liefert vorher ein Teilergebnis |
| Ein LLM-Aufruf | 90 s | `METADATA_AGENT_REQUEST_TIMEOUT` | Das Feld scheitert; ein Lese-Timeout wird einmal wiederholt |
| Textextraktion | 10 s Verbindung, 30 s Antwort | fest | `TEXT_EXTRACTION_CONNECT_TIMEOUT`, `TEXT_EXTRACTION_TIMEOUT` |
| Schreiben (`/upload`, `/nodes`, `/node`, `/workflow`, `/screenshot` mit `node_id`) | 100 s | `METADATA_AGENT_UPLOAD_TIMEOUT` | `504` `UPLOAD_TIMEOUT` |
| Screenshot | 90 s, zwei Aufnahmen zugleich | fest | `502`; weitere Aufnahmen warten innerhalb der 90 s |
| `/generate`-Läufe zugleich | keine Grenze | `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` | `503` `TOO_MANY_GENERATIONS`, `Retry-After: 15` |
| Parallele Feld-Aufrufe eines Laufs | 20 | `max_workers`, `METADATA_AGENT_DEFAULT_MAX_WORKERS` | Gedeckelt auf das, was der Provider gleichzeitig zulässt (AcademicCloud: 2) |

Ein Proxy vor dem Agent braucht ein Timeout von mindestens etwa 310 s. Mit
einem kürzeren bekommt der Aufrufer das nackte `504` des Proxys statt der
Antwort des Agents.

**JSON.** Jeder Endpunkt mit Body liest ihn auf dieselbe Weise und
nachsichtig: Zeilenumbrüche und Tabulatoren innerhalb von Strings werden
repariert, in Bodys bis 1.000.000 Zeichen. `NaN` und `Infinity`, die kein JSON
sind, ungültiges UTF-8, eine zu tiefe Verschachtelung und ein Body, der kein
JSON-Objekt ist, werden mit `400` beantwortet.

**Status `499`.** Schließt der Aufrufer die Verbindung, stoppen `/generate`,
`/extract-field` und `/detect-content-type` ihre LLM-Aufrufe. Eine Antwort
erreicht niemanden; die Anfragezeile im Log und die Metrik
`metadata_agent_http_requests_total` zeigen den Status `499`, und ein
`/generate`-Lauf zählt als `cancelled`, nicht als `failed`. Ein Proxy, der
nach seinem eigenen Timeout aufgibt, löst genau das aus. Schreibende
Operationen werden so nicht gestoppt: Ein begonnenes Schreiben wird beendet
oder aufgeräumt. Wer die Verbindung schließt, bevor sein Request-Body
vollständig ist, bekommt bei jedem Endpunkt denselben Status.

### Änderungen seit Release 3.0.1

Release 4.0.0 hat die folgenden Punkte geändert. Wer gegen
3.0.1 programmiert hat, prüft sie:

| Bereich | Bisher | Jetzt |
|---|---|---|
| `/generate` kurz vor dem Zeitlimit | `504` | `200` mit `processing.status: "partial"`; die zu späten Felder stehen in `processing.errors` |
| Aufrufer trennt die Verbindung | Der Lauf ging weiter | Die LLM-Aufrufe stoppen; Status `499` in Log und Metriken |
| Zu viele `/generate`-Läufe | keine Grenze | Optionale Grenze: `503` `TOO_MANY_GENERATIONS` mit `Retry-After` |
| Node, den das Repository nicht hat oder nicht zeigt (`node_id`, `node_url`) | `502` | `404` |
| Nicht freigegebenes `llm_model` | Abgelehnt nach dem Abruf der Quelle | `422` vor dem Abruf der Quelle |
| Textextraktion | Direkt aufgerufen | Über die B-API mit dem Schlüssel des Aufrufers; neue Codes `TEXT_EXTRACTION_AUTH_MISSING`, `TEXT_EXTRACTION_AUTH_REFUSED`, `TEXT_EXTRACTION_NOT_CONFIGURED`, `TEXT_EXTRACTION_RATE_LIMITED` |
| `node_url` mit abgelehntem oder fehlendem Schlüssel | Rückfall auf Titel und Beschreibung | Endet mit dem Fehlercode |
| `NaN`, `Infinity` in einem Body | `500` | `400` |
| `_origins`, `_source_text` falscher Art | `500` | `422` |
| `metadataset`, `schemaVersion`, `contextName`, `_schema` falscher Art bei `/upload`, `/nodes`, `/upload/verify` | Node geschrieben, danach `500` | `422`, bevor etwas geschrieben wird |
| Optionen von `/validate` und `/export/markdown` in der flachen Form | Ignoriert | Wirksam |
| `/extract-field` | Wert in eigener Form | Wert in der Form von `/generate` (Koordinaten für `schema:location`, Tageskürzel bei Öffnungszeiten) |
| `processing.warnings` | wenige Fälle | Auch: Orte ohne Koordinaten, weil die B-API den Geocoder abgelehnt hat |
| LLM-Antwort an der Tokengrenze abgeschnitten | Ein halber Wert wurde übernommen | Das Feld scheitert mit `LLM_ANSWER_TRUNCATED` |
| Erzeugte Anfrage-ID | 16 Hex-Zeichen | 32 Hex-Zeichen (die B3-Trace-ID) |
| `/validate`, `/export/markdown` mit unbekanntem Kontext, unbekannter Version oder Schemadatei | `200` (`valid: false` bzw. `Schema not found` als Markdown) | `404` |
| Ungültiges JSON oder ein Body, der kein Objekt ist, bei `/extract-field`, `/detect-content-type`, `/node`, `/screenshot` | `422` | `400`, wie bei den anderen Endpunkten; auch `NaN` und `Infinity` werden dort abgelehnt |
| `detail` eines `422` dieser vier Endpunkte | Jeder Eintrag wiederholte die abgelehnte Eingabe | Jeder Eintrag hat nur `loc`, `msg`, `type` |
| Aufrufer geht, bevor der Request-Body vollständig ist | `400` oder `500` in Log und Metriken | `499` |
| Pfad mit angehängtem Schrägstrich, zum Beispiel `/generate/` | Umleitung mit `307` | `404` |
| Body von `/validate` und `/export/markdown` | Bis 20 MiB | Höchstens 2 MiB, sonst `413` |
| Länge von `context`, `version`, `schema_file`, `field_id`, `language`, URLs und Listeneinträgen | Keine Grenze | Begrenzt, darüber `422`, siehe [Grenzen und Zeitlimits](#grenzen-und-zeitlimits) |
| Befunde von `/validate` | Einer je Wert, Werte vollständig zitiert | Höchstens 20 je Feld und Prüfung, 500 Fehler und 500 Warnungen; zitierte Werte auf 200 Zeichen gekürzt |
| `/validate`: Pflichtfeld, das `[""]` oder nur Leerzeichen enthält | Galt als gefüllt | Fehler `Required field is empty` |
| `/validate`: Zahlen und Wahrheitswerte in geschlossenen Vokabularen | `3` und `true` bekamen eine Warnung, `0` und `false` blieben ungeprüft | Verglichen als Text (`3` ist `"3"`, `true` ist `"true"`); `0` und `false` werden geprüft |
| `/validate`: Vorschlag für einen Wert außerhalb eines geschlossenen Vokabulars | Nur bei Vokabularen mit URIs; nach Ähnlichkeit auch bei Werten unter vier Zeichen | Auch bei Vokabularen mit `value` als Schlüssel (`Deutsh` ergibt `de`); nach Ähnlichkeit erst ab vier Zeichen |
| `/export/markdown` | Titel war der Dateiname; Core-Felder konnten fehlen oder doppelt stehen; `schema_used` war `auto` | Titel ist der Name des Inhaltstyps; jedes Feld einmal; Felder nicht deklarierter Gruppen in einem letzten Abschnitt; `schema_used` nennt die Datei |
| `label` von `GET /info/schemas/…` | Dateiname | Name des Inhaltstyps (`core.json`: der Dateiname) |
| `GET /widget/info` | Absolute URLs, aus der Anfrage gebildet | Relative Verweise (`dist/main.js`, `examples/full.html`); die Snippets nutzen den Platzhalter `{AGENT_URL}` |
| `screenshot_method` von `/generate` | Jeder nicht leere Text löste eine Aufnahme aus | `playwright` oder `pageshot`; andere Werte `422` |
| Korrekturanweisung über 10.000 Zeichen bei einer URL- oder Node-Quelle | Mit einer Warnung verworfen | `400` |
| Kopfschlüssel und `processing` in `existing_metadata` | Ersetzten den Kopf der Antwort | Entfallen |
| Node ohne jeden Text (`node_id`, `node_url`) | `502` | `422` |
| Aus einem Node gelesene Metadaten (`node_id`, `node_url`) | Jede Property des Nodes | Nur Felder, die ein Schema des Kontexts führt; nie `ccm:oeh_extended*` |
| `/detect-content-type`, `/extract-field` mit einem Text über 150.000 Zeichen | Stillschweigend gekürzt | Gekürzt mit Hinweis in `warnings` |
| `/nodes` mit einer Option, die es nur bei `/upload` gibt, oder umgekehrt; unbekannter Schlüssel neben einem verschachtelten `metadata` | Ignoriert | `422` |
| `metadataset`, das bei `/upload`, `/nodes` kein ladbares Schema nennt | Core-Felder geschrieben, `200` | `422` `SCHEMA_UNAVAILABLE`, nichts geschrieben |
| Schreiben erreicht das Zeitlimit, während Sammlungen verknüpft werden | Node verworfen | Node bleibt: `504` mit `node` und `collections` |
| Antwort von `/upload` und `/nodes` | `fields_skipped` war nach jedem gelungenen Schreiben `0` | `fields_skipped` zählt die gesendeten Felder, die keine Property erreichen; neu nennt `skipped_fields` sie |
| `schemaVersion: null` bei `/upload`, `/nodes`, `/upload/verify` | `422` | Die Standardversion, wie bei `/validate` |
| `/screenshot` mit `node_id`, ohne Dienstkonto | `200` mit `preview_uploaded: false` | `503` vor der Aufnahme |
| `/upload/verify` | Verschachteltes `expected_metadata` wurde nicht verglichen; Optionen zählten als Felder; ein als Schlüssel und Version gespeicherter Lizenz-Link war `missing_in_repo`; `oeh:new_lrt` war `not_written`, wo das Schema das Feld `ccm:oeh_lrt` nennt; Groß- und Kleinschreibung überall ignoriert | Alle Formen werden gleich verglichen; Optionen bleiben außen vor; ein solcher Lizenz-Link ist ein `match`; `oeh:new_lrt` wird als `ccm:oeh_lrt` verglichen; Groß- und Kleinschreibung nur bei Vokabularwerten und dem Lizenzschlüssel ignoriert |
| `ccm:commonlicense_key` in der Schreibweise `CC BY-SA`, `cc-by-sa` oder `CC0` | Geschrieben wie gesendet | Geschrieben als `CC_BY_SA`, `CC_0` |
| `ccm:custom_license` | Ein Link beliebiger Herkunft, der auf einen Lizenzschlüssel endet, wurde zu diesem Schlüssel; ein portierter CC-Link verlor das Land | Nur URIs unter `w3id.org/openeduhub/vocabs/` werden zum Schlüssel; ein portierter CC-Link (`/3.0/de/`) bleibt neben Schlüssel und Version erhalten |
| Dublettenprüfung | Nur der Suchindex; ein Treffer ohne Titel zählte nicht | Auch die 25 neuesten Nodes des Eingangsordners; ein Treffer ohne Titel ist eine Dublette, benannt mit seiner ID |
| Metrik `metadata_agent_generations_total` | `success`, `partial`, `failed` | Auch `cancelled` (der Aufrufer ist gegangen) und `refused` (`TOO_MANY_GENERATIONS`) |
| `Retry-After` für eine Seite eines anderen Origins | Nicht lesbar | Lesbar (CORS) |
| `/generate` ohne ein Feld, das zu erschließen bleibt | `500` | `200`, Datensatz unverändert, `fields_extracted: 0` |
| Antwort des Modells für ein Vokabularfeld | Statt des Schlüssels konnte ein Label zurückkommen (`Deutsch`); Antworten mit ein bis drei Zeichen wurden nach Ähnlichkeit zugeordnet (aus `7` wurde die Altersfreigabe 0) | Immer der Schlüssel (`de`); keine Zuordnung nach Ähnlichkeit unter vier Zeichen |
| Einzelner Wert in einem Listenfeld | Blieb ein einzelner Wert | Eine Liste mit einem Eintrag |
| Liste in einem Feld für einen Wert | Erster Eintrag, die übrigen entfielen stillschweigend | Erster Eintrag, mit Hinweis in `processing.warnings` |
| `_origins` eines leeren Felds, nach dem das Modell gefragt wurde | `user` | `ai` |
| Feld, das `core.json` und der Inhaltstyp beide führen | Zweimal erschlossen und in `fields_total` zweimal gezählt (`event.json`: 48) | Einmal (47) |
| Pflichtfeld ohne Wert | Bis zu `METADATA_AGENT_LLM_MAX_RETRIES` weitere Aufrufe | Genau ein weiterer Aufruf; keiner bei geschlossenem Vokabular |
| Bestehender Wert eines Felds in dessen Prompt | In voller Länge | Auf 2.000 Zeichen gekürzt |
| Zahlen in Wahrheitswert-Feldern | Vom Modell galt jede Zahl außer `0` als `true`; vom Aufrufer war keine Zahl ein Wahrheitswert | `1` und `0` sind `true` und `false`; jede andere Zahl ist kein Wahrheitswert |
| `1.500` in einem Ganzzahlfeld | `2` | `1500` |
| Datumsangaben `März 15, 2025`, `1. Sept. 2025` | Nicht gelesen | Gelesen |
| Datum `03/15/2025` vom Modell; Einträge von `schema:eventSchedule` und `schema:openingHoursSpecification`, die keine Objekte sind | Entfielen stillschweigend | Entfallen mit Hinweis in `processing.warnings` |
| `dayOfWeek` mit mehreren Tagen in einem String (`Mo, Mi, Fr`) | Ein Tag (`MO`) | Jeder genannte Tag; auch die englischen Kurzformen `mon` bis `sun` |
| Ort mit `address` oder `geo` als Text | Geokodierung für die ganze Anfrage abgeschaltet | Die Text-Adresse wird nachgeschlagen; ein Fehler kostet nur diesen Ort, mit Hinweis in `processing.warnings` |
| `language` außer `de`, `en`, `fr` | Orte blieben ohne Koordinaten | Koordinaten wie bei den anderen Sprachen |
| Am Zeitlimit oder durch Trennung gestoppte LLM-Aufrufe in `metadata_agent_llm_requests_total` | `result="ok"` | `result="cancelled"` |
| Metriken, die sagen, wann zuletzt ein Fehler auftrat | keine | `metadata_agent_llm_last_failure_timestamp_seconds`, `metadata_agent_http_last_internal_error_timestamp_seconds` |

## Gemeinsame Bausteine

### Eingabequelle

`/generate`, `/extract-field` und `/detect-content-type` nehmen ihren Text aus
einer von vier Quellen:

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `input_source` | string | `text` | `text`, `url`, `node_id` oder `node_url` |
| `text` | string | keiner | Der Text. Pflicht bei `text` |
| `source_url` | string | keiner | URL der Seite. Pflicht bei `url`, optional bei `node_url` |
| `node_id` | string | keiner | UUID eines Nodes im Repository. Pflicht bei `node_id` und `node_url` |
| `extraction_method` | string | `browser` | Wie die Seite gelesen wird: `browser` (rendert JavaScript) oder `simple` (schnell) |
| `output_format` | string | `markdown` | Form des Seitentexts: `markdown`, `txt` oder `html` |
| `repository` | string | `staging` | Veraltet und ignoriert. Das Repository kommt aus `METADATA_AGENT_REPOSITORY_URL` |

Ein leerer String gilt bei `text`, `source_url` und `node_id` als „nicht
angegeben“.

| `input_source` | Woher der Text kommt |
|---|---|
| `text` | Aus der Anfrage. Steuerzeichen werden entfernt |
| `url` | Der Textextraktionsdienst liest `source_url` |
| `node_id` | Volltext und Metadaten des Nodes. Ohne gespeicherten Volltext: Titel, Beschreibung und Schlagwörter des Nodes |
| `node_url` | Der gespeicherte Volltext des Nodes. Ohne ihn die Seite unter `source_url` oder unter `ccm:wwwurl` des Nodes. Ist die Seite nicht lesbar: Titel, Beschreibung und Schlagwörter, mit Hinweis in den Warnungen |

Bei Node-Quellen gelten die Metadaten des Nodes als `existing_metadata`; Werte
aus der Anfrage haben Vorrang. Vom Node übernommen werden nur die Felder, die
ein Schema des Kontexts führt, nie die erweiterten Felder
`ccm:oeh_extended*`. Ein gespeicherter Volltext wird bis 150.000 Zeichen
gelesen; die Warnungen sagen, wenn er gekürzt wurde. Hat die Quelle eine URL
(`source_url` oder `ccm:wwwurl` des Nodes), beginnt der Text mit einer Zeile
`Quell-URL / Source URL: <url>`.

Fehler der Quelle, bei allen drei Endpunkten:

| Status | `error.code` | Bedeutung |
|---|---|---|
| `400` | `BAD_REQUEST` | `text`, `source_url` oder `node_id` fehlt für die gewählte Quelle; die Quelle lieferte keinen Text; `source_url` ist nicht öffentlich |
| `404` | `NOT_FOUND` | Das Repository hat den Node nicht oder zeigt ihn dem Dienstkonto nicht |
| `422` | `VALIDATION_ERROR` | `node_id` ist keine UUID; `source_url` ist länger als 8.192 Zeichen; der Node enthält keinen Text: weder Volltext noch Titel, Beschreibung, Schlagwörter oder eine abrufbare URL |
| `422` | `TEXT_EXTRACTION_EMPTY` | Die Seite hat keinen Text |
| `422` | `TEXT_EXTRACTION_PAGE_UNAVAILABLE` | Die Textextraktion konnte die Seite nicht abrufen |
| `502` | `UPSTREAM_ERROR` | Das Repository war nicht lesbar. `detail`: `Failed to fetch input: <Fehlertyp>` |
| `502`, `503`, `504` | `TEXT_EXTRACTION_*` | Siehe [Fehler](#fehler). Bei `node_url` beenden nur die Codes `…_AUTH_MISSING`, `…_AUTH_REFUSED` und `…_NOT_CONFIGURED` die Anfrage; andere Ausfälle führen zum Rückfall auf Titel und Beschreibung |

### Nur öffentliche URLs

Jede URL, die der Agent selbst öffnet, muss eine öffentliche `http`- oder
`https`-Adresse sein: `source_url`, Screenshot-URLs und URLs von
Vorschaubildern. Abgelehnt werden `localhost`, Namen mit den Endungen
`.localhost`, `.local`, `.internal`, `.lan` oder `.home.arpa`, Loopback-,
private und Link-Local-Adressen (auch `169.254.169.254`), Zugangsdaten in der
URL und andere Schemata. Der Hostname wird im Container des Agents aufgelöst;
ein Name, der dort auf eine private Adresse zeigt, wird ebenfalls abgelehnt.

| Wo | Antwort |
|---|---|
| `source_url` bei `input_source=url`, `url` von `/screenshot` | `400`, der Grund steht in `detail` |
| `source_url` bei `node_url` | `400`, aber nur, wenn der Node keinen gespeicherten Volltext hat (mit Volltext wird die URL nicht abgerufen) |
| `ccm:wwwurl` eines Nodes bei `node_url` | Kein Fehler: Titel und Beschreibung werden genutzt, mit Hinweis in den Warnungen |
| Screenshot oder Vorschau neben `/generate` oder `/upload` | Kein Fehler: Die Antwort hat keine Vorschau bzw. `preview.success: false` |

### Provider und Modell je Anfrage

`/generate`, `/extract-field` und `/detect-content-type` nehmen zwei optionale
Felder an, die das konfigurierte LLM für eine Anfrage ersetzen:

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `llm_provider` | string | `METADATA_AGENT_LLM_PROVIDER` (`b-api-openai`) | `b-api-openai`, `b-api-academiccloud`, `b-api-router` oder `openai`. Großschreibung und Leerzeichen werden toleriert |
| `llm_model` | string | Standardmodell des Providers | Modellname. Bei `b-api-router` ein Routenname oder `provider/modell`. Höchstens 200 Zeichen aus `A–Z a–z 0–9 . _ : / @ -` |

Standardmodelle: `gpt-6-luna` (`b-api-openai`), `deepseek-v4-flash`
(`b-api-academiccloud`), `openai/gpt-6-luna` (`b-api-router`), `gpt-4o-mini`
(`openai`).

Beide werden vor dem Abruf der Quelle geprüft und mit `422` beantwortet:

- `llm_provider: "openai"` ist nur erlaubt, wenn der Dienst selbst mit dem
  Provider `openai` läuft.
- Ist `METADATA_AGENT_ALLOWED_LLM_MODELS` gesetzt, muss `llm_model` auf dieser
  Liste stehen oder das Standardmodell des Providers sein.
- Ein unbekannter Providername oder ein Modellname mit anderen Zeichen.

### Schemata und Inhaltstypen

Ein Datensatz gehört zu einem **Kontext** (`default`, `mds_oeh`), einer
**Version** des Kontexts und einem **Inhaltstyp**, benannt durch seine
Schemadatei. Die Core-Felder (`core.json`: Titel, Beschreibung, Schlagwörter,
Fach, Bildungsstufe und weitere) gelten für jeden Inhaltstyp.

| Name | Bedeutung |
|---|---|
| `context` | `default` (WLO/OEH-Standard) oder `mds_oeh` |
| `version` | `latest` oder eine Version wie `2.0.0`. `latest` ist derzeit `2.0.0`; `1.8.0` und `1.8.1` werden weiter ausgeliefert |
| `schema_file` | Schemadatei wie `event.json`, die URI des Inhaltstyps (`http://w3id.org/openeduhub/vocabs/contentTypes/event`) oder `auto`, wo eine Erkennung angeboten wird |

Inhaltstypen der Version `2.0.0` (beide Kontexte):

| Schemadatei | Inhaltstyp | Letzter Teil der URI |
|---|---|---|
| `learning_material.json` | Lernmaterial | `learning_material` |
| `education_offer.json` | Bildungsangebot | `education_offer` |
| `event.json` | Veranstaltung | `event` |
| `person.json` | Person | `person` |
| `organization.json` | Organisation | `organization` |
| `tool_service.json` | Tool oder Dienst | `tool_service` |
| `occupation.json` | Beruf | `profession` |
| `didactic_planning_tools.json` | Didaktische Planung | `didactic_concepts` |

Die Versionen `1.8.0` und `1.8.1` bieten zusätzlich `source.json` und
`prompt.json`. Die aktuellen Listen liefern die Endpunkte unter
[Schema-Informationen](#schema-informationen). Die Feld-IDs unterscheiden sich
zwischen den Versionen: In `2.0.0` beginnt eine Veranstaltung mit
`ccm:oeh_event_begin`. Welche Felder das Repository erreichen, steht in
[WLO-REPO-FELDER.md](../WLO-REPO-FELDER.md).

### Metadaten-Body

`/validate`, `/export/markdown`, `/upload` und `/nodes` nehmen einen
Metadatensatz in zwei gleichwertigen Formen an. Die Optionen der Operation
stehen neben dem Datensatz.

Flach, genau das, was `/generate` zurückgibt (empfohlen):

```json
{
  "contextName": "default",
  "schemaVersion": "2.0.0",
  "metadataset": "event.json",
  "cclom:title": "Workshop KI in der Bildung",
  "ccm:oeh_event_begin": "2026-03-15T09:00",
  "check_duplicates": false
}
```

Verschachtelt, die Felder unter `metadata`:

```json
{
  "metadata": {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "cclom:title": "Workshop KI in der Bildung",
    "ccm:oeh_event_begin": "2026-03-15T09:00"
  },
  "check_duplicates": false
}
```

Regeln:

- `contextName`, `schemaVersion` und `metadataset` dürfen in `metadata` oder
  daneben stehen. Innen hat Vorrang.
- `/upload` und `/nodes` nehmen auch die Form an, die die Webkomponente sendet:
  die Felder eine Ebene tiefer unter `metadata.metadata`, die drei Kennzeichen
  im äußeren `metadata`.
- **`metadataset` immer mitschicken** (Dateiname oder URI des Inhaltstyps). Es
  entscheidet, welches Schema neben `core.json` gilt. Ohne es schreiben
  `/upload` und `/nodes` nur die Core-Felder und lassen typspezifische Felder
  wie `ccm:oeh_event_begin` ohne Fehler weg; `/validate` und
  `/export/markdown` nehmen `learning_material.json` an.
- In der flachen Form werden nur die Optionsnamen der Operation aus dem
  Datensatz herausgenommen. Eine falsch geschriebene Option gilt als
  Metadatenfeld und bleibt wirkungslos.
- `/upload` und `/nodes` lehnen eine Option, die es nur beim jeweils anderen
  gibt, mit `422` ab, in beiden Formen (`repository` ignorieren beide). Neben
  einem verschachtelten `metadata` lehnen sie außerdem jeden Schlüssel ab, der
  weder eine ihrer Optionen noch Teil einer `/generate`-Antwort ist.
- `contextName`, `schemaVersion` und `metadataset` müssen Strings oder `null`
  sein (`null` gilt als nicht angegeben), `_schema` (ältere Form mit
  `context`, `version`, `file`) ein Objekt. Sonst `422`.
- Der Rest einer `/generate`-Antwort stört nicht: Schlüssel, die mit `_`
  beginnen (`_origins`, `_source_text`), und die Schlüssel `metadataset_uri`,
  `language`, `exportedAt`, `processing` und `preview_image_url` werden nie
  als Felder geschrieben oder validiert.

## Erschließung

### POST /generate

Erschließt einen vollständigen Metadatensatz aus einem Text, einer Webseite
oder einem Node im Repository. Der Inhaltstyp wird erkannt oder vorgegeben.
Jedes Feld ist ein LLM-Aufruf; die Aufrufe laufen parallel.

**Request.** Body: JSON, oder `text/plain` mit dem Text allein (alle anderen
Werte auf ihrem Standard).

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| Eingabefelder | | | Siehe [Eingabequelle](#eingabequelle) |
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version |
| `schema_file` | string | `auto` | Inhaltstyp. `auto`: Das LLM erkennt ihn |
| `language` | string | `de` | Sprache der Erschließung: `de` oder `en` |
| `max_workers` | integer | `METADATA_AGENT_DEFAULT_MAX_WORKERS` (20) | Parallele LLM-Aufrufe, 1 bis 20 |
| `include_core` | boolean | `true` | Auch die Core-Felder erschließen |
| `enable_geocoding` | boolean | `true` | Die Orte in `schema:location` um Koordinaten ergänzen |
| `normalize` | boolean | `true` | Datumsangaben, Wahrheitswerte, Vokabularwerte und Struktur normalisieren. Die Form folgt dem Schema: Ein einzelner Wert in einem Listenfeld wird zur Liste mit einem Eintrag; eine Liste in einem Feld für einen Wert ergibt ihren ersten Eintrag, mit Hinweis in `processing.warnings`. Siehe „Wie Werte gelesen werden“ unten |
| `existing_metadata` | object | keiner | Werte, die es schon gibt, siehe unten |
| `regenerate_fields` | string-Array | keiner | Nur diese Feld-IDs werden neu erschlossen |
| `regenerate_empty` | boolean | `false` | Auch die Felder erschließen, die in `existing_metadata` leer sind |
| `llm_provider`, `llm_model` | string | konfiguriert | Siehe [Provider und Modell je Anfrage](#provider-und-modell-je-anfrage) |
| `screenshot_method` | string | keiner | `playwright` (oder der alte Alias `pageshot`) nimmt neben der Erschließung eine Vorschau auf. Nicht gesetzt oder leer: kein Screenshot. Jeder andere Wert: `422` |
| `preview_url` | string | keiner | URL für den Screenshot. Nicht gesetzt: `source_url`, sonst `ccm:wwwurl` des Datensatzes |

Bestehende Werte und wiederholte Läufe:

- `existing_metadata` enthält Feld-IDs mit ihren Werten, dazu `_origins` und
  `_source_text` aus einer früheren Antwort. Die Kopfschlüssel
  (`contextName`, `schemaVersion`, `metadataset`, `metadataset_uri`,
  `language`, `exportedAt`), `processing` und `preview_image_url` entfallen;
  eine ganze frühere Antwort lässt sich also senden. Jeder andere Schlüssel,
  den das Schema nicht kennt, kommt unverändert wie ein Feld zurück.
- Ohne `regenerate_fields` und `regenerate_empty` wird jedes Feld erschlossen.
  Ein neuer Wert ersetzt den bestehenden. Eine leere Antwort ersetzt nie einen
  bestehenden Wert.
- Mit einer der beiden Optionen werden nur die genannten Felder, die leeren
  Felder (`regenerate_empty`) und die Felder erschlossen, die
  `existing_metadata` nicht enthält.
- Bleibt kein Feld zu erschließen, etwa bei einem vollständigen Datensatz mit
  `regenerate_empty`, lautet die Antwort `200` mit dem unveränderten Datensatz
  und `processing.fields_extracted: 0`. Ein Feld-Aufruf findet nicht statt.
- Der bestehende Wert eines Felds geht in dessen Prompt ein, gekürzt auf 2.000
  Zeichen.
- `existing_metadata._origins` ordnet Feld-IDs `ai` oder `user` zu. Ein Feld
  mit `user` behält seinen Wert und wird nicht erschlossen, außer es steht in
  `regenerate_fields`.
- **Korrekturrunde.** Die Felder des ersten Laufs als `existing_metadata`
  senden, einschließlich `_source_text`, und die Anweisung in `text` schreiben
  (zum Beispiel „Titel kürzer“). Der Agent arbeitet dann mit der
  ursprünglichen Quelle, den bisherigen Werten und der Anweisung. Bei
  `input_source=text` gilt ein `text` über 10.000 Zeichen stattdessen als neue
  Quelle; `processing.warnings` sagt das. Bei einer URL- oder Node-Quelle wird
  die Quelle abgerufen, und `text` kann nur die Anweisung sein: Über 10.000
  Zeichen wird sie mit `400` abgelehnt.
- `_origins` muss ein Objekt aus Strings sein und `_source_text` ein String,
  sonst `422`.

**Antwort `200`.** Die Felder stehen auf der obersten Ebene, nicht in einem
Objekt `metadata`:

```json
{
  "contextName": "default",
  "schemaVersion": "2.0.0",
  "metadataset": "event.json",
  "metadataset_uri": "http://w3id.org/openeduhub/vocabs/contentTypes/event",
  "language": "de",
  "exportedAt": "2026-11-14T08:00:00.123456+00:00",
  "cclom:title": "Workshop KI in der Bildung",
  "cclom:general_description": "Ein Workshop über den Einsatz von KI im Unterricht.",
  "cclom:general_keyword": ["KI", "Bildung", "Workshop"],
  "ccm:oeh_event_begin": "2026-03-15T09:00",
  "schema:location": [
    {
      "name": "Berlin",
      "address": {
        "streetAddress": "",
        "postalCode": "",
        "addressLocality": "Berlin",
        "addressRegion": "Berlin",
        "addressCountry": "DE"
      },
      "geo": { "latitude": 52.5200066, "longitude": 13.404954 }
    }
  ],
  "_origins": {
    "cclom:title": "ai",
    "cclom:general_description": "ai",
    "cclom:general_keyword": "ai",
    "ccm:oeh_event_begin": "ai",
    "schema:location": "ai"
  },
  "_source_text": "Workshop KI in der Bildung am 15. März 2026 um 9 Uhr in Berlin.",
  "processing": {
    "status": "success",
    "success": true,
    "fields_extracted": 5,
    "fields_failed": 0,
    "fields_total": 47,
    "processing_time_ms": 14200,
    "llm_provider": "b-api-openai",
    "llm_model": "gpt-6-luna",
    "auth_source": "forwarded",
    "content_type_detection": { "method": "llm", "confidence": "high", "schema_file": "event.json" },
    "errors": [],
    "warnings": []
  }
}
```

| Teil | Bedeutung |
|---|---|
| `contextName`, `schemaVersion`, `metadataset` | Kontext, konkrete Version und Schemadatei, die galten |
| `metadataset_uri` | URI des Inhaltstyps |
| Feld-IDs | Jedes Feld mit einem Wert. Leere Werte (`""`, `[]`, `{}`, `null`) entfallen |
| `_origins` | Herkunft jedes Felds des Schemas, auch der leeren, die in der Antwort entfallen (das Beispiel zeigt nur die gefüllten): `ai` für einen Wert vom Modell und für ein leeres Feld, nach dem das Modell gefragt wurde, `user` für einen Wert, den der Aufrufer mitgebracht und das Modell nicht ersetzt hat |
| `_source_text` | Der Text, aus dem die Werte stammen. Für eine Korrekturrunde zurücksenden |
| `preview_image_url` | Nur bei gelungenem Screenshot: Data-URL (`data:image/png;base64,…`). Ein Screenshot, der scheitert oder zu spät kommt, entfällt ohne Fehler |
| `processing.status` | `success`, `partial` (einzelne Felder gescheitert) oder `failed` (nie in einem `200`) |
| `processing.fields_extracted`, `fields_failed`, `fields_total` | Felder mit einem Wert aus diesem Lauf, aus technischem Grund gescheiterte Felder, Felder des Schemas, die das Modell füllen kann. Ein Feld, das `core.json` und der Inhaltstyp beide führen, zählt einmal (`event.json`: 47) |
| `processing.auth_source` | Herkunft des Schlüssels: `forwarded`, `static`, `openai` |
| `processing.content_type_detection` | `method`: `llm`, `preselected` (per `schema_file` vorgegeben) oder `keywords`. Bei `keywords` gab das LLM keine brauchbare Antwort, der Typ ist geraten: `confidence: "low"`, ein `reason` und ein Hinweis in `warnings` |
| `processing.error` | Bei `partial` mit benennbarer Ursache und in der Fehlerantwort eines gescheiterten Laufs: `code`, `message`, bei LLM-Fehlern auch `upstream_status` und `auth_source` |
| `processing.errors` | Ein Text je gescheitertem Feld, dazu die zu späten Felder |
| `processing.warnings` | Hinweise, die den Lauf nicht scheitern lassen, siehe unten |

Typische Einträge in `processing.warnings`: Die Quelle wurde auf 150.000
Zeichen gekürzt; der Inhaltstyp wurde per Schlagwortsuche geraten; bei
`node_url` war die Seite nicht lesbar, und nur Titel und Beschreibung wurden
genutzt; Orte bleiben ohne Koordinaten, weil die B-API den Geocoder abgelehnt
hat (mit `proxy`, oder mit `auto`, wenn auch Photon direkt nicht nutzbar war)
oder die Zeit nicht reichte; ein langer `text` wurde als neue Quelle
genommen.

Weitere Einträge, im Wortlaut des Codes (`<Feld>` ist die Feld-ID):

| Eintrag | Wann |
|---|---|
| `<Feld>: Feld nimmt einen Wert auf: N weitere Wert(e) nicht übernommen.` | Für ein Feld, das einen Wert aufnimmt, kam eine Liste. Der erste Eintrag blieb |
| `<Feld>: '03/15/2025' ist kein Datum in der Lesart Tag/Monat/Jahr und wurde nicht übernommen.` | Das Modell schrieb ein Datum, das es in der Lesart Tag/Monat/Jahr nicht gibt: den Monat zuerst oder einen Tag wie `30.02.2026`. Der Wert entfiel |
| `<Feld>: N Wert(e) ohne Objektform nicht übernommen.` | Einträge von `schema:openingHoursSpecification` oder `schema:eventSchedule`, die keine Objekte sind, entfielen |
| `Geokodierung: N Ort(e) konnten nicht verarbeitet werden und bleiben unverändert (<Fehlertyp>).` | Ein Fehler beim Nachschlagen eines Orts. Dieser Ort bleibt, wie er war; die anderen werden verarbeitet |

**Wie Werte gelesen werden.**

- **Vokabulare.** Ein Vokabularfeld enthält den Schlüssel eines Konzepts:
  seine URI, sonst sein `value` (`de`), nie sein Label. Eine Antwort des
  Modells wird über den Schlüssel zugeordnet (ein `value` in beliebiger
  Schreibung), über ein Label oder Alternativlabel in beliebiger Sprache und,
  bei einem geschlossenen Vokabular, über das nächstliegende deutsche Label.
  Das letzte nur bei Antworten ab vier Zeichen: Aus `7` wird nie eine andere
  Altersfreigabe. Passt kein Konzept, ergibt ein geschlossenes Vokabular
  keinen Wert, und das Modell wird erneut gefragt, bis zu
  `METADATA_AGENT_LLM_MAX_RETRIES`-mal; ein offenes Vokabular behält die
  Antwort. Werte aus `existing_metadata` oder vom Node werden nur exakt
  zugeordnet, über Schlüssel oder Label, und ein Wert, der zu nichts passt,
  bleibt, wie er ist. Zahlen und Wahrheitswerte zählen nach ihrem Text: `3`
  ist `"3"`, `true` ist `"true"`, und `0` und `false` sind Werte.
- **Pflichtfelder.** Ein Pflichtfeld ohne Wert kostet genau einen weiteren
  Aufruf. Mit einem geschlossenen Vokabular kostet es keinen: `null` sagt
  dort, dass kein Konzept passt.
- **Zahlen.** Deutsche Schreibweise (`1,5`, `1.500,50`) und deutsche
  Zahlwörter (`zwölf`). In einem Ganzzahlfeld ist `1.500` die Zahl 1500, in
  einem Zahlfeld 1,5. `1_000` und `ein` sind keine Zahlen.
- **Wahrheitswerte.** `true`/`false`, `ja`/`nein`, `yes`/`no`,
  `wahr`/`falsch` und `1`/`0`, das letzte Paar als Text oder als Zahl. Jede
  andere Zahl ist kein Wahrheitswert.
- **Datumsangaben.** Zurückgegeben als `YYYY-MM-DD`, mit Uhrzeit als
  `YYYY-MM-DDTHH:MM`. Gelesen werden ISO-Formen, deutsche Formen
  (`15.03.2025`) und deutsche Monatsnamen, auch `März 15, 2025` und
  `1. Sept. 2025`. Der Tag steht vorn: `03/15/2025` vom Modell ist kein Datum
  und entfällt mit einer Warnung, wie ein Tag, den es nicht gibt.
- **Wochentage.** `dayOfWeek` der Öffnungszeiten enthält die Kürzel `MO` bis
  `SU`. Ein Bereich (`Mo-Fr`) und eine Liste in einem String (`Mo, Mi, Fr`,
  `Sa/So`, `Montag und Mittwoch`) werden aufgelöst, wenn jeder Teil ein Tag
  oder ein Bereich ist. Die englischen Kurzformen `mon` bis `sun` zählen
  ebenfalls. Ein Eintrag mit mehr als 200 Zeichen bleibt, wie er geschrieben
  ist.
- **Orte.** Eine als Text geschriebene `address` wird so nachgeschlagen und
  nicht ersetzt. Ein `geo`, das kein Objekt ist, enthält keine Koordinaten;
  der Breitengrad `0` ist eine Koordinate. Der Geocoder wird auf `de`, `en`
  oder `fr` gefragt; bei jeder anderen `language` antwortet er in der Sprache
  des Orts. Ein Fehler bei einem Ort lässt diesen Ort unverändert, mit einer
  Warnung.

**Teilergebnis am Zeitlimit.** Feld-Aufrufe, die 5 s vor
`METADATA_AGENT_GENERATE_TIMEOUT` noch laufen, werden beendet. Was bis dahin
erschlossen ist, kommt mit `200` zurück:

```json
{
  "status": "partial",
  "success": true,
  "fields_extracted": 31,
  "fields_failed": 2,
  "fields_total": 47,
  "processing_time_ms": 295400,
  "llm_provider": "b-api-openai",
  "llm_model": "gpt-6-luna",
  "auth_source": "forwarded",
  "content_type_detection": { "method": "preselected", "schema_file": "event.json" },
  "errors": [
    "Zeitlimit: 2 Feld(er) nicht mehr erschlossen, der KI-Dienst antwortete nicht rechtzeitig (ccm:competence, schema:offers)."
  ],
  "warnings": []
}
```

Prüfen Sie `processing.status` und `processing.errors`, bevor Sie ein Ergebnis
übernehmen. Die zu späten Felder lassen sich mit `regenerate_fields` oder
[POST /extract-field](#post-extract-field) nachholen.

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `200` | keiner | `processing.status` ist `success` oder `partial` |
| `400` | `BAD_REQUEST` | Ungültiges JSON, `NaN` oder `Infinity`, Body ist kein Objekt, kein Text, unbekannte URI des Inhaltstyps, Korrekturanweisung über 10.000 Zeichen bei einer URL- oder Node-Quelle, siehe auch [Eingabequelle](#eingabequelle) |
| `404` | `NOT_FOUND` | Kontext, Version oder Schemadatei unbekannt (geprüft vor dem Abruf der Quelle); Node nicht gefunden |
| `422` | `VALIDATION_ERROR` | Ein Feld passt nicht, zum Beispiel `max_workers` außerhalb von 1 bis 20, ein unbekanntes `screenshot_method`, ein Wert über seiner Längengrenze, `_origins` oder `_source_text` falscher Art, Provider oder Modell nicht erlaubt; ein Node ohne Text |
| `499` | keiner | Der Aufrufer hat die Verbindung geschlossen; der Lauf wurde gestoppt |
| `502` | `LLM_*`, `GENERATION_FAILED`, `EXTRACTION_FAILED` | Kein einziges Feld wurde erschlossen. Der Body enthält zusätzlich `contextName`, `schemaVersion`, `metadataset`, `language` und `processing` |
| `503` | `LLM_RATE_LIMITED` | Wie `502`, der KI-Dienst ist gedrosselt |
| `503` | `TOO_MANY_GENERATIONS` | Die Grenze gleichzeitiger Läufe ist erreicht. Header `Retry-After: 15` |
| `504` | `GENERATION_TIMEOUT` | Das Zeitlimit lief ab, bevor es ein Ergebnis gab, etwa beim Abruf der Quelle |
| `422`, `502`, `503`, `504` | `TEXT_EXTRACTION_*`, `UPSTREAM_ERROR` | Die Quelle war nicht lesbar, siehe [Eingabequelle](#eingabequelle) |

Ein gescheiterter Lauf (`schema_file` war vorgegeben, die B-API lehnte den
Schlüssel beim ersten Feld-Aufruf ab):

```json
{
  "detail": "KI-Metadatengenerierung fehlgeschlagen: Der KI-Dienst hat den Zugriff verweigert (HTTP 403; eigener B_API_KEY des Agents). Dem Schlüssel fehlt eine Berechtigung – für den LLM-Endpunkt oder für Modell gpt-6-luna",
  "error": {
    "code": "LLM_AUTH_FORBIDDEN",
    "message": "Der KI-Dienst hat den Zugriff verweigert (HTTP 403; eigener B_API_KEY des Agents). Dem Schlüssel fehlt eine Berechtigung – für den LLM-Endpunkt oder für Modell gpt-6-luna",
    "upstream_status": 403,
    "auth_source": "static",
    "request_id": "3f9c2a7d1b4e5f6091c4a7d2e8b05f13"
  },
  "contextName": "default",
  "schemaVersion": "2.0.0",
  "metadataset": "event.json",
  "language": "de",
  "processing": {
    "status": "failed",
    "success": false,
    "fields_extracted": 0,
    "fields_failed": 47,
    "fields_total": 47,
    "processing_time_ms": 320,
    "llm_provider": "b-api-openai",
    "llm_model": "gpt-6-luna",
    "auth_source": "static",
    "content_type_detection": { "method": "preselected", "schema_file": "event.json" },
    "errors": [
      "Der KI-Dienst hat den Zugriff verweigert (HTTP 403; eigener B_API_KEY des Agents). Dem Schlüssel fehlt eine Berechtigung – für den LLM-Endpunkt oder für Modell gpt-6-luna"
    ],
    "warnings": [],
    "error": {
      "code": "LLM_AUTH_FORBIDDEN",
      "message": "Der KI-Dienst hat den Zugriff verweigert (HTTP 403; eigener B_API_KEY des Agents). Dem Schlüssel fehlt eine Berechtigung – für den LLM-Endpunkt oder für Modell gpt-6-luna",
      "upstream_status": 403,
      "auth_source": "static"
    }
  }
}
```

Nach einem fatalen Fehler wie einem abgelehnten Schlüssel sendet der Agent
keine weiteren Feld-Aufrufe. Scheitert schon die Erkennung des Inhaltstyps,
ist `metadataset` `null` und `processing.fields_total` `0`.

**Beispiel.**

```bash
curl -s -X POST "$API/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Workshop KI in der Bildung am 15. März 2026 um 9 Uhr in Berlin. Zielgruppe: Lehrkräfte.",
    "schema_file": "event.json"
  }'
```

### POST /extract-field

Erschließt ein einzelnes Feld neu, zum Beispiel um es ohne vollständigen Lauf
zu korrigieren. Der Wert kommt in derselben Form zurück wie von `/generate`.

**Request.**

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| Eingabefelder | | | Siehe [Eingabequelle](#eingabequelle) |
| `schema_file` | string | Pflicht | Schemadatei oder URI des Inhaltstyps. `auto` ist hier nicht möglich |
| `field_id` | string | Pflicht | Feld-ID, zum Beispiel `ccm:oeh_event_begin`. Ein Feld, das der Inhaltstyp nicht führt, wird in `core.json` gesucht |
| `existing_metadata` | object | keiner | Aktuelle Werte. Der Wert von `field_id` geht als bisheriger Wert an das LLM, gekürzt auf 2.000 Zeichen |
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version |
| `language` | string | `de` | `de` oder `en` |
| `normalize` | boolean | `true` | Den Wert normalisieren und wie bei `/generate` formen: Ein einzelner Wert in einem Listenfeld wird zur Liste mit einem Eintrag, eine Liste in einem Feld für einen Wert ergibt ihren ersten Eintrag mit Hinweis in `processing.warnings`. Bei `schema:location` gehören Koordinaten dazu |
| `llm_provider`, `llm_model` | string | konfiguriert | Siehe [Provider und Modell je Anfrage](#provider-und-modell-je-anfrage) |

**Antwort `200`.**

```json
{
  "field_id": "ccm:oeh_event_begin",
  "field_label": "Start (Datum/Zeit)",
  "value": "2026-03-20T00:00",
  "raw_value": "2026-03-20",
  "previous_value": "2026-03-15T09:00",
  "changed": true,
  "normalized": true,
  "context": "default",
  "version": "2.0.0",
  "schema_file": "event.json",
  "processing": {
    "llm_provider": "b-api-openai",
    "llm_model": "gpt-6-luna",
    "processing_time_ms": 450,
    "warnings": []
  }
}
```

| Teil | Bedeutung |
|---|---|
| `value` | Der erschlossene Wert, normalisiert. `null`, wenn der Text keinen Wert für das Feld enthält |
| `raw_value` | Die Antwort des LLM vor der Normalisierung. `null`, wenn die Normalisierung nichts geändert hat |
| `previous_value` | Der Wert aus `existing_metadata` |
| `changed` | `value` weicht von `previous_value` ab |
| `normalized` | Die Normalisierung hat den Wert geändert |
| `field_label` | Bezeichnung des Felds in `language` |
| `processing.warnings` | Hinweise zur Eingabe, zu nicht übernommenen Werten (ein Datum, das das Modell mit dem Monat zuerst schrieb, weitere Einträge einer Liste, Einträge ohne Objektform) und zum Geocoding, im Wortlaut von [POST /generate](#post-generate); auch, dass der Text auf 150.000 Zeichen gekürzt wurde |

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `400` | `BAD_REQUEST` | Ungültiges JSON, Body ist kein Objekt, unbekannte URI des Inhaltstyps, kein Text, siehe auch [Eingabequelle](#eingabequelle) |
| `404` | `NOT_FOUND` | Kontext, Version, Schema oder Feld unbekannt (geprüft vor dem Abruf der Quelle); Node nicht gefunden |
| `422` | `VALIDATION_ERROR` | `schema_file` oder `field_id` fehlt, ein Feld passt nicht oder liegt über seiner Längengrenze, Provider oder Modell nicht erlaubt; ein Node ohne Text |
| `499` | keiner | Der Aufrufer hat die Verbindung geschlossen |
| `500` | `INTERNAL_ERROR` | Die Erschließung scheiterte aus einem Grund, der kein LLM-Fehler ist |
| `502`, `503` | `LLM_*` | Der LLM-Aufruf scheiterte, siehe [Fehler](#fehler) |
| `504` | `GENERATION_TIMEOUT` | Länger als `METADATA_AGENT_GENERATE_TIMEOUT` |
| `422`, `502`, `503`, `504` | `TEXT_EXTRACTION_*`, `UPSTREAM_ERROR` | Die Quelle war nicht lesbar |

**Beispiel.**

```bash
curl -s -X POST "$API/extract-field" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Der Workshop wurde auf den 20. März 2026 verschoben.",
    "schema_file": "event.json",
    "field_id": "ccm:oeh_event_begin",
    "existing_metadata": {"ccm:oeh_event_begin": "2026-03-15T09:00"}
  }'
```

### POST /detect-content-type

Erkennt den Inhaltstyp eines Textes mit einem LLM-Aufruf und listet die
Inhaltstypen des Kontexts.

**Request.**

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| Eingabefelder | | | Siehe [Eingabequelle](#eingabequelle) |
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version |
| `language` | string | `de` | `de` oder `en` |
| `llm_provider`, `llm_model` | string | konfiguriert | Siehe [Provider und Modell je Anfrage](#provider-und-modell-je-anfrage) |

**Antwort `200`.** `available` ist hier auf einen Eintrag gekürzt.

```json
{
  "detected": {
    "schema_file": "event.json",
    "uri": "http://w3id.org/openeduhub/vocabs/contentTypes/event",
    "profile_id": "type:event",
    "label": { "de": "Veranstaltung", "en": "Event" },
    "confidence": "high"
  },
  "available": [
    {
      "schema_file": "learning_material.json",
      "uri": "http://w3id.org/openeduhub/vocabs/contentTypes/learning_material",
      "profile_id": "type:learning_material",
      "label": { "de": "Lernmaterial", "en": "Learning Material" }
    }
  ],
  "context": "default",
  "version": "2.0.0",
  "processing_time_ms": 800,
  "method": "llm",
  "warnings": []
}
```

| Teil | Bedeutung |
|---|---|
| `detected` | Der erkannte Inhaltstyp. `schema_file` oder `uri` an `/generate` weitergeben |
| `available` | Alle Inhaltstypen von Kontext und Version |
| `method` | `llm`: Das Modell nannte einen der angebotenen Typen (`confidence: "high"`). `keywords`: Das Modell gab keine brauchbare Antwort, und die Schlagwortsuche ist eingesprungen (`confidence: "low"`, der Grund steht in `warnings`). Ein solches Ergebnis prüfen |
| `warnings` | Hinweise zur Eingabe und zur Erkennung; auch, dass der Text auf 150.000 Zeichen gekürzt wurde |

Ein abgelehnter Schlüssel oder ein nicht erreichbarer KI-Dienst ist ein Fehler,
nie ein stillschweigendes `learning_material.json`.

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `400` | `BAD_REQUEST` | Ungültiges JSON, Body ist kein Objekt, kein Text, siehe auch [Eingabequelle](#eingabequelle) |
| `404` | `NOT_FOUND` | Kontext oder Version unbekannt, oder sie haben keine Inhaltstypen; Node nicht gefunden |
| `422` | `VALIDATION_ERROR` | Ein Feld passt nicht oder liegt über seiner Längengrenze, Provider oder Modell nicht erlaubt; ein Node ohne Text |
| `499` | keiner | Der Aufrufer hat die Verbindung geschlossen |
| `502`, `503` | `LLM_*` | Der LLM-Aufruf scheiterte, siehe [Fehler](#fehler) |
| `504` | `GENERATION_TIMEOUT` | Länger als `METADATA_AGENT_GENERATE_TIMEOUT` |
| `422`, `502`, `503`, `504` | `TEXT_EXTRACTION_*`, `UPSTREAM_ERROR` | Die Quelle war nicht lesbar |

**Beispiel.**

```bash
curl -s -X POST "$API/detect-content-type" \
  -H "Content-Type: application/json" \
  -d '{"text": "Workshop KI in der Bildung am 15. März 2026 in Berlin."}'
```

## Validierung und Export

### POST /validate

Prüft einen Datensatz gegen sein Schema und die Core-Felder. Braucht weder LLM
noch Repository.

**Request.** Ein [Metadaten-Body](#metadaten-body), flach oder verschachtelt,
mit diesen Optionen:

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `metadata` | object | in der flachen Form der Body selbst | Der Datensatz |
| `context` | string | `auto` | `auto`: aus `contextName`, sonst `default` |
| `version` | string | `auto` | `auto`: aus `schemaVersion`, sonst `latest`. Ein führendes `v` wird toleriert |
| `schema_file` | string | `auto` | `auto`: aus `metadataset`, sonst `learning_material.json`. Dateiname oder URI des Inhaltstyps |

Was geprüft wird:

| Prüfung | Ergebnis |
|---|---|
| Pflichtfelder sind gefüllt. `[""]`, Text aus lauter Leerzeichen und `{}` sind kein Wert; `0` und `false` sind einer | Fehler |
| Objektfelder enthalten Objekte in der Form des Schemas, siehe [OBJEKTFELDER.md](../OBJEKTFELDER.md) | Fehler |
| Felder, deren ID auf `latitude`, `lat`, `longitude`, `lon` oder `lng` endet, liegen im Wertebereich | Fehler |
| Zahlen, Wahrheitswerte, Datumsangaben (`YYYY-MM-DD`), Datum mit Zeit (`YYYY-MM-DDTHH:MM`), Zeiten (`HH:MM` oder `HH:MM:SS`), URLs (`http://` oder `https://`). Tag und Uhrzeit müssen existieren | Warnung, mit Vorschlag, wo sich einer ableiten lässt |
| Werte geschlossener Vokabulare. Ein Wert muss der Schlüssel eines Konzepts sein, so wie das Vokabular ihn schreibt: die URI, sonst das `value`. Eine Zahl oder ein Wahrheitswert wird als Text verglichen: `3` ist das Konzept `"3"`, `true` ist `"true"` | Warnung; bei den ersten 5 ungültigen Werten eines Felds mit Vorschlag, wo sich einer ableiten lässt. Der Vorschlag ist der Schlüssel des Konzepts, das der Wert über ein Label, über sein `value` in anderer Schreibung oder über das Ende seiner URI nennt, sonst des Konzepts mit dem nächstliegenden Label (nur bei Werten ab vier Zeichen). `Deutsh` ergibt `de` |

**Antwort `200`.**

```json
{
  "valid": true,
  "schema_used": "event.json",
  "errors": [],
  "warnings": [
    {
      "field_id": "oeh:eventType",
      "message": "Value 'Worksho' not in vocabulary. Did you mean 'http://w3id.org/openeduhub/vocabs/eventType/workshop'?",
      "severity": "warning"
    }
  ],
  "coverage": 100.0
}
```

| Teil | Bedeutung |
|---|---|
| `valid` | `true`, wenn `errors` leer ist. Warnungen machen einen Datensatz nicht ungültig |
| `schema_used` | Die angewandte Schemadatei |
| `errors`, `warnings` | Einträge mit `field_id`, `message`, `severity` |
| `coverage` | Anteil der gefüllten Pflichtfelder in Prozent |

Die Listen sind begrenzt. Je Feld und Prüfung werden höchstens 20 Befunde
aufgeführt; ein weiterer Eintrag zählt dann die nicht mehr geprüften Werte.
Eine Antwort enthält höchstens 500 Fehler und 500 Warnungen; ein weiterer
Eintrag mit `field_id: "_limit"` zählt den Rest. Ein in einer Meldung
zitierter Wert ist auf 200 Zeichen gekürzt.

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `200` | keiner | Ergebnis im Body |
| `400` | `BAD_REQUEST` | Ungültiges JSON, Body ist kein Objekt, unbekannte URI des Inhaltstyps |
| `404` | `NOT_FOUND` | Kontext, Version oder Schemadatei unbekannt, ob von einer Option genannt oder von `contextName`, `schemaVersion`, `metadataset` des Datensatzes |
| `413` | `REQUEST_TOO_LARGE` | Body größer als 2 MiB |
| `422` | `VALIDATION_ERROR` | `contextName`, `schemaVersion`, `metadataset` oder eine Option vom falschen Typ oder über der Längengrenze |

**Beispiel.**

```bash
curl -s -X POST "$API/validate" \
  -H "Content-Type: application/json" \
  -d '{
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "cclom:title": "Workshop KI in der Bildung",
    "oeh:eventType": ["Worksho"]
  }'
```

### POST /export/markdown

Gibt einen Datensatz als Markdown-Dokument mit den Bezeichnungen des Schemas
aus: jedes Feld von `core.json` und des Inhaltstyps einmal, ein Abschnitt je
Gruppe, die Gruppen von `core.json` zuerst. Felder einer Gruppe, die keines
der beiden Schemata führt, folgen in einem letzten Abschnitt `Weitere Angaben`
(englisch `Further details`). Der Titel ist der Name des Inhaltstyps; bei
`core.json` ist es der Dateiname.

**Request.** Ein [Metadaten-Body](#metadaten-body), flach oder verschachtelt,
mit diesen Optionen:

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `metadata` | object | in der flachen Form der Body selbst | Der Datensatz |
| `context`, `version`, `schema_file` | string | `auto` | Wie bei [POST /validate](#post-validate) |
| `language` | string | `auto` | Sprache der Bezeichnungen. `auto`: `language` des Datensatzes, sonst `de`. In der flachen Form ist das der Schlüssel `language` des Datensatzes |
| `include_empty` | boolean | `false` | Auch Felder ohne Wert aufführen |

**Antwort `200`.**

```json
{
  "markdown": "# Veranstaltung\n\n## Beschreibung\n\n**Titel:** Workshop KI in der Bildung\n\n## Typisierung\n\n**Schlagwörter:** KI, Bildung\n\n## Termin & Status\n\n**Start (Datum/Zeit):** 2026-03-15T09:00\n",
  "schema_used": "event.json"
}
```

`schema_used` nennt die Schemadatei, mit der der Datensatz ausgegeben wurde.
Gruppen und Felder ohne Wert entfallen, außer mit `include_empty`; `0` und
`false` sind Werte, auch in einer Liste oder einem Objekt.

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `200` | keiner | Das Dokument |
| `400` | `BAD_REQUEST` | Ungültiges JSON, Body ist kein Objekt, unbekannte URI des Inhaltstyps |
| `404` | `NOT_FOUND` | Kontext, Version oder Schemadatei unbekannt, wie bei [POST /validate](#post-validate) |
| `413` | `REQUEST_TOO_LARGE` | Body größer als 2 MiB |
| `422` | `VALIDATION_ERROR` | `contextName`, `schemaVersion`, `metadataset`, `language` oder eine Option vom falschen Typ oder über der Längengrenze |

**Beispiel.**

```bash
curl -s -X POST "$API/export/markdown" \
  -H "Content-Type: application/json" \
  -d '{
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "language": "de",
    "cclom:title": "Workshop KI in der Bildung",
    "cclom:general_keyword": ["KI", "Bildung"],
    "ccm:oeh_event_begin": "2026-03-15T09:00",
    "include_empty": false
  }'
```

## Repository

Fünf Operationen arbeiten auf dem edu-sharing-Repository, das
`METADATA_AGENT_REPOSITORY_URL` nennt:

| Operation | Wofür |
|---|---|
| [POST /node](#post-node) | Eine Node-ID bekommen, bevor die Metadaten existieren |
| [POST /nodes](#post-nodes) | Einen Node anlegen oder ändern und den vollständigen Node zurückbekommen. Keine Dublettenprüfung, kein Screenshot, Workflow nur auf Wunsch |
| [POST /upload](#post-upload) | Einen fertigen Datensatz an die Redaktion übergeben: standardmäßig mit Dublettenprüfung, Vorschau-Screenshot und Prüf-Workflow |
| [POST /workflow/{node_id}](#post-workflownode_id) | Weitere Workflow-Status setzen |
| [POST /upload/verify/{node_id}](#post-uploadverifynode_id) | Prüfen, was das Repository tatsächlich gespeichert hat |

### Gemeinsame Regeln beim Schreiben

**Dienstkonto.** Alle fünf brauchen `WLO_GUEST_USERNAME` und
`WLO_GUEST_PASSWORD`. Ohne sie lautet die Antwort `503` `SERVICE_UNAVAILABLE`.

**Bearbeitungsfenster.** Ein bestehender Node lässt sich über diese API nur
bis **2 Stunden nach dem Anlegen** (`cm:created`) schreiben. Bearbeiten
verlängert das Fenster nicht. Danach hat der Inhalt in der Regel die Redaktion
erreicht, und spätere Änderungen gehören in die Redaktionsoberfläche. Das gilt
für `/nodes` und `/upload` mit `node_id` und für `/screenshot` mit `node_id`:

| Status | `error.code` | Bedeutung |
|---|---|---|
| `403` | `FORBIDDEN` | Der Node ist älter als 2 Stunden, oder seine Erstellungszeit ist nicht lesbar |
| `404` | `NOT_FOUND` | Der Node existiert nicht, oder das Dienstkonto hat keinen Zugriff |
| `502` | `UPSTREAM_ERROR` | Der Node war wegen eines Repository-Fehlers nicht lesbar. Erneut versuchen |

**Was geschrieben wird.** Nur Felder, die das Schema für das Repository
vorsieht: die von `core.json` und die des Inhaltstyps in `metadataset`. Vor
dem Schreiben wandelt der Agent einige Werte um:

| Eingabe | Geschrieben als |
|---|---|
| `cm:author: ["Max Müller"]` | `ccm:lifecyclecontributer_author` als vCard |
| Koordinaten in `schema:location[].geo` oder `schema:geo` | `cm:latitude`, `cm:longitude` |
| `ccm:custom_license` mit einem Creative-Commons-Link, zum Beispiel `https://creativecommons.org/licenses/by-sa/4.0/` | `ccm:commonlicense_key: ["CC_BY_SA"]` und `ccm:commonlicense_cc_version: ["4.0"]`. `publicdomain/zero` wird zu `CC_0`, `publicdomain/mark` zu `PDM`. Steht der Link in einem Satz oder nennt er das Land einer portierten Lizenz (`…/by/3.0/de/`), bleibt auch der Text erhalten |
| `ccm:custom_license` mit einer URI des Lizenzvokabulars unter `http://w3id.org/openeduhub/vocabs/` (oder `https://`), zum Beispiel `…/vocabs/licenses/CC_BY_40` | `ccm:commonlicense_key: ["CC_BY"]` und `ccm:commonlicense_cc_version: ["4.0"]` |
| `ccm:custom_license` mit Freitext oder einem nicht zuordenbaren Link, auch einem Link anderer Herkunft, der auf einen Lizenzschlüssel endet | Bleibt erhalten, mit `ccm:commonlicense_key: ["CUSTOM"]` |
| `ccm:commonlicense_key` in anderer Schreibweise: `CC BY-SA`, `cc-by-sa`, `CC0` | Der Schlüssel, den edu-sharing auflöst: `CC_BY_SA`, `CC_0`. Ein Schlüssel, den edu-sharing nicht kennt, wird entfernt. Ein CC-Schlüssel ohne Version erhält `4.0`, `CC_0` erhält `1.0` |
| Keine Lizenz | `ccm:commonlicense_key` bleibt leer. Der Agent setzt keinen Standard; die Redaktion entscheidet |
| Wahrheitswerte, verschachtelte Listen, Text aus lauter Leerzeichen | `"true"` oder `"false"`; eine flache Liste; nicht geschrieben |

Mit `write_extended_data` kommen drei Felder hinzu: `ccm:oeh_extendedType`
(URI des Inhaltstyps), `ccm:oeh_extendedData` (die Metadatenfelder des
Datensatzes als JSON, ohne die erweiterten Felder selbst) und
`ccm:oeh_extendedText` (`extended_text`, der Rohtext der Quelle, höchstens
150.000 Zeichen; `error` sagt, wenn er gekürzt wurde).

**Reihenfolge eines Schreibvorgangs.** Dublettenprüfung (nur `/upload`), Node
anlegen oder Bearbeitungsfenster prüfen, Metadaten, erweiterte Daten,
Vorschaubild, Sammlungsverweise, Workflow, Zurücklesen. Die Dublettenprüfung
fragt den Suchindex und liest die 25 neuesten Nodes des Eingangsordners, weil
der Index nachläuft. Prüfung und Anlegen laufen je URL nacheinander, innerhalb
eines Prozesses des Agents.

**Sammlungen.** `collection_id` ist immer eine Liste, auch bei einer einzelnen
Sammlung. Ein nackter String wird mit `422` abgelehnt. Die Einträge sind
Node-IDs oder Sammlungs-URLs, wie man sie aus der Redaktionsoberfläche kopiert
(`…/components/collections?id=<uuid>`). Der Node wird in der Sammlung
referenziert; das Original bleibt im Eingangsordner. Sammlungen aus dem
Datensatz (`virtual:collection_id_primary`, `ccm:collection_id`) kommen hinzu,
Dubletten entfallen. Die Antwort enthält in `collections` einen Eintrag je
Sammlung. Eine Sammlung, die den Node schon referenziert, gilt als verknüpft.

**Ergebnisse.** Ein Schreibvorgang hat drei Arten von Ausgang:

| Ausgang | Status | Body |
|---|---|---|
| Geschrieben | `200` | `success: true`. Ist ein späterer Schritt gescheitert (ein Feld, eine Sammlung, ein Workflow-Schritt, die Vorschau), nennt `error` ihn, und `field_errors`, `collections`, `workflow` oder `preview` zeigen die Einzelheiten. `error` auch bei `success: true` prüfen |
| Dublette, oder der Node existiert, aber das Schreiben brach danach ab | `200` | `success: false` mit `error` und `node`; bei einer Dublette auch `duplicate: true` |
| Nichts geschrieben | `422`, `502` | Fehlerform, siehe unten |

| Status | `error.code` | Bedeutung |
|---|---|---|
| `400` | `BAD_REQUEST` | Ungültiges JSON, Body ist kein Objekt |
| `422` | `VALIDATION_ERROR` | Der Body passt nicht zum Modell, enthält eine Option der anderen Schreiboperation oder einen unbekannten Schlüssel neben einem verschachtelten `metadata`, oder ein Feld verletzt die Objektform des Schemas. Es wurde nichts geschrieben |
| `422` | `SCHEMA_UNAVAILABLE` | Kontext oder Version des Datensatzes unbekannt, oder `metadataset` nennt kein ladbares Schema (ein Tippfehler, eine Datei, die es in der Version nicht gibt). Neben `error`: `step: "schema"`. Es wurde nichts geschrieben |
| `502` | `UPSTREAM_ERROR` | Das Repository hat den Node oder alle Metadaten abgelehnt oder war nicht erreichbar. Neben `error`: `discarded_node` (ein Node, den dieser Aufruf angelegt und wieder entfernt hat), `node` (ein Node, der sich nicht entfernen ließ), `step`, `field_errors` |
| `504` | `UPLOAD_TIMEOUT` | Das Schreiben hat `METADATA_AGENT_UPLOAD_TIMEOUT` überschritten. `discarded_node`: Der unvollständige Node, den dieser Aufruf angelegt hat, wurde entfernt. `node`: ein Node, der bleibt, weil eine Sammlung ihn schon referenzieren kann, er schon im Workflow ist, vollständig geschrieben ist, sich nicht entfernen ließ oder per `node_id` übergeben wurde. `collections`: nur wenn das Zeitlimit beim Verknüpfen der Sammlungen ablief; ein Eintrag je Sammlung, bei nicht verknüpften mit dem Grund in `error` |

Ein verworfener Node liegt im Papierkorb des Repositorys und lässt sich
wiederherstellen.

Antwortmodelle geben optionale Felder ohne Wert als `null` zurück. Die
Beispiele unten lassen sie weg.

### POST /node

Legt einen Node im Eingangsordner an und gibt seine ID zurück, ohne die
vollständigen Metadaten zu schreiben. Für Aufrufer, die eine ID brauchen,
bevor die Metadaten existieren: um sie anzuzeigen, zu verlinken oder
weiterzugeben. Die ID innerhalb von 2 Stunden als `node_id` an `/upload` oder
`/nodes` übergeben.

**Request.**

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `metadata` | object | Pflicht | Mindestens `cclom:title`. Übernommen werden außerdem `cclom:general_description`, `cclom:general_keyword`, `ccm:wwwurl`, `cclom:general_language`. Alles andere wird ignoriert |
| `check_duplicates` | boolean | `false` | Zuerst nach einem Node mit derselben `ccm:wwwurl` suchen |

Der Node erhält außerdem `ccm:linktype: USER_GENERATED`. Sein Name (`cm:name`)
kommt aus `cm:name` in den Metadaten, falls angegeben, sonst aus der URL, sonst
aus dem Titel. Ein Name aus den Metadaten oder dem Titel wird vorher
bereinigt: Die Zeichen `* " \ > < ? / : |` werden ersetzt, höchstens 200
Zeichen bleiben.

**Antwort `200`.**

```json
{
  "success": true,
  "node": {
    "nodeId": "5443240c-43a2-4961-8324-0c43a22961dd",
    "title": "Bruchrechnung Klasse 6",
    "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/5443240c-43a2-4961-8324-0c43a22961dd"
  }
}
```

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `200` | keiner | Angelegt (`success: true`, `node`), oder eine Dublette wurde gefunden (`success: false`, `duplicate: true`, `node` ist der bestehende Node). Konnte die Dublettenprüfung nicht laufen, wird der Node angelegt, und `error` sagt das |
| `400` | `BAD_REQUEST` | Kein Titel; ungültiges JSON, Body ist kein Objekt |
| `422` | `VALIDATION_ERROR` | `metadata` fehlt oder hat den falschen Typ |
| `502` | `UPSTREAM_ERROR` | Das Repository hat abgelehnt oder war nicht erreichbar |
| `503` | `SERVICE_UNAVAILABLE` | Kein Dienstkonto |
| `504` | `UPLOAD_TIMEOUT` | Zeitlimit überschritten. Ein trotzdem angelegter Node steht in `node`; entfernt wird hier nie |

**Beispiel.**

```bash
curl -s -X POST "$API/node" \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"cclom:title": "Bruchrechnung Klasse 6"}}'
```

### POST /nodes

Schreibt einen Datensatz ins Repository: legt einen Node an (ohne `node_id`)
oder aktualisiert einen (mit `node_id`). Die Antwort enthält immer den
vollständigen Node. Das ist der vorgesehene Weg, einen Node zu ändern, den der
Agent angelegt hat.

**Request.** Ein [Metadaten-Body](#metadaten-body) mit diesen Optionen:

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `node_id` | string | keiner | Node, der aktualisiert wird, innerhalb seines Bearbeitungsfensters. Nicht gesetzt: ein neuer Node im Eingangsordner |
| `collection_id` | string-Array | keiner | Sammlungen, in denen der Node referenziert wird |
| `write_extended_data` | boolean | `false` | Die drei erweiterten Felder schreiben |
| `extended_text` | string | keiner | Rohtext der Quelle. Nur mit `write_extended_data: true` |
| `preview` | string | keiner | Vorschaubild: eine `http(s)`-URL, die der Agent lädt, eine Data-URL oder nacktes Base64. PNG, JPEG, GIF oder WebP bis 10 MB; der Typ wird aus dem Bild selbst gelesen |
| `start_review_workflow` | boolean | `false` | Den Node zur Prüfung übergeben (`200_tocheck`) |
| `start_quality_workflow` | boolean | `false` | Die Qualität bestätigen (`140_ELEMENT_LEGALLY_APPROVED`), nach der Übergabe, wenn beide gesetzt sind |

Verhalten:

- **Ein Update schreibt nur, was im Body steht.** Leere Werte (`null`, `""`,
  Text aus lauter Leerzeichen, `[]`) gelten als nicht gesetzt und löschen
  nichts.
- Eine Option, die es nur bei `/upload` gibt (`check_duplicates`,
  `start_workflow`, `workflow_steps`, `preview_url`, `source` und die
  übrigen), wird mit `422` abgelehnt.
- `ccm:oeh_extendedData` ist ein JSON-Block und wird als Ganzes ersetzt.
  Deshalb ist `write_extended_data` hier standardmäßig aus. Einschalten, wenn
  der vollständige Datensatz gesendet wird.
- Ohne `preview` bleibt ein vorhandenes Vorschaubild. Ein Vorschaubild, das
  sich nicht laden lässt, macht das Schreiben nicht rückgängig: `success`
  bleibt `true`, der Grund steht in `preview.error` und in `error`.
- Ohne die beiden Workflow-Schalter landet der Node in keiner Warteschlange.

**Antwort `200`.**

```json
{
  "success": true,
  "node_created": false,
  "schema_used": "learning_material.json",
  "repo_fields_available": 40,
  "fields_written": 1,
  "fields_skipped": 0,
  "skipped_fields": [],
  "node": {
    "nodeId": "177fba93-3885-479e-bfba-933885c79e1c",
    "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/177fba93-3885-479e-bfba-933885c79e1c"
  },
  "node_full": {
    "ref": { "id": "177fba93-3885-479e-bfba-933885c79e1c" },
    "properties": { "cclom:general_keyword": ["Bruchrechnung", "Klasse 6"] }
  }
}
```

| Teil | Bedeutung |
|---|---|
| `node_created` | `true`, wenn dieser Aufruf den Node angelegt hat, `false` bei einem Update |
| `schema_used` | Das Schema des Inhaltstyps, das entschied, was geschrieben werden darf. `null`: Nur `core.json` galt |
| `repo_fields_available` | Anzahl der Felder, die dieses Schema schreiben darf. Deutlich weniger als erwartet heißt: falsches oder fehlendes `metadataset` |
| `fields_written`, `field_errors` | Ergebnis des Schreibens der Metadaten: Zahl der geschriebenen Properties und die Felder, die das Repository abgelehnt hat |
| `fields_skipped`, `skipped_fields` | Zahl der nicht geschriebenen Felder und die Namen derer, die mit einem Wert gesendet wurden, aber keine Property des Repositorys erreichen, meist weil das Schema sie nicht für das Repository vorsieht. Abgelehnte Felder zählen mit; sie stehen in `field_errors` |
| `node` | Kurzform: `nodeId`, `title`, `description`, `wwwurl`, `repositoryUrl` |
| `node_full` | Der vollständige edu-sharing-Node, wie ihn das Repository liefert (oben gekürzt). `null` nur, wenn das Zurücklesen scheiterte; das Schreiben gilt trotzdem |
| `preview`, `collections`, `workflow` | Nur wenn angefordert |

**Status und Fehlercodes.** Siehe
[Gemeinsame Regeln beim Schreiben](#gemeinsame-regeln-beim-schreiben):
`400`, `403`, `404`, `422`, `502`, `503`, `504`.

**Beispiel.**

```bash
# Update: Nur die Felder im Body ändern sich
curl -s -X POST "$API/nodes" \
  -H "Content-Type: application/json" \
  -d '{
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "node_id": "177fba93-3885-479e-bfba-933885c79e1c",
    "cclom:general_keyword": ["Bruchrechnung", "Klasse 6"]
  }'
```

### POST /upload

Lädt einen Datensatz für die Redaktion hoch. Standardmäßig prüft die
Operation auf eine Dublette, legt einen Node an, schreibt die Metadaten und die
erweiterten Daten, nimmt die Seite als Vorschaubild auf und übergibt den Node
zur Prüfung (`200_tocheck`).

**Request.** Ein [Metadaten-Body](#metadaten-body) mit diesen Optionen:

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `check_duplicates` | boolean | `true` | Den Upload ablehnen, wenn es einen Node mit derselben `ccm:wwwurl` gibt |
| `node_id` | string | keiner | In diesen Node schreiben, statt einen anzulegen, siehe unten |
| `collection_id` | string-Array | keiner | Sammlungen, in denen der Node referenziert wird |
| `source` | string | keiner | Herausgeber. Ersetzt `ccm:oeh_publisher_combined` |
| `write_extended_data` | boolean | `true` | Die drei erweiterten Felder schreiben |
| `extended_text` | string | keiner | Rohtext der Quelle für `ccm:oeh_extendedText`, zum Beispiel `_source_text` der `/generate`-Antwort. Er wird nicht von selbst aus dem Datensatz übernommen |
| `preview_url` | string | `ccm:wwwurl` des Datensatzes | Seite, die als Vorschaubild aufgenommen wird. Ohne jede URL gibt es keine Vorschau |
| `screenshot_method` | string | `playwright` | `playwright` oder der alte Alias `pageshot` |
| `start_workflow` | boolean | `true` | Die Workflow-Schritte ausführen |
| `workflow_steps` | string-Array | `["200_tocheck"]` | Status, die der Reihe nach gesetzt werden, siehe [POST /workflow/{node_id}](#post-workflownode_id). Unbekannte Status: `422` |
| `workflow_comment` | string | `Upload via Metadata Agent API` | Kommentar jedes Schritts |
| `workflow_receiver` | string-Array | siehe unten | Authority-Namen, die bei jedem Schritt benachrichtigt werden |
| `return_full_node` | boolean | `false` | Den Node zurücklesen und als `node_full` mitliefern |
| `repository` | string | `staging` | Veraltet und ignoriert |

Leere Werte: Ein leerer String oder eine leere Liste gilt bei `node_id`,
`collection_id`, `source`, `preview_url`, `extended_text`, `workflow_comment`
und `screenshot_method` als „nicht angegeben“. Zwei Felder weichen ab:

| Feld | Eine leere Liste bedeutet |
|---|---|
| `workflow_receiver` | Niemanden benachrichtigen. Nicht gesetzt: die Gruppe `GROUP_ORG_WLO-Uploadmanager` bei `200_tocheck`, niemand bei anderen Status |
| `workflow_steps` | Wird mit `422` abgelehnt. Für einen Upload ohne Workflow `start_workflow: false` setzen |

Die Optionen, die es nur bei `/nodes` gibt (`preview`,
`start_review_workflow`, `start_quality_workflow`), werden mit `422`
abgelehnt.

**Zweistufiger Upload mit `node_id`.** Den Node mit [POST /node](#post-node)
anlegen, dann den Datensatz mit dessen ID hierher senden:

| | Ohne `node_id` | Mit `node_id` |
|---|---|---|
| Dublettenprüfung | läuft | entfällt; der Node trägt die URL schon |
| Node nach einem Fehler | wird entfernt, außer eine Sammlung oder der Workflow kann ihn schon führen | bleibt; er gehört dem Aufrufer |
| Bearbeitungsfenster | keines | 2 Stunden nach dem Anlegen |
| `node_created` in der Antwort | `true` | `false` |

**Antwort `200`.**

```json
{
  "success": true,
  "node_created": true,
  "schema_used": "event.json",
  "repo_fields_available": 33,
  "fields_written": 12,
  "fields_skipped": 0,
  "skipped_fields": [],
  "node": {
    "nodeId": "3f2b8c1e-7a4d-4e6f-9b0a-1c2d3e4f5a6b",
    "title": "Workshop KI in der Bildung",
    "description": "Ein Workshop über den Einsatz von KI im Unterricht.",
    "wwwurl": "https://example.com/workshop",
    "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/3f2b8c1e-7a4d-4e6f-9b0a-1c2d3e4f5a6b"
  },
  "preview": { "success": true, "method": "playwright", "capture_time_ms": 2800, "size_bytes": 245000 },
  "collections": [
    { "collectionId": "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9", "success": true }
  ],
  "workflow": [
    { "status": "200_tocheck", "success": true }
  ]
}
```

Die Felder haben die bei [POST /nodes](#post-nodes) beschriebene Bedeutung.
Zusätzlich:

| Teil | Bedeutung |
|---|---|
| `duplicate` | `true`, wenn die URL schon existiert. Dann `success: false`, `node` ist der bestehende Node, und `error` nennt ihn, mit seinem Titel oder, ohne einen, als `Node <id>` |
| `preview` | Ergebnis des Screenshots: `success`, `method`, `capture_time_ms`, `size_bytes`, oder `success: false` mit `error`. Eine gescheiterte Vorschau macht den Upload nicht rückgängig |
| `collections` | Ein Eintrag je Sammlung: `collectionId`, `success`, `error` |
| `workflow` | Ein Eintrag je Schritt: `status`, `success`, `error`; `skipped: true` bei Schritten, die nicht gesendet wurden, weil ein früherer scheiterte |
| `node_full` | Nur mit `return_full_node: true`. Felder in edu-sharing-Notation: `ref.id`, `properties["cclom:title"][0]` |
| `field_errors` | Felder, die das Repository abgelehnt hat: `field_id`, `error`, `status_code` |

Mehr zur Antwort: [UPLOAD-RESPONSE.md](../UPLOAD-RESPONSE.md). Sammlungen und
Workflow für Entwickler:
[ENTWICKLER-UEBERSICHT.md](../ENTWICKLER-UEBERSICHT.md).

Eine Dublette:

```json
{
  "success": false,
  "duplicate": true,
  "node": {
    "nodeId": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
    "title": "Workshop KI in der Bildung",
    "wwwurl": "https://example.com/workshop",
    "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
  },
  "error": "URL existiert bereits: \"Workshop KI in der Bildung\""
}
```

**Status und Fehlercodes.** Siehe
[Gemeinsame Regeln beim Schreiben](#gemeinsame-regeln-beim-schreiben):
`400`, `422`, `502`, `503`, `504`; mit `node_id` auch `403` und `404`.

**Beispiel.**

```bash
curl -s -X POST "$API/upload" \
  -H "Content-Type: application/json" \
  -d '{
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "cclom:title": "Workshop KI in der Bildung",
    "ccm:wwwurl": "https://example.com/workshop",
    "ccm:oeh_event_begin": "2026-03-15T09:00",
    "collection_id": ["3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"]
  }'
```

### POST /workflow/{node_id}

Setzt Workflow-Status an einem bestehenden Node, einen nach dem anderen.
edu-sharing führt jeden Schritt als eigenen Eintrag in der Workflow-Historie
des Nodes, mit Status, Kommentar, Zeitpunkt und ausführendem Nutzer. Deshalb
werden die Status Schritt für Schritt gesetzt und nicht in einem Sprung. Der
ausführende Nutzer ist das Dienstkonto. Für diese Operation gilt kein
Bearbeitungsfenster.

**Request.** `node_id` im Pfad muss eine Node-UUID sein.

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `steps` | string oder string-Array | Pflicht | Status, die der Reihe nach gesetzt werden. Mindestens einer |
| `comment` | string | `Upload via Metadata Agent API` | Kommentar jedes Schritts. Ein leerer String gilt als nicht angegeben |
| `receiver` | string-Array | `["GROUP_ORG_WLO-Uploadmanager"]` bei `200_tocheck`, sonst niemand | Authority-Namen, die benachrichtigt werden. Eine leere Liste: niemand |

Status des WLO-Redaktions-Workflows:

| Status | Bedeutung |
|---|---|
| `100_unchecked` | Ungeprüft |
| `110_METADATA_RECORD_REQUESTED` | Metadaten-Erfassung angefordert |
| `120_METADATA_QUALITY_CONFIRMED` | Metadaten-Qualität bestätigt |
| `125_METADATA_QUALITY_FOR_BUFFET` | Für das Redaktionsbuffet qualifiziert |
| `130_ELEMENT_REJECTED` | Element abgelehnt |
| `140_ELEMENT_LEGALLY_APPROVED` | Qualität bestätigt, Element freigegeben |
| `150_PUBLISH_IN_SEARCH` | In der Suche veröffentlicht |
| `160_REMOVE_FROM_SEARCH` | Aus der Suche entfernt |
| `200_tocheck` | Zur Prüfung übergeben (Standard nach `/upload`) |
| `TASK_CREATE_TREE`, `TASK_CHECK_COLLECTION_PROPOSAL`, `TASK_CHECK_QUALITY` | Aufgaben-Status |

**Antwort `200`.**

```json
{
  "success": true,
  "nodeId": "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9",
  "steps": [
    { "status": "140_ELEMENT_LEGALLY_APPROVED", "success": true }
  ],
  "current_status": "140_ELEMENT_LEGALLY_APPROVED",
  "history": [
    { "status": "140_ELEMENT_LEGALLY_APPROVED", "comment": "Redaktionell geprüft", "time": 1786343291000 }
  ],
  "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
}
```

| Teil | Bedeutung |
|---|---|
| `steps` | Ergebnis je Schritt. Nach einem gescheiterten Schritt werden die übrigen nicht gesendet und mit `skipped: true` aufgeführt |
| `current_status` | `ccm:wf_status` des Nodes, nach den Schritten zurückgelesen |
| `history` | Workflow-Historie des Nodes, so weitergegeben, wie das Repository sie liefert. Neben Status, Kommentar und Zeitpunkt nennt jeder Eintrag den ausführenden Nutzer |
| `error` | Gesetzt, wenn ein Schritt scheiterte oder das Repository nicht erreichbar war |

Scheitert eine der beiden Leseoperationen, fehlt ihr Feld. Die ausgeführten
Schritte gelten trotzdem.

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `200` | keiner | Ergebnis im Body. `success: false`, wenn ein Schritt scheiterte oder das Repository nicht erreichbar war |
| `400` | `BAD_REQUEST` | `node_id` ist keine UUID, Body fehlt, ungültiges JSON oder kein Objekt |
| `404` | `NOT_FOUND` | Node unbekannt oder nicht sichtbar. Es wird kein Status gesetzt |
| `422` | `VALIDATION_ERROR` | Unbekannter Status, `steps` leer oder nicht vorhanden |
| `503` | `SERVICE_UNAVAILABLE` | Kein Dienstkonto |
| `504` | `UPLOAD_TIMEOUT` | Zeitlimit überschritten. `node` nennt den Node; einzelne Schritte können schon gesetzt sein |

**Beispiel.**

```bash
curl -s -X POST "$API/workflow/3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9" \
  -H "Content-Type: application/json" \
  -d '{"steps": ["140_ELEMENT_LEGALLY_APPROVED"], "comment": "Redaktionell geprüft"}'
```

### POST /upload/verify/{node_id}

Liest einen Node aus dem Repository und meldet, welche seiner Werte das
Repository nicht auflöst. Mit `expected_metadata` vergleicht die Operation den
Node außerdem Feld für Feld mit dem Gesendeten. Ein `200` beim Schreiben
beweist nicht, dass jeder Wert als brauchbarer Wert angekommen ist; diese
Operation zeigt es.

**Request.** `node_id` im Pfad muss eine Node-UUID sein. Der Body ist
optional.

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `expected_metadata` | object | keiner | Der gesendete Datensatz, zum Beispiel die Antwort von `/generate`, in jeder Form, die `/upload` annimmt: flach, unter `metadata` oder unter `metadata.metadata`. Optionen von `/upload` und `/nodes` neben den Feldern werden nicht verglichen. Nicht gesetzt: kein Vergleich |
| `repository` | string | `staging` | Veraltet und ignoriert |

Ein Body, der vorhanden, aber fehlerhaft ist, wird abgelehnt. Er führt nie zu
einem bloßen Lesen, weil das einen Vergleich melden würde, der nicht
stattgefunden hat.

**Antwort `200`.**

```json
{
  "success": true,
  "node_id": "3f2b8c1e-7a4d-4e6f-9b0a-1c2d3e4f5a6b",
  "actual_metadata": {
    "cclom:title": "Workshop KI in der Bildung",
    "ccm:oeh_lrt": "http://w3id.org/openeduhub/vocabs/new_lrt/does-not-exist"
  },
  "unresolved": [
    {
      "field_id": "ccm:oeh_lrt",
      "value": ["http://w3id.org/openeduhub/vocabs/new_lrt/does-not-exist"],
      "known": false,
      "reason": "leeres _DISPLAYNAME — der Wert steht in keinem Vokabular"
    }
  ],
  "diff": [
    {
      "field_id": "cclom:general_keyword",
      "status": "missing_in_repo",
      "expected": ["KI", "Bildung"]
    },
    {
      "field_id": "ccm:oeh_lrt",
      "status": "extra_in_repo",
      "actual": "http://w3id.org/openeduhub/vocabs/new_lrt/does-not-exist"
    },
    {
      "field_id": "cclom:title",
      "status": "match",
      "expected": "Workshop KI in der Bildung",
      "actual": "Workshop KI in der Bildung"
    }
  ],
  "summary": {
    "match": 1,
    "mismatch": 0,
    "missing_in_repo": 1,
    "extra_in_repo": 1,
    "not_written": 0,
    "unresolved": 0
  }
}
```

| Teil | Bedeutung |
|---|---|
| `actual_metadata` | Die Felder des Nodes als flache Metadaten |
| `unresolved` | Werte, die das Repository speichert, aber nicht als Werte liest. Ein Vokabularfeld braucht ein gefülltes `<feld>_DISPLAYNAME`, `ccm:commonlicense_key` braucht `virtual:licenseurl`. Gemeldet mit und ohne `expected_metadata`. Eine leere Liste: Alles löst auf |
| `unresolved[].known` | `true` bei Feldern, für die das Repository keinen Wertebereich hat (`ccm:oeh_quality_correctness`, die drei `ccm:commonlicense_ai_*`). Sie sind korrekt gespeichert und erscheinen an jedem Node. Echte Befunde stehen zuerst |
| `diff`, `summary` | Nur mit `expected_metadata`. Probleme zuerst |
| `diff[].resolution` | `resolved`, `unresolved` oder `null` (nichts aufzulösen). Unabhängig von `status`: Ein Feld kann ein `match` sein und trotzdem nicht auflösen |
| `summary` | Die Zahl der Felder je `status`. `unresolved` zählt daneben die verglichenen Felder, deren Wert das Repository nicht auflöst; ein solches Feld kann zugleich ein `match` sein |

| `status` | Bedeutung |
|---|---|
| `match` | Erwarteter und gespeicherter Wert stimmen überein. Der Vergleich ist tolerant: Ein einzelner Wert gleicht einer Liste mit einem Eintrag, die Reihenfolge einer Liste zählt nicht, ein Datum darf als Millisekunden gespeichert sein. Groß- und Kleinschreibung wird bei Vokabularwerten und dem Lizenzschlüssel ignoriert, in Freitext nicht. Ein Feld, das der Agent in anderer Form schreibt, wird in dieser Form verglichen: `oeh:new_lrt` als `ccm:oeh_lrt`, ein Lizenz-Link in `ccm:custom_license` als Schlüssel und Version, `cm:author` über die Namen |
| `mismatch` | Der Wert wurde geschrieben, weicht aber ab |
| `missing_in_repo` | Erwartet, aber nicht am Node |
| `extra_in_repo` | Am Node, aber nicht erwartet. Properties, die der Agent aus einem gesendeten Feld ableitet (Lizenzschlüssel und -version, die vCard des Autors), zählen nicht |
| `not_written` | Das Feld wird nie geschrieben. Es ist kein Repository-Feld des Schemas, weder unter seinem Namen noch unter dem, unter dem es geschrieben wird (`oeh:new_lrt` zählt als `ccm:oeh_lrt`), oder es ist ein `virtual:`-Feld, das das Repository selbst berechnet. Sind die Repository-Felder des Schemas nicht lesbar, gilt das für jedes Feld |

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `400` | `BAD_REQUEST` | `node_id` ist keine UUID, ungültiges JSON, Body ist kein Objekt |
| `404` | `NOT_FOUND` | Node unbekannt, oder das Dienstkonto hat keinen Zugriff |
| `422` | `VALIDATION_ERROR` | Der Body passt nicht, zum Beispiel ist `schemaVersion` eine Zahl |
| `502` | `UPSTREAM_ERROR` | Das Repository konnte den Node nicht liefern |
| `503` | `SERVICE_UNAVAILABLE` | Kein Dienstkonto |

**Beispiel.**

```bash
curl -s -X POST "$API/upload/verify/3f2b8c1e-7a4d-4e6f-9b0a-1c2d3e4f5a6b" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_metadata": {
      "contextName": "default",
      "schemaVersion": "2.0.0",
      "metadataset": "event.json",
      "cclom:title": "Workshop KI in der Bildung",
      "cclom:general_keyword": ["KI", "Bildung"]
    }
  }'
```

## Screenshot

### POST /screenshot

Nimmt eine Webseite mit einem lokalen Chromium-Browser auf und gibt das Bild
zurück. Mit `node_id` wird das Bild außerdem als Vorschaubild dieses Nodes
gespeichert. Die Aufnahme läuft im Agent; ein externer Screenshot-Dienst ist
nicht beteiligt.

**Request.**

| Name | Typ | Standard | Bedeutung |
|---|---|---|---|
| `url` | string | Pflicht | Seite, die aufgenommen wird. [Nur öffentliche URLs](#nur-öffentliche-urls) |
| `method` | string | `playwright` | `playwright` oder der alte Alias `pageshot`. Beide nehmen lokal auf; die Antwort nennt `playwright` |
| `width` | integer | `1920` | Breite des Viewports, 320 bis 3840 |
| `height` | integer | `1080` | Höhe des Viewports, 200 bis 2160 |
| `format` | string | `png` | `png` oder `jpeg`. Andere Werte werden als PNG geliefert und ausgewiesen |
| `full_page` | boolean | `false` | Die ganze scrollbare Seite aufnehmen |
| `delay` | integer | `2000` | Wartezeit vor der Aufnahme in ms, 0 bis 10000 |
| `node_id` | string | keiner | Das Bild als Vorschau dieses Nodes speichern. Muss eine UUID sein |
| `repository` | string | `staging` | Veraltet und ignoriert |

Aufgenommen wird, sobald das HTML der Seite geladen ist und das Netz ruht. Auf
die Ruhe wartet der Agent höchstens 10 s und nimmt danach trotzdem auf, dann
folgt `delay`. Führt die Seite, eine Weiterleitung, ein Frame oder ein Bild zu
einer internen Adresse, wird die Aufnahme verworfen.

**Antwort `200`.**

```json
{
  "success": true,
  "method": "playwright",
  "url": "https://example.com",
  "format": "png",
  "mimetype": "image/png",
  "width": 1920,
  "height": 1080,
  "size_bytes": 245000,
  "capture_time_ms": 2800,
  "image_base64": "iVBORw0KGgo…"
}
```

`success` sagt, dass die Aufnahme gelungen ist. Ohne `node_id` in der Anfrage
sind `preview_uploaded`, `node_id` und `error` `null`; das Beispiel lässt sie
weg. Mit `node_id` sagt `preview_uploaded`, ob das Bild gespeichert wurde, und
`node_id` wiederholt den Node. Lehnt das Repository das Bild ab, bleibt die
Antwort ein `200` mit dem Bild, `preview_uploaded: false` und dem Grund in
`error`. Ohne das Dienstkonto wird eine Anfrage mit `node_id` vor der Aufnahme
mit `503` beantwortet.

**Status und Fehlercodes.**

| Status | `error.code` | Bedeutung |
|---|---|---|
| `400` | `BAD_REQUEST` | Die URL, eine Weiterleitung oder ein Frame ist nicht öffentlich; ungültiges JSON, Body ist kein Objekt |
| `403`, `404` | `FORBIDDEN`, `NOT_FOUND` | Mit `node_id`: außerhalb des Bearbeitungsfensters oder Node unbekannt. Geprüft vor der Aufnahme |
| `422` | `VALIDATION_ERROR` | Ein Feld passt nicht, zum Beispiel `width` außerhalb des Bereichs oder eine `url` über 8.192 Zeichen |
| `500` | `INTERNAL_ERROR` | Die Aufnahme scheiterte unerwartet |
| `502` | `UPSTREAM_ERROR` | Die Seite ließ sich nicht innerhalb von 90 s laden oder aufnehmen. Mit `node_id` auch: Das Repository war nicht lesbar |
| `503` | `SERVICE_UNAVAILABLE` | Mit `node_id`: kein Dienstkonto. Geprüft vor der Aufnahme |
| `504` | `UPLOAD_TIMEOUT` | Mit `node_id`: Aufnahme und Speichern haben `METADATA_AGENT_UPLOAD_TIMEOUT` überschritten. `node` nennt den Node |

**Beispiel.**

```bash
curl -s -X POST "$API/screenshot" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "format": "jpeg", "width": 1280, "height": 800}'
```

## Schema-Informationen

Drei lesende Operationen beschreiben die Schemata, die der Agent kennt. Sie
brauchen weder LLM noch Repository. Ein unbekannter Kontext, eine unbekannte
Version oder Schemadatei wird mit `404` `NOT_FOUND` beantwortet, ein
Pfadparameter über seiner Längengrenze (siehe
[Grenzen und Zeitlimits](#grenzen-und-zeitlimits)) mit `422`.

### GET /info/schemata

Listet die Kontexte und ihre Versionen.

**Antwort `200`.**

```json
{
  "contexts": [
    {
      "name": "default",
      "display_name": "WLO/OEH Standard",
      "versions": ["1.8.1", "1.8.0", "2.0.0"],
      "default_version": "2.0.0"
    },
    {
      "name": "mds_oeh",
      "display_name": "MDS OEH",
      "versions": ["1.8.1", "1.8.0", "2.0.0"],
      "default_version": "2.0.0"
    }
  ],
  "default_context": "default"
}
```

`default_version` ist das, was `latest` bedeutet.

```bash
curl -s "$API/info/schemata"
```

### GET /info/schemas/{context}/{version}

Listet die Schemadateien eines Kontexts und einer Version. `version` darf
`latest` sein.

**Antwort `200`.** Hier auf zwei Schemadateien gekürzt.

```json
[
  {
    "file": "core.json",
    "profile_id": "core:descriptive",
    "label": { "de": "core.json", "en": "core.json" },
    "groups": ["description", "typification", "quality", "ai_provenance"],
    "field_count": 27
  },
  {
    "file": "event.json",
    "profile_id": "type:event",
    "label": { "de": "Veranstaltung", "en": "Event" },
    "groups": ["event-typification", "schedule", "attendance", "roles", "education", "license"],
    "field_count": 22
  }
]
```

`label` ist der Name des Inhaltstyps, wie in der Liste `available` von
[POST /detect-content-type](#post-detect-content-type). Bei `core.json`, das
kein Inhaltstyp ist, ist es der Dateiname.

```bash
curl -s "$API/info/schemas/default/latest"
```

### GET /info/schema/{context}/{version}/{schema_file}

Gibt eine Schemadefinition als JSON zurück: `profileId`, `version`,
`@context`, `groups`, `fields` und `output_template`. Jedes Feld hat `id`,
`group`, `label`, `description`, `prompt` und `system` (Datentyp, `required`,
`multiple`, `repo_field`, Vokabular).

**Antwort `200`.** `event.json`, gekürzt auf ein Präfix, eine Gruppe, ein Feld
und drei Schlüssel der Vorlage.

```json
{
  "profileId": "type:event",
  "version": "2.0.0",
  "@context": { "oeh": "http://w3id.org/openeduhub/vocabs/" },
  "groups": [
    {
      "id": "schedule",
      "icon": "calendar_today",
      "label": { "de": "Termin & Status", "en": "Schedule & Status" }
    }
  ],
  "fields": [
    {
      "id": "ccm:oeh_event_begin",
      "group": "schedule",
      "label": { "de": "Start (Datum/Zeit)", "en": "Start (Date/Time)" },
      "description": {
        "de": "z.B. 2026-09-15T20:00:00.000Z",
        "en": "e.g. 2026-09-15T20:00:00.000Z"
      },
      "prompt": {
        "de": "Extrahiere das Startdatum und ggf. die Startzeit der Veranstaltung. Konvertiere IMMER ins vollständige ISO 8601-Format mit UTC-Zeitzone: YYYY-MM-DDTHH:mm:ss.000Z. Wenn nur ein Datum ohne Uhrzeit angegeben ist, verwende T00:00:00.000Z. Achte auf deutsche Datumsformate (TT.MM.JJJJ) und wandle korrekt um. Beispiele: '2026-09-15T20:00:00.000Z' (bei '15.09.2026, 20:00 Uhr'), '2026-03-01T00:00:00.000Z' (bei '1. März 2026'), '2026-10-10T09:30:00.000Z' (bei '10.10.2026 ab 9:30').",
        "en": "Extract the start date and optionally start time of the event. ALWAYS convert to full ISO 8601 format with UTC timezone: YYYY-MM-DDTHH:mm:ss.000Z. If only a date without time is given, use T00:00:00.000Z. Watch for European date formats (DD.MM.YYYY) and convert correctly. Examples: '2026-09-15T20:00:00.000Z' (for 'Sep 15, 2026 at 8pm'), '2026-03-01T00:00:00.000Z' (for 'March 1, 2026'), '2026-10-10T09:30:00.000Z' (for '10/10/2026 starting 9:30am')."
      },
      "system": {
        "path": "ccm:oeh_event_begin",
        "uri": "ccm:oeh_event_begin",
        "datatype": "datetime",
        "multiple": false,
        "required": false,
        "ask_user": true,
        "ai_fillable": true,
        "index": { "keyword": true },
        "validation": {
          "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{3}Z$"
        },
        "normalization": { "trim": true },
        "repo_field": true
      }
    }
  ],
  "output_template": {
    "oeh:eventType": [],
    "ccm:oeh_event_begin": "",
    "ccm:oeh_event_end": ""
  }
}
```

```bash
curl -s "$API/info/schema/default/latest/event.json"
```

## Endpunkte der Webkomponente

Der Agent liefert die Webkomponente `<metadata-agent-canvas>` selbst aus.
Attribute, Layouts und Events beschreibt
[WIDGET-REFERENZ.md](../WIDGET-REFERENZ.md).

| Pfad | Inhalt |
|---|---|
| `GET /widget/info` | Einbindung der Komponente, als JSON |
| `GET /widget/i18n/{lang}.json` | Oberflächentexte. `lang` ist `de` oder `en`, sonst `404` (`422` bei mehr als 8 Zeichen). `Cache-Control: public, max-age=3600` |
| `GET /widget/dist/…` | Die gebaute Komponente: `runtime.js`, `polyfills.js`, `main.js`, `styles.css`, Schriften |
| `GET /widget/examples/…` | Beispielseiten, zum Beispiel `full.html`, `detail.html`, `minimal.html` |

`GET /widget/info` liefert diese Schlüssel:

| Schlüssel | Inhalt |
|---|---|
| `name`, `version`, `description` | `metadata-agent-canvas`, die Version des Agents |
| `dist_base_url`, `scripts` | Basis der Dateien (`dist`) und die Listen `required` (`dist/runtime.js`, `dist/polyfills.js`, `dist/main.js`), `styles`, `fonts` |
| `variants` | `full`, `detail`, `minimal`, jeweils mit `description`, `example_url` und einem HTML-`snippet` |
| `layouts`, `layout_aliases` | Layoutnamen und ihre Aliase |
| `attributes`, `events`, `incoming_events` | Attribute und Events mit Kurzbeschreibung |
| `examples`, `example_data` | URLs der Beispielseiten (`examples/full.html`) und eines Beispieldatensatzes |

Die URLs in der Antwort sind relative Verweise. Aufzulösen sind sie gegen die
Adresse, unter der `/widget/info` aufgerufen wurde: Aus `dist/main.js` wird
`…/widget/dist/main.js`, auch über den Proxy. In den HTML-Snippets steht der
Platzhalter `{AGENT_URL}` für die Basis-URL des Agents; vor der Verwendung
eines Snippets ist er zu ersetzen.

**Antwort `200` von `GET /widget/info`.** Gekürzt auf eine Variante, ein
Layout, zwei Attribute, ein Event und drei Beispielseiten.

```json
{
  "name": "metadata-agent-canvas",
  "version": "4.0.4",
  "description": "Angular Web Component zur Anzeige und Bearbeitung von Metadaten. Kann als <metadata-agent-canvas> Tag in beliebige Webanwendungen eingebettet werden. Die URLs dieser Antwort sind relativ zur Adresse von /widget/info; {AGENT_URL} in den Snippets steht für die Basis-URL des Dienstes.",
  "dist_base_url": "dist",
  "scripts": {
    "required": ["dist/runtime.js", "dist/polyfills.js", "dist/main.js"],
    "styles": ["dist/styles.css"],
    "fonts": []
  },
  "variants": {
    "minimal": {
      "description": "Kompakte, randlose Variante ohne Statusbar. Für Einbettung in bestehende Formulare oder Sidebars.",
      "example_url": "examples/minimal.html",
      "snippet": "<link rel=\"stylesheet\" href=\"{AGENT_URL}/widget/dist/styles.css\">\n<script src=\"{AGENT_URL}/widget/dist/runtime.js\" defer></script>\n<script src=\"{AGENT_URL}/widget/dist/polyfills.js\" defer></script>\n<script src=\"{AGENT_URL}/widget/dist/main.js\" defer></script>\n\n<metadata-agent-canvas\n  api-url=\"{AGENT_URL}\"\n  layout=\"clean\"\n  show-input-area=\"true\"\n  show-status-bar=\"false\"\n  show-floating-controls=\"false\">\n</metadata-agent-canvas>"
    }
  },
  "layouts": {
    "default": "Vollständige Bearbeitung — Eingabe, Statusbar, Floating Controls, Footer."
  },
  "layout_aliases": { "dialog": ["modal", "review", "redaktion"] },
  "attributes": {
    "layout": "Layout-Variante, siehe 'layouts'. Aliase siehe 'layout_aliases'.",
    "context-name": "Schema-Kontext: 'default' oder 'mds_oeh'."
  },
  "events": {
    "metadataSubmit": "Beim Absenden/Speichern. event.detail enthält die vollständigen Metadaten."
  },
  "incoming_events": {
    "plugin-extract": "Extraktion von außen anstoßen: canvas.dispatchEvent(new CustomEvent('plugin-extract', {detail: {text, url, inputMode, reset}}))."
  },
  "examples": {
    "full": "examples/full.html",
    "detail": "examples/detail.html",
    "minimal": "examples/minimal.html"
  },
  "example_data": { "metadata_json": "examples/metadata-2026-02-11.json" }
}
```

**Antwort `200` von `GET /widget/i18n/{lang}.json`.** `de.json`, gekürzt auf
zwei ihrer Gruppen.

```json
{
  "COMMON": {
    "CANCEL": "Abbrechen",
    "CLOSE": "Schließen",
    "CONFIRM": "Bestätigen"
  },
  "LANGUAGE": {
    "SWITCH_TO_ENGLISH": "Switch to English",
    "SWITCH_TO_GERMAN": "Zu Deutsch wechseln"
  }
}
```

```bash
curl -s "$API/widget/info"
curl -s "$API/widget/i18n/de.json"
```

## Betriebsendpunkte

Diese Endpunkte stehen nicht in `/openapi.json`.

| Endpunkt | Port 8000 | Interner Port (`METADATA_AGENT_INTERNAL_PORT`) |
|---|---|---|
| `GET /health` | ja, ohne Angaben zur Konfiguration | ja, mit `llm_provider` und `b_api_auth_mode` |
| `GET /ready` | ja, ohne Gründe | ja, mit `problems` |
| `GET /metrics` | nein (`404`) | ja |
| `GET /debug/auth`, `GET /debug/dependencies` | nur mit `METADATA_AGENT_DEBUG_ENDPOINTS=true`, sonst `404` | nein |

Der interne Port ist ein zweiter Server im selben Prozess, den der Ingress
nicht freigibt. Ohne `METADATA_AGENT_INTERNAL_PORT` gibt es ihn nicht und damit
auch kein `/metrics`. Siehe [DEPLOYMENT](../DEPLOYMENT.de.md) und
[UEBERGABE.md](../UEBERGABE.md).

### GET /health

Liveness: Der Prozess antwortet. Über die abhängigen Dienste sagt das nichts.

```json
{ "status": "healthy", "version": "4.0.4" }
```

```bash
curl -s "$API/health"
```

### GET /ready

Readiness: Mit dieser Konfiguration kann der Agent arbeiten. Andere Dienste
ruft der Endpunkt nicht auf.

```json
{ "status": "ready" }
```

`503` `NOT_READY`, wenn die Konfiguration unvollständig ist:

| Grund | Bedingung |
|---|---|
| Keine Schema-Kontexte geladen | immer geprüft |
| `B_API_KEY` fehlt | B-API-Provider mit `METADATA_AGENT_B_API_AUTH_MODE=static` |
| `OPENAI_API_KEY` fehlt | Provider `openai` |
| `WLO_GUEST_USERNAME` oder `WLO_GUEST_PASSWORD` fehlt | außer mit `METADATA_AGENT_REQUIRE_REPOSITORY=false` (ein Deployment, das nur erschließt) |

Das `503` hat die Fehlerform und zusätzlich `"status": "not_ready"`. Die Gründe
stehen im Log und, als `problems`, auf dem internen Port.

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$API/ready"
```

### GET /metrics

Prometheus-Metriken, nur auf dem internen Port. Neben den Prozessmetriken:

| Metrik | Labels |
|---|---|
| `metadata_agent_http_requests_total`, `…_duration_seconds` | `method`, `route` (die Routenvorlage, nie eine ID), `status` (auch `499`) |
| `metadata_agent_generations_total` | `status`: `success`, `partial`, `failed`, `cancelled` (der Aufrufer ist gegangen), `refused` (`TOO_MANY_GENERATIONS`) |
| `metadata_agent_llm_requests_total`, `…_duration_seconds` | Der Zähler nach Provider, Ergebnis (`ok`, `cancelled` oder der Fehlercode), `auth_source` und `config`; das Histogramm nur nach Provider. Es misst, was der Aufrufer eines KI-Aufrufs wartet: auf einen freien Platz innerhalb der LLM-Grenzen des Prozesses, auf die Anfrage, ihre Wiederholungen und die Pausen dazwischen |
| `metadata_agent_llm_last_failure_timestamp_seconds` | Die Labels des LLM-Zählers; Unix-Zeit des letzten KI-Aufrufs, der mit einem Fehler endete |
| `metadata_agent_http_last_internal_error_timestamp_seconds` | `route`; Unix-Zeit der letzten Antwort `500` |
| `metadata_agent_llm_tokens_total` | Provider und Art des Tokens: `prompt`, `cached` (aus dem Prompt-Cache gelesen), `cache_write` (hineingeschrieben), `completion` |
| `metadata_agent_upstream_requests_total`, `…_responses_total`, `…_response_duration_seconds` | `service`: `llm`, `text-extraction`, `repository`, `geocoder`, `other`. Die Antworten auch nach `status`; bei `other` nach Statusklasse (`2xx`, `4xx`) |

Das Histogramm der Anfragedauer hat Buckets bis 300 s, ab 120 s alle 30 s
(150 bis 270 s); daran lässt sich der Anteil langsamer `/generate`-Läufe
ablesen.

```bash
# Das Helm-Chart setzt den internen Port auf 9000
curl -s "http://localhost:9000/metrics"
```

### GET /debug/auth und GET /debug/dependencies

Hilfen beim Einrichten eines Deployments. Es gibt sie nur mit
`METADATA_AGENT_DEBUG_ENDPOINTS=true`; sonst antworten sie mit `404`. Danach
wieder ausschalten.

`GET /debug/auth` zeigt, ob ein durchgereichter Schlüssel ankommt. Der
Endpunkt zeigt nie einen Schlüssel, nur Vorhandensein, Schema und Länge:

```json
{
  "mode": "auto",
  "Authorization": { "present": true, "scheme": "Bearer", "length": 36 },
  "X-API-KEY": { "present": false },
  "b_api_key_configured": true,
  "llm_calls_use": "forwarded"
}
```

`llm_calls_use` ist `forwarded`, `static` oder `null` (kein Schlüssel
verfügbar).

`GET /debug/dependencies` ruft B-API, Textextraktion, Geocoder und Repository
je einmal auf, mit dem Schlüssel der Anfrage, und meldet je Dienst `url`,
`reachable`, `status`, `ms` und, wo ein Schlüssel im Spiel ist, `auth_source`
und `authorized`. Dazu kommen `version`, `llm_provider`, `b_api_auth_mode`, `config_warnings` und
`refused_ways`: die Wege zu Textextraktion und Geocoder, die die B-API zuletzt
abgelehnt hat (etwa `"geocoder:forwarded": 412`), mit den Sekunden, bis der
Agent sie wieder versucht. Der Aufruf verbraucht keine LLM-Tokens.

```bash
curl -s "$API/debug/auth" -H "Authorization: Bearer $B_API_KEY"
curl -s "$API/debug/dependencies" -H "Authorization: Bearer $B_API_KEY"
```

## Nutzungsbeispiele

### Von der Webseite zum geprüften Node

Der übliche Ablauf: erschließen, validieren, hochladen, prüfen.

```bash
API=http://localhost:8000

# 1. Aus einer Seite erschließen. Die Antwort ist der Datensatz.
curl -s -X POST "$API/generate" \
  -H "Content-Type: application/json" \
  -d '{"input_source": "url", "source_url": "https://example.com/workshop"}' \
  > record.json

# 2. Validieren: Der Datensatz geht unverändert hinein.
curl -s -X POST "$API/validate" \
  -H "Content-Type: application/json" -d @record.json

# 3. Hochladen: der Datensatz unverändert. Optionen stünden neben den Feldern.
curl -s -X POST "$API/upload" \
  -H "Content-Type: application/json" -d @record.json \
  > upload.json

# 4. Prüfen mit der nodeId aus upload.json.
curl -s -X POST "$API/upload/verify/<nodeId>" \
  -H "Content-Type: application/json" \
  -d "{\"expected_metadata\": $(cat record.json)}"
```

Vor Schritt 3 `processing.status` in `record.json` prüfen. Bei `partial` nennt
`processing.errors` die Felder, die fehlen.

### Mehrzeiliger Text ohne JSON

```bash
curl -s -X POST "$API/generate" \
  -H "Content-Type: text/plain" \
  --data-binary 'Workshop KI in der Bildung
Am 15. März 2026 in Berlin.
Zielgruppe: Lehrkräfte aller Schulformen.
Kosten: 49 Euro'
```

### Korrekturrunde

Die Felder des ersten Ergebnisses als `existing_metadata` zurücksenden und die
Anweisung in `text` schreiben. `_source_text` unter ihnen macht die Anfrage zur
Korrektur.

```bash
# Felder, _origins und _source_text des ersten Ergebnisses, ohne Kopf und processing
jq 'del(.contextName, .schemaVersion, .metadataset, .metadataset_uri,
        .language, .exportedAt, .processing, .preview_image_url)' \
  record.json > previous.json

curl -s -X POST "$API/generate" \
  -H "Content-Type: application/json" \
  -d "{
    \"text\": \"Titel kürzer, höchstens sechs Wörter\",
    \"schema_file\": \"event.json\",
    \"existing_metadata\": $(cat previous.json)
  }"
```

### Erst die Node-ID, dann die Metadaten

```bash
# 1. Den Node anlegen und seine ID merken.
curl -s -X POST "$API/node" \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"cclom:title": "Bruchrechnung Klasse 6"}}'

# 2. Innerhalb von 2 Stunden: den Datensatz in diesen Node schreiben.
curl -s -X POST "$API/upload" \
  -H "Content-Type: application/json" \
  -d '{
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    "ccm:wwwurl": "https://example.com/bruchrechnung",
    "node_id": "5443240c-43a2-4961-8324-0c43a22961dd"
  }'
```

### Python: Teilergebnisse und Fehler behandeln

```python
import httpx

API = "http://localhost:8000"
# Länger als METADATA_AGENT_GENERATE_TIMEOUT (300 s)
client = httpx.Client(base_url=API, timeout=320)

response = client.post("/generate", json={
    "text": "Fortbildung: Digitale Werkzeuge im Unterricht, am 20. April 2026 "
            "in der Stadthalle Hamburg, Dammtorwall 10, 20355 Hamburg.",
    "schema_file": "event.json",
})

if response.status_code == 503 and "Retry-After" in response.headers:
    raise SystemExit(f"Ausgelastet, erneut in {response.headers['Retry-After']} s")
if response.is_error:
    error = response.json()["error"]
    raise SystemExit(f"{error['code']} (Anfrage {error['request_id']})")

record = response.json()
processing = record["processing"]
if processing["status"] == "partial":
    print("Fehlende Felder:", processing["errors"])
for warning in processing["warnings"]:
    print("Hinweis:", warning)

validation = client.post("/validate", json=record).json()
if validation["valid"]:
    upload = client.post("/upload", json={**record, "check_duplicates": True}).json()
    if upload["success"]:
        print("Hochgeladen:", upload["node"]["repositoryUrl"])
    if upload.get("error"):
        print("Hinweis:", upload["error"])
```
