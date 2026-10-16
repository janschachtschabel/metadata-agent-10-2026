# Metadata Agent API — Deployment (Kubernetes / Docker)

> Für die Übergabe an den Betrieb (Ports, Header, Metriken, Skalierung,
> Beispiel-Values, Kosten) siehe [UEBERGABE.md](UEBERGABE.md).
> Bestehendes Deployment aktualisieren: [Update von 2.0.x auf 2.1.0](#update-von-20x-auf-210).

## Update von 2.0.x auf 2.1.0

**Kurz: Ein bestehendes Deployment muss nichts ändern.** Das Image auf 2.1.0
zu tauschen genügt. Alle neuen Einstellungen haben Defaults, die sich wie 2.0.x
verhalten, und das mitgelieferte Widget kennt die neuen Antworten bereits.

### Vor dem Update prüfen

| Punkt | Betrifft | Was tun |
|---|---|---|
| **Fehlschläge kommen als HTTP-Fehler** | Programme außer dem mitgelieferten Widget, die `/generate`, `/extract-field` oder `/detect-content-type` aufrufen | Siehe die Liste unter der Tabelle. Solche Programme müssen Antworten außer `200` auswerten und dürfen bei `partial` nicht von einem vollständigen Ergebnis ausgehen. |
| **`METADATA_AGENT_LLM_PROVIDER` wird geprüft** | Deployments mit einem Wert außer `b-api-openai`, `b-api-academiccloud`, `b-api-router` oder `openai` | Bisher landete jeder andere Wert (auch ein Tippfehler) stillschweigend bei OpenAI. Jetzt startet der Dienst nicht. Groß-/Kleinschreibung und Leerzeichen sind egal. |
| **`llm_provider` im Request wird geprüft** | Programme, die `llm_provider` mitschicken | Erlaubt sind die drei `b-api-*`-Provider; `openai` nur, wenn der Dienst selbst mit `openai` läuft. Alles andere: `422`. |
| **`collection_id` muss eine Node-UUID sein** | Programme, die `/upload` oder `/nodes` mit `collection_id` aufrufen | Eine UUID oder ein Sammlungs-Link, der eine enthält. Sonst `422`, statt die Eingabe in eine Repository-URL einzusetzen. |
| **Request-Bodies höchstens 20 MB** | Sehr große `/upload`-Anfragen | Darüber `413`. Einstellbar mit `METADATA_AGENT_MAX_REQUEST_BYTES`. Ein Ingress davor hat oft eine eigene, kleinere Grenze (ingress-nginx: 1 MB, `proxy-body-size`); passend setzen, sonst antwortet schon der Ingress mit `413`. |
| **Ein eingehender `Authorization`-Header wird weitergereicht** | Deployments, vor denen ein Proxy einen `Authorization: Bearer …` mitschickt, der **kein** B-API-Schlüssel ist (etwa ein eigenes Sitzungs-Token) | Im Default `auto` geht dieser Header an die B-API statt `B_API_KEY`, und die Erschließung scheitert mit `502 LLM_AUTH_REJECTED`. Abhilfe: `METADATA_AGENT_B_API_AUTH_MODE=static`; das ist genau das Verhalten von 2.0.x. Ohne einen solchen Header ändert sich nichts. |

Was sich bei Fehlschlägen ändert:

- **Totalausfall von `/generate`** (kein einziges Feld): jetzt `502`, bei
  Drosselung `503`, mit `detail` und `error.code`. 2.0.x antwortete mit `200`,
  `success: true` und leeren Feldern.
- **Fehler der Textextraktion:** jetzt `504`, `502` bzw. `422` mit
  `error.code`. Bisher lieferten `/detect-content-type` und `/extract-field`
  dafür `400`, `/generate` `502`.
- **KI-Fehler in `/extract-field`:** jetzt `502` (Drosselung `503`) mit
  `error.code`, bisher `500`.
- **Teilergebnisse:** weiter `200`, aber mit `processing.status: "partial"`.

### Ändert sich ohne Zutun

- **Inhaltsart:** Liefert die KI keine brauchbare Antwort, kommt nicht mehr
  stillschweigend `learning_material.json`. Die Schlüsselwort-Erkennung ist als
  solche gekennzeichnet (`method: "keywords"`, Warnung).
- **Timeout je KI-Aufruf 90 s** (`METADATA_AGENT_REQUEST_TIMEOUT`): vorher 60 s
  im Code und 300 s in `docker-compose.yml`/`.env.template`. 90 s lassen einen
  Puffer unter dem Ingress-Timeout von 120 s für ein ganzes `/generate`. Wer den
  Wert selbst gesetzt hat, behält ihn.
- **Günstigere Erschließung mit OpenAI-Providern:** Vor den Feld-Aufrufen läuft
  ein kurzer Vorlauf-Aufruf, der den Seitentext in den Prompt-Cache legt; die
  Feld-Aufrufe lesen ihn dann zu einem Zehntel des Preises. Eine Erschließung
  hat damit einen KI-Aufruf mehr und kostet gemessen 59–86 % weniger
  (UEBERGABE §7). Mit AcademicCloud gibt es keinen Vorlauf.
- **Weniger KI-Aufrufe bei Fehlern:** Bei abgelehntem Schlüssel oder
  erschöpftem Kontingent bricht die Erschließung sofort ab. Scheiternde Felder
  werden nicht mehr zusätzlich neu angefragt.
- **Logs:** gehen jetzt vollständig nach stdout und im Format `text` zeilengleich
  wie bisher. Neu sichtbar sind INFO-Meldungen einiger Module, die vorher
  verloren gingen. Warnungen, die vorher nach stderr gingen, stehen jetzt in
  stdout.
- **Header:**
  - Jede Antwort trägt `X-Request-ID`.
  - Aufrufe an B-API, Textextraktion und Repository tragen `x-request-id` und
    eingehende Tracing-Header.
- **OpenAPI-Spec:** `/health` steht nicht mehr darin, ist aber unverändert
  erreichbar. Der Docker-HEALTHCHECK und bestehende Probes funktionieren
  weiter.
- **Image:** Es kommt die Abhängigkeit `prometheus-client` hinzu; `openai`,
  `tenacity`, `aiofiles` und `python-multipart` fallen weg (nie genutzt). Das
  betrifft nur den Image-Bau in der CI. Wer mit pip installiert, nutzt die
  aktualisierte `requirements.txt`.
- **Schreiben ins Repository (`/upload`, `/nodes`):**
  - Lehnt das Repository alle Metadatenfelder ab, wird der neue Node verworfen;
    Sammlungen und Workflow laufen dann nicht mehr (`success: false`,
    `step: "setMetadata"`).
  - Ohne ladbares Schema wird gar nichts geschrieben (`step: "schema"`).
  - Der Workflow hält beim ersten fehlgeschlagenen Schritt an; die übrigen
    stehen mit `skipped: true` in der Antwort.
  - Teilfehler (Sammlung, Workflow-Schritt, erweiterte Daten, Aspekte,
    übersprungene Dublettenprüfung) nennt `error`, auch wenn `success: true`.
  - `/upload` mit `node_id` beachtet das Zwei-Stunden-Fenster wie `/nodes`
    (`403`/`404`).
- **API-Doku ohne Dritt-Server:** `/docs` lädt Swagger UI vom Agenten statt
  von `cdn.jsdelivr.net`, kein Validator-Aufruf an `validator.swagger.io`.
  `/redoc` entfällt (lud zusätzlich Google Fonts); die Spec unter
  `/openapi.json` ist unverändert.
- **Entfernte Einstellungen ohne Wirkung:** `METADATA_AGENT_DEBUG`,
  `…_DEFAULT_VERSION`, `…_NORMALIZATION_ENABLED`, `…_NORMALIZATION_TEMPERATURE`,
  `…_TEXT_EXTRACTION_DEFAULT_METHOD`. Kein Code hat sie gelesen; sind sie noch
  gesetzt, ignoriert der Dienst sie.
- **Widget:** Es lädt seine Texte als `/widget/i18n/<sprache>.json?v=2.1.0`.
  Nach dem Update gibt es daher keine Mischung aus neuem Code und alten,
  gecachten Texten.

### Empfohlen, aber optional

- **Readiness-Probe auf `/ready`:** liefert `503`, wenn die Konfiguration nicht
  arbeiten kann, statt den Pod Anfragen annehmen zu lassen.
- **`METADATA_AGENT_INTERNAL_PORT`** (z.B. `9000`): Probes und `/metrics` laufen
  dann über einen Port ohne Ingress, siehe [UEBERGABE.md](UEBERGABE.md). Nur
  mit einem Worker je Container.
- **`METADATA_AGENT_LOG_FORMAT=json`**, wenn ein Log-Sammler JSON erwartet.
- **Proxy- und Ingress-Timeout für `/generate`:** mindestens 120 s; das galt
  schon für 2.0.x.
- **B-API-Proxy mit `forwardApiKey`:** Wer darauf umstellt, sorgt dafür, dass
  der Dienst nur über die B-API erreichbar ist. Optional
  `METADATA_AGENT_B_API_AUTH_MODE=forward`, siehe
  [ENV-PARAMETER.md](ENV-PARAMETER.md#b-api-authentifizierung).
- **B-API-Routing** (`METADATA_AGENT_LLM_PROVIDER=b-api-router`): erst die
  Route anlegen, dann den Namen und `METADATA_AGENT_LLM_REQUEST_STYLE` setzen,
  siehe [ENV-PARAMETER.md](ENV-PARAMETER.md#b-api-routing).

### Zurück auf 2.0.x

Den Image-Tag zurücksetzen. Neu gesetzte Variablen stören 2.0.x nicht; es
ignoriert unbekannte Einstellungen, auch in einer `.env`-Datei.

## Container Image

Das Image wird automatisch bei jedem Push auf `main`/`develop` und bei neuen Tags gebaut und in die interne Registry gepusht:

```
$DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api
```

**Verfügbare Tags:**
- `main` — aktueller Stand von `main`
- `develop` — aktueller Stand von `develop`
- `v2.0.0` — spezifische Version (bei Git-Tag `v2.0.0`)

**Pull:**
```bash
docker pull $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:main
```

---

## Container starten

```bash
docker run -d \
  --name metadata-agent-api \
  -p 8000:8000 \
  -e B_API_KEY=<key> \
  -e WLO_GUEST_USERNAME=<user> \
  -e WLO_GUEST_PASSWORD=<pass> \
  $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:main
```

---

## Umgebungsvariablen

### Pflicht (Secrets)

| Variable | Beschreibung |
|---|---|
| `B_API_KEY` | API-Key für die B-API-LLM-Provider (OpenAI, AcademicCloud, Router). Pflicht im Modus `static`; im Default `auto` der Rückfall für Aufrufe ohne weitergereichten Schlüssel; im Modus `forward` nicht nötig (siehe `METADATA_AGENT_B_API_AUTH_MODE`) |

### Optional (Secrets)

| Variable | Beschreibung | Benötigt für |
|---|---|---|
| `OPENAI_API_KEY` | API-Key für nativen OpenAI-Zugang | Nur wenn `llm_provider=openai` |
| `WLO_GUEST_USERNAME` | edu-sharing Login für Upload | `/upload` Endpunkt |
| `WLO_GUEST_PASSWORD` | edu-sharing Passwort für Upload | `/upload` Endpunkt |

Das Ziel-Repository wird über `METADATA_AGENT_REPOSITORY_URL` gesetzt (siehe
nächste Tabelle) — es gibt keinen zweiten Schalter dafür.

### Konfiguration (mit Defaults)

> Die **vollständige** Liste aller Variablen steht in
> [ENV-PARAMETER.md](ENV-PARAMETER.md). Unten nur, was beim Deployment
> üblicherweise gesetzt wird.

Alle Konfigurationsvariablen haben das Prefix `METADATA_AGENT_`:

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | LLM-Provider: `b-api-openai`, `b-api-academiccloud`, `b-api-router` (Routen der B-API, siehe [ENV-PARAMETER.md](ENV-PARAMETER.md#b-api-routing)), `openai` (ohne B-API) |
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` | Schlüssel der KI-Aufrufe: `auto` (vom B-API-Proxy weitergereichter `Authorization: Bearer`, sonst `B_API_KEY`), `forward` (nur weitergereicht), `static` (nur `B_API_KEY`). Hinter dem Proxy (`forwardApiKey`) darf der Dienst nur über die B-API erreichbar sein — Details: [ENV-PARAMETER.md](ENV-PARAMETER.md#b-api-authentifizierung) |
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `20` | Parallele LLM-Worker (1–20), auch für `/generate` ohne `max_workers`; gedeckelt auf die Grenze des Providers |
| `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` | providerabhängig | Gleichzeitige LLM-Requests. Leer = Default des Providers (`b-api-openai`/`openai`: 20, `b-api-academiccloud`: 2) |
| `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` | providerabhängig | LLM-Requests pro Sekunde. Leer = Default des Providers (`b-api-academiccloud`: 2, sonst keine Grenze), `0` = aus |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `90` | Timeout je KI-Aufruf in Sekunden; Puffer unter dem Ingress-Timeout von 120 s |
| `METADATA_AGENT_DEFAULT_CONTEXT` | `default` | Standard Schema-Kontext |
| `METADATA_AGENT_SCREENSHOT_METHOD` | `playwright` | Immer lokal mit Chromium im Container; `pageshot` ist ein Legacy-Alias und nimmt ebenfalls lokal auf |
| `METADATA_AGENT_CORS_ORIGINS` | `*` | CORS Origins (komma-getrennt oder `*`) |
| `METADATA_AGENT_REPOSITORY_URL` | `https://repository.staging.openeduhub.net/edu-sharing/rest` | Repository-URL (Metadaten-Abruf & Upload) |

### LLM-Provider-spezifisch

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_B_API_OPENAI_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/openai` | B-API OpenAI Endpoint |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-6-luna` | B-API OpenAI Modell |
| `METADATA_AGENT_LLM_VERBOSITY` | `low` | Nur GPT-5/GPT-6 und o-Modelle. Leer = nicht senden |
| `METADATA_AGENT_LLM_REASONING_EFFORT` | `low` | Nur GPT-5/GPT-6 und o-Modelle. `none`/`low`/`medium`/`high` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/academiccloud` | B-API AcademicCloud Endpoint |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-v4-flash` | B-API AcademicCloud Modell |
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` | Beliebiger OpenAI-kompatibler Endpunkt |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` | Nativer OpenAI Modell |
| `METADATA_AGENT_OPENAI_TEMPERATURE` | folgt `LLM_TEMPERATURE` | Nur nötig, wenn nativ OpenAI abweichen soll |

**Durchsatzgrenzen** — jeder Provider hat ein eigenes Budget.
`b-api-academiccloud` (gemessen am 12.08.2026): exakt 2 gleichzeitige Requests,
~2 pro Sekunde. Ein dritter paralleler Request wird sofort mit `429` abgewiesen,
ohne `retry-after`. Der Default bildet das ab; wer ihn hochsetzt, erzeugt nur
Retries. `b-api-openai`: Default 20 gleichzeitig ohne Ratenbegrenzung — ein
echter `/generate`-Lauf am 06.10.2026 mit `gpt-6-luna` und 20 parallelen
Requests lief ohne `429`. Antwortet das Gateway doch mit `429`,
`METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` senken. Gesetzte Grenzen gelten für
jeden Provider. `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` ist **pro Sekunde**,
nicht pro Minute: `2` sind 120 Aufrufe/Minute.

Was das für die Laufzeit hieß — Erschließung mit 50 Feldern, gemessen am
12.08.2026:

| Provider / Modell | Dauer |
|---|---:|
| `b-api-openai` / `gpt-5.6-luna` | 25,2 s |
| `b-api-academiccloud` / `deepseek-v4-flash` | 32–90 s (Warteschlange am Gateway) |
| `b-api-openai` / `gpt-6-luna` (Standard, gemessen 16.10.2026) | 12–22 s |

Der Container selbst setzt kein Zeitlimit, Proxy und Ingress davor aber schon:
ingress-nginx bricht nach 60 s ab (`proxy-read-timeout`). Für `/generate` muss
dort mindestens **120 s** stehen (siehe oben und [UEBERGABE.md](UEBERGABE.md)),
sonst antwortet der Ingress mit `504`, während der Agent noch arbeitet. Bei
ingress-nginx außerdem `proxy-body-size` auf 20 MB setzen (Default 1 MB), sonst
scheitert `/upload` mit Vorschaubild schon am Ingress. Beispiel-Annotationen:
[UEBERGABE.md](UEBERGABE.md) §6.

**Auf AcademicCloud umstellen:**

```
METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL=deepseek-v4-flash
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=2
```

---

## Health Check

```
GET http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "version": "2.1.0",
  "llm_provider": "b-api-openai",
  "b_api_auth_mode": "auto"
}
```

Im Container ist ein HEALTHCHECK konfiguriert (Interval: 30s, Timeout: 10s, Retries: 3).

`GET /ready` antwortet `{"status": "ready"}`. Kann die Konfiguration nicht
arbeiten, antwortet es mit `503` und den Gründen, z.B. `B_API_KEY` fehlt im Modus
`static`. Beide Pfade stehen nicht in der OpenAPI-Spec. Mit
`METADATA_AGENT_INTERNAL_PORT` gibt es sie zusätzlich auf dem internen Port,
dort auch `/metrics`.

`/health` sagt nur, dass der Prozess läuft. Ob die KI-Aufrufe durchgehen, zeigt
erst der folgende Test.

---

## Nach dem Deployment prüfen (über den B-API-Proxy)

Die Befehle laufen in der Browserkonsole einer Redaktionsseite, auf der man
angemeldet ist. Sie nutzen die bestehende Anmeldung; einen API-Key braucht man
dafür nicht. Der Dienstname im Pfad (`metadata-agent-canvas`) ist der, unter dem
der Agent in der B-API eingetragen ist.

**1. Ein Feld, ein KI-Aufruf** – der kleinste echte Test:

```js
(async () => {
  const r = await fetch("/edu-sharing/rest/bapi/api/v1/proxy/metadata-agent-canvas/extract-field", {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: "Am 12. November findet in Weimar ein Konzert statt.",
                           schema_file: "core.json", field_id: "cclom:title", language: "de" })
  });
  const data = await r.json();
  console.log("HTTP", r.status, "| Anfrage-ID", data.error?.request_id || "–");
  console.log(r.ok ? `Titel: ${data.value}` : `${data.error?.code}: ${data.detail}`);
})();
```

**2. Vollständige Generierung:**

```js
(async () => {
  const r = await fetch("/edu-sharing/rest/bapi/api/v1/proxy/metadata-agent-canvas/generate", {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                           schema_file: "auto", language: "de", enable_geocoding: false })
  });
  const data = await r.json();
  const p = data.processing || {};
  console.table({
    http: r.status,
    anfrageId: data.error?.request_id || "–",
    status: p.status,
    felder: `${p.fields_extracted} von ${p.fields_total}, ${p.fields_failed} gescheitert`,
    inhaltsart: `${data.metadataset} (${p.content_type_detection?.method})`,
    schluessel: p.auth_source,
    fehler: data.error?.code || p.error?.code || "–",
  });
  console.log(data.detail || p.error?.message || data["cclom:title"]);
})();
```

| Ergebnis | Bedeutung |
|---|---|
| `200`, `success`, Inhaltsart `(llm)` | In Ordnung. `schluessel: forwarded` heißt, der Schlüssel kam über den Proxy. |
| `502`, `LLM_AUTH_FORBIDDEN` | Die B-API lehnt den Schlüssel ab. Rechte des Keys prüfen. |
| `502`, `LLM_AUTH_MISSING` | Modus `forward`, aber kein `Authorization` angekommen. `forwardApiKey` prüfen. |
| `503`, `LLM_RATE_LIMITED` | Kontingent oder Limit des Schlüssels erschöpft. |
| `partial` | Ein Teil der Felder ist gescheitert; `processing.errors` nennt die Ursachen. |
| Inhaltsart `(keywords)` | Die Inhaltsart ist nur über Schlüsselwörter geraten; die KI gab keine brauchbare Antwort. |
| `504`, `TEXT_EXTRACTION_CONNECT_TIMEOUT` | Nur bei URL-Eingabe: Der Textextraktionsdienst ist nicht erreichbar. |

**3. Wenn es scheitert:** `METADATA_AGENT_DEBUG_ENDPOINTS=true` setzen und
`/debug/auth` sowie `/debug/dependencies` über denselben Pfad aufrufen (siehe
[ENV-PARAMETER.md](ENV-PARAMETER.md#b-api-authentifizierung)). Danach wieder
ausschalten.

**Fehler melden:** Jede Antwort trägt eine Anfrage-ID (`X-Request-ID`).
Fehlerantworten mit Fehlercode (`error.code`, z.B. `LLM_*`, `TEXT_EXTRACTION_*`)
wiederholen sie als `error.request_id`. Im Log des Agenten steht dazu eine Zeile
`❌ [<Anfrage-ID>] <Status> <Code>: …`, wo vorhanden mit Upstream-Status und
Schlüsselquelle. Andere Fehler, etwa `400` bei fehlenden Angaben oder
Validierungsfehler (`422`), haben die ID nur im Header und keine eigene
Logzeile. Eine ID, die ein Proxy davor
schon vergeben hat, wird übernommen, sofern sie nur aus Buchstaben, Ziffern,
`.`, `_` und `-` besteht (höchstens 64 Zeichen).

---

## Ports

| Port | Protokoll | Beschreibung |
|---|---|---|
| `8000` | HTTP | FastAPI / Uvicorn: API, `/docs`, Widget; zusätzlich `/health` und `/ready` |
| `METADATA_AGENT_INTERNAL_PORT` (optional, z.B. `9000`) | HTTP | intern: `/health`, `/ready`, `/metrics` – nicht über den Ingress freigeben |

---

## Ressourcen-Empfehlung (Kubernetes)

| Ressource | Request | Limit |
|---|---|---|
| CPU | 500m | 2000m |
| Memory | 512Mi | 2Gi |

Das Image enthält Playwright + Chromium für Screenshots (~400 MB). Screenshots werden immer im Container aufgenommen — auch mit dem Legacy-Alias `pageshot`.

---

## Ausgehende URLs: nur öffentliche Ziele

Jede URL, die der Dienst selbst öffnet — `source_url` bei `input_source=url`,
Screenshot-Ziele (`/screenshot`, `/generate`, `/upload`) und Vorschaubild-URLs
bei `POST /nodes` —, muss ein öffentliches http(s)-Ziel sein. Abgelehnt werden
`localhost`, `*.local`, `*.internal`, `*.lan`, `*.home.arpa`, Loopback, private
Netze, Link-Local (inkl. `169.254.169.254`), Zugangsdaten in der URL und andere
Schemata. Bei Vorschaubild-URLs wird jede Weiterleitung einzeln geprüft
(höchstens 5, Download höchstens 10 MB). Im Screenshot-Browser werden direkte
Anfragen an interne Hosts blockiert; führt eine Weiterleitung oder Navigation der
Seite (auch in Frames oder für Bilder) zu einem internen Ziel, wird die Aufnahme
verworfen. Der Weiterleitungs-Request selbst ist dann allerdings schon gesendet —
wer auch das ausschließen muss, braucht Egress-Regeln für den Container.

Jeder Screenshot startet ein eigenes Chromium. Es laufen höchstens zwei
gleichzeitig, weitere warten; eine Aufnahme darf samt DNS-Prüfung und Wartezeit
höchstens 90 s dauern. Warum eine Aufnahme scheiterte (Netzfehler, Chromium
fehlt), steht im Log — die API-Antwort nennt nur einen neutralen Grund.

Die Prüfung löst DNS **im Container** auf: ein Hostname, der dort auf eine
private Adresse zeigt (z.B. Split-Horizon-DNS im Cluster), wird ebenfalls
abgelehnt — auch wenn er von außen öffentlich erreichbar ist.

---

## Kubernetes Deployment (Beispiel)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metadata-agent-api
  labels:
    app: metadata-agent-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: metadata-agent-api
  template:
    metadata:
      labels:
        app: metadata-agent-api
    spec:
      containers:
        - name: metadata-agent-api
          # Produktion: ein Versions-Tag (z.B. :v2.1.0), nie :main (siehe UEBERGABE.md)
          image: $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v2.1.0
          ports:
            - name: http
              containerPort: 8000
            - name: internal
              containerPort: 9000
          env:
            - name: B_API_KEY
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: b-api-key
            - name: WLO_GUEST_USERNAME
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: wlo-username
            - name: WLO_GUEST_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: wlo-password
            - name: METADATA_AGENT_LLM_PROVIDER
              value: "b-api-openai"
            - name: METADATA_AGENT_DEFAULT_MAX_WORKERS
              value: "20"
            - name: METADATA_AGENT_SCREENSHOT_METHOD
              value: "playwright"
            # /health, /ready, /metrics auf einem Port, den der Ingress nicht freigibt
            - name: METADATA_AGENT_INTERNAL_PORT
              value: "9000"
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: 2000m
              memory: 2Gi
          livenessProbe:
            httpGet:
              path: /health
              port: internal
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /ready
              port: internal
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: metadata-agent-api
spec:
  selector:
    app: metadata-agent-api
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
```

### Secrets anlegen:

```bash
kubectl create secret generic metadata-agent-secrets \
  --from-literal=b-api-key='<B_API_KEY>' \
  --from-literal=wlo-username='<WLO_GUEST_USERNAME>' \
  --from-literal=wlo-password='<WLO_GUEST_PASSWORD>'
```

---

## API-Dokumentation

Nach dem Start erreichbar unter:
- **Swagger UI:** `http://<host>:8000/docs` (lädt nichts von Dritt-Servern)
- **OpenAPI JSON:** `http://<host>:8000/openapi.json`

---

## Anforderungen an alternative Hosting-Runtimes

- Langlaufende Requests müssen mindestens die erwartete Gateway-Laufzeit erlauben.
- Screenshots brauchen Chromium in der Runtime; ohne Chromium gibt es keine Screenshots (`pageshot` nimmt ebenfalls lokal auf).
- `src/static/widget/dist/` muss zusammen mit der Anwendung ausgeliefert werden.
