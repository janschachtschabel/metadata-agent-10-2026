# Metadata Agent API — Deployment (Kubernetes / Docker)

> Für die Übergabe an den Betrieb (Ports, Header, Metriken, Skalierung,
> Alarmregeln, Kosten) siehe [UEBERGABE.md](UEBERGABE.md).
> Bestehendes Deployment aktualisieren: [Update von 2.1.x auf 3.0.0](#update-von-21x-auf-300),
> davor [Update von 2.0.x auf 2.1.0](#update-von-20x-auf-210).

## Update von 2.1.x auf 3.0.0

**Kurz:** Image tauschen genügt für Docker/Compose. Mit dem Helm-Chart (0.3.0)
vor der Installation die Namespaces der NetworkPolicy anpassen. Clients, die auf
Statuscodes verzweigen, prüfen die Tabelle „Breaking“ im CHANGELOG – die
mitgelieferte Webkomponente und das 2.1.1-Widget sind nicht betroffen (ein
langer neuer Text zusammen mit `_source_text` gilt wie in 2.1.1 als neue
Quelle, nicht als zu lange Korrekturanweisung). Die Webkomponente 3.x braucht
den Agent 3.x (WIDGET-REFERENZ.md).

- **Helm-Chart 0.3.0:**
  - **NetworkPolicy** (Standard an): Port 8000 nur aus `networkPolicy.ingressNamespaces`
    (Standard `b-api`), Port 9000 nur aus `prometheusNamespaces` (`monitoring`).
    Ausgehend: DNS nur zum Cluster-DNS (`networkPolicy.dns`, Standard
    `kube-system`/`k8s-app=kube-dns`), `egressNamespaces` nur auf `egressPorts`
    (Standard 80, 443, 8000, 8080 – die Ports der Ziel-Pods) und das Internet
    (IPv4) ohne private Netze. Namespaces und Ports vorher anpassen, sonst
    erreicht die B-API den Dienst nicht oder der Dienst die Textextraktion nicht
    (UEBERGABE §6, [Grenzen der NetworkPolicy](#grenzen-der-networkpolicy)).
  - **Schreibgeschütztes Root-Dateisystem** (`readOnlyRootFilesystem: true`);
    `/tmp` und `/home/appuser` sind `emptyDir` (Chromium). Kein
    Service-Account-Token im Pod.
  - **`METADATA_AGENT_INTERNAL_PORT`** kommt aus `service.internalPort` und steht
    nicht mehr unter `env` – einen eigenen Wert dort entfernen.
  - **`forwardedAllowIps`** (Standard `"*"`, siehe unten).
  - **`/ready` verlangt die Repository-Zugangsdaten:** Fehlt `WLO_GUEST_USERNAME`
    oder `WLO_GUEST_PASSWORD` – etwa durch einen Tippfehler in
    `secret.existingSecret` –, bleibt der Pod nicht bereit (`503`, Grund einmal
    im Log). Ohne sie antworten `/upload` und `/nodes` ohnehin mit `503`.
    Ein Deployment, das nur erschließt (mit 2.1.1 ohne diese Zugangsdaten
    bereit), setzt `METADATA_AGENT_REQUIRE_REPOSITORY=false` (Helm:
    `env.METADATA_AGENT_REQUIRE_REPOSITORY: "false"`): bereit ohne sie, mit
    Startwarnung.
  - **Secret-Änderungen starten die Pods nicht mehr neu.** Die Annotation
    `checksum/secret` ist entfallen: Sie zeigte einen ungesalzenen Hash der
    Secret-Werte jedem, der Deployments lesen darf. Nach einer Änderung:
    `kubectl rollout restart deployment/<name>` (`<name>` nennt die Ausgabe
    von `helm install`, beim Release `metadata-agent` ebenfalls `metadata-agent`).
  - **`emptyDir` mit `sizeLimit`** (`scratchVolumes`: `/tmp` 1Gi, Home 512Mi)
    und `topologySpreadConstraints` (weich, `ScheduleAnyway`; `topologySpread`).
  - **AcademicCloud:** `sharedLlmLimit.enabled=true` teilt das gemeinsame
    GWDG-Limit durch `replicaCount` (siehe
    [Durchsatzgrenzen](#llm-provider-spezifisch)).
- **Neue Einstellungen:** `METADATA_AGENT_GENERATE_TIMEOUT` (300 s) begrenzt ein
  ganzes `/generate` (auch `/detect-content-type`, `/extract-field`); danach
  `504 GENERATION_TIMEOUT`. `METADATA_AGENT_UPLOAD_TIMEOUT` (100 s) begrenzt
  `/upload` und `/nodes`; danach `504 UPLOAD_TIMEOUT`. Beide samt Aufräumen und
  preStop müssen unter den 320 s von Ingress und Grace-Period bleiben – sonst
  warnt der Agent beim Start. Die 300 s gelten auch ohne Ingress (Docker,
  Compose): Wer dort mit langsamen Modellen längere Läufe braucht (2.1.1 lief
  unbegrenzt), erhöht `METADATA_AGENT_GENERATE_TIMEOUT` und die Timeouts der
  Clients davor.
- **Lange Quellen (19.10.):** Der Quelltext wird auf 150.000 Zeichen gekürzt
  (vorher 500.000); `processing.warnings` von `/generate` nennt dann die
  Originallänge. Ein sehr langer Artikel (Goethe, 412.000 Zeichen) brauchte
  ungekürzt 56 KI-Aufrufe à Ø 34 s, zusammen 126 s – mehr als die früheren
  110 s; auf 150.000 Zeichen gekürzt 35–42 s mit denselben Feldern.
- **Timeouts davor mindestens ~310 s:** Der B-API-Proxy und edu-sharing vor dem
  Agenten haben eigene Timeouts, die der Chart nicht setzt. Sie müssen
  mindestens ~310 s erlauben (300 s Zeitlimit + Antwort), sonst brechen sie
  lange Erschließungen vorher ab und der Client sieht deren `504` statt des
  Ergebnisses.
- **Logger-Namen:** Fehlerzeilen kommen von `src.api.responses`, Endpunkte von
  `src.api.routers.*`, KI-Aufrufe von `src.services.llm_transport`. Wer im
  Logging-System auf `logger="src.main"` filtert, passt das an.
- **Image:** Basis-Images per Digest, `/app` gehört root (nur lesbar für den
  Dienst). Mit Docker selbst schreibgeschützt starten:
  `docker run --read-only --tmpfs /tmp --tmpfs /home/appuser …`.
- **pip-Installation:** `requirements.txt` ist der Export von `uv.lock` mit
  Hashes (`pip install --require-hashes -r requirements.txt`).
- **docker-compose.yml:** Port nur noch auf `127.0.0.1` (im Modus `auto`
  könnte sonst jeder im Netz Erschließungen auf `B_API_KEY` auslösen),
  schreibgeschützt mit tmpfs für `/tmp` und `/home/appuser` wie im Chart, und
  `stop_grace_period: 320s` – Dockers Default von 10 s brach ein laufendes
  `/generate` ab. Wer von anderen Rechnern zugreifen muss, setzt `"8000:8000"`,
  nur hinter einer Zugriffsbeschränkung. Eine vorhandene `.env` reicht der
  Container jetzt ganz durch (`env_file`, optional; braucht Docker Compose
  2.24+) – bisher kamen nur die in `environment` gelisteten Variablen an.

### Hinter TLS (Ingress, Proxy)

uvicorn übernimmt `X-Forwarded-Proto` und `X-Forwarded-For` nur von den
Adressen in `FORWARDED_ALLOW_IPS` (uvicorn-Variable, Standard `127.0.0.1`).
Ohne sie liefert `/widget/info` hinter einem TLS-Ingress `http://`-URLs, die
Browser auf einer `https`-Seite als Mixed Content blockieren. Der Helm-Chart
setzt sie aus `forwardedAllowIps` (Standard `"*"` – vertretbar, weil die
NetworkPolicy nur die B-API und Prometheus an den Pod lässt; ohne
NetworkPolicy die Adressen der Proxys eintragen). Mit Docker:
`-e FORWARDED_ALLOW_IPS=<Proxy-IP>`.

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
  Puffer unter dem Zeitlimit von 300 s für ein ganzes `/generate`. Wer den
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
- **Widget:** Es lädt seine Texte als `/widget/i18n/<sprache>.json?v=<Widget-Version>`.
  Nach dem Update gibt es daher keine Mischung aus neuem Code und alten,
  gecachten Texten.

### Empfohlen, aber optional

- **Readiness-Probe auf `/ready`:** liefert `503`, wenn die Konfiguration nicht
  arbeiten kann, statt den Pod Anfragen annehmen zu lassen.
- **`METADATA_AGENT_INTERNAL_PORT`** (z.B. `9000`): Probes und `/metrics` laufen
  dann über einen Port ohne Ingress, siehe [UEBERGABE.md](UEBERGABE.md). Nur
  mit einem Worker je Container.
- **`METADATA_AGENT_LOG_FORMAT=json`**, wenn ein Log-Sammler JSON erwartet.
- **Proxy- und Ingress-Timeout für `/generate`:** mindestens ~310 s – für den
  B-API-Proxy, edu-sharing und einen eigenen Ingress (der Chart setzt 320 s).
  2.0.x brauchte 120 s; seit 19.10. darf ein Lauf 300 s dauern.
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
- `v3.0.0` — spezifische Version (bei Git-Tag `v3.0.0`)

**Pull:**
```bash
docker pull $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v3.0.0
```

---

## Container starten

```bash
docker run -d \
  --name metadata-agent-api \
  -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp --tmpfs /home/appuser --stop-timeout 320 \
  -e B_API_KEY=<key> \
  -e WLO_GUEST_USERNAME=<user> \
  -e WLO_GUEST_PASSWORD=<pass> \
  $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v3.0.0
```

---

## Umgebungsvariablen

### Pflicht (Secrets)

| Variable | Beschreibung |
|---|---|
| `B_API_KEY` | API-Key für die B-API-LLM-Provider (OpenAI, AcademicCloud, Router). Pflicht im Modus `static`; im Default `auto` der Rückfall für Aufrufe ohne weitergereichten Schlüssel; im Modus `forward` nicht nötig (siehe `METADATA_AGENT_B_API_AUTH_MODE`) |
| `WLO_GUEST_USERNAME` | edu-sharing-Konto für `/upload`, `/nodes`, `/node`, `/workflow`, `/screenshot` mit `node_id` und Node-Quellen. Ohne es antwortet `/ready` mit `503` (außer mit `METADATA_AGENT_REQUIRE_REPOSITORY=false`) |
| `WLO_GUEST_PASSWORD` | Passwort dazu. Ohne es antwortet `/ready` mit `503` (außer mit `METADATA_AGENT_REQUIRE_REPOSITORY=false`) |

### Optional (Secrets)

| Variable | Beschreibung | Benötigt für |
|---|---|---|
| `OPENAI_API_KEY` | API-Key für nativen OpenAI-Zugang | Nur wenn `llm_provider=openai` |

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
| `METADATA_AGENT_REQUEST_TIMEOUT` | `90` | Timeout je KI-Aufruf in Sekunden; Puffer unter `METADATA_AGENT_GENERATE_TIMEOUT` (300 s) |
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
AcademicCloud (GWDG; `b-api-academiccloud` oder eine Router-Route aus
AcademicCloud-Modellen) hat zwei gemessene Grenzen, beide für den
B-API-Schlüssel, nicht je Pod:

- **höchstens 2 gleichzeitig** (12.08.2026, Staging, `deepseek-v4-flash`): Ein
  dritter paralleler Request wird sofort mit `429` abgewiesen, ohne
  `retry-after`. Damals gingen kurzzeitig ~2 Requests pro Sekunde durch.
- **~10 Aufrufe pro Minute, ~200 pro Stunde** (15.10.2026, Router mit
  Qwen-Modellen; der Agent wurde dabei auf 9 pro Minute gedrosselt).

Für eine ganze Erschließung (~50 Aufrufe) ist das Minutenlimit die engere
Grenze: 5–6 Minuten je Lauf, weit über `METADATA_AGENT_GENERATE_TIMEOUT`.
Der Default (2 gleichzeitig, 2 pro Sekunde je Prozess) bildet nur die erste
Grenze ab. Ob das Minutenlimit auch für `deepseek-v4-flash` über
`b-api-academiccloud` gilt, ist nicht nachgemessen; geplant wird mit ~10 pro
Minute. Die Drosselung des Agenten gilt je Prozess: Mit dem Helm-Chart teilt
`sharedLlmLimit.enabled=true` das Limit durch `replicaCount` (bei 2 Replicas je
Pod 1 gleichzeitig und 0,0833 Aufrufe/s); ohne Chart die beiden Werte unten von
Hand durch die Zahl der Instanzen teilen. `b-api-openai`: Default 20 gleichzeitig ohne Ratenbegrenzung — ein
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

Der Agent selbst begrenzt `/generate`, `/detect-content-type` und
`/extract-field` auf `METADATA_AGENT_GENERATE_TIMEOUT` (300 s) und `/upload`,
`/nodes` auf `METADATA_AGENT_UPLOAD_TIMEOUT` (100 s); Proxy und Ingress davor
haben eigene Grenzen: ingress-nginx bricht ohne Annotation nach 60 s ab
(`proxy-read-timeout`). Für `/generate` muss
dort mindestens **~310 s** stehen (der Chart setzt 320 s; siehe oben und
[UEBERGABE.md](UEBERGABE.md)), sonst antwortet der Ingress mit `504`, während
der Agent noch arbeitet. Das gilt ebenso für den B-API-Proxy und edu-sharing
vor dem Agenten, deren Timeouts der Chart nicht setzen kann. Bei
ingress-nginx außerdem `proxy-body-size` auf 20 MB setzen (Default 1 MB), sonst
scheitert `/upload` mit Vorschaubild schon am Ingress. Beispiel-Annotationen:
[UEBERGABE.md](UEBERGABE.md) §6.

**Auf AcademicCloud umstellen** (eine Instanz; bei mehreren die beiden
Grenzen durch ihre Zahl teilen, im Helm-Chart `sharedLlmLimit.enabled=true`
statt der beiden Variablen):

```
METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL=deepseek-v4-flash
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16   # ~10 pro Minute
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
  "version": "3.0.0"
}
```

Provider und Auth-Modus (`llm_provider`, `b_api_auth_mode`) stehen nur in
`/health` auf dem internen Port (`METADATA_AGENT_INTERNAL_PORT`).

Im Container ist ein HEALTHCHECK konfiguriert (Interval: 30s, Timeout: 10s, Retries: 3).

`GET /ready` antwortet `{"status": "ready"}`. Kann die Konfiguration nicht
arbeiten, antwortet es mit `503` (`status: "not_ready"`, dazu `detail` und
`error.code` `NOT_READY` wie jeder Fehler), z.B. wenn `B_API_KEY` im Modus
`static` oder `WLO_GUEST_USERNAME`/`WLO_GUEST_PASSWORD` fehlen (Letzteres nicht
mit `METADATA_AGENT_REQUIRE_REPOSITORY=false`). Die Gründe stehen im Log und in
`/ready` auf dem internen Port (`problems`), nicht auf dem öffentlichen. Abhängige
Dienste ruft `/ready` bewusst nicht auf: Ein Ausfall der B-API soll nicht alle
Pods auf einmal aus dem Service nehmen. Ohne `B_API_KEY` im Modus `auto` bleibt
der Pod bereit (hinter dem Proxy mit `forwardApiKey` ein gültiger Betrieb); das
meldet nur die Startwarnung. Beide Pfade stehen nicht in der OpenAPI-Spec. Mit
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

**Fehler melden:** Jede Antwort trägt eine Anfrage-ID (`X-Request-ID`). Jede
Fehlerantwort wiederholt sie als `error.request_id` (neben `error.code`, z.B.
`LLM_*`, `TEXT_EXTRACTION_*`, `BAD_REQUEST`, `VALIDATION_ERROR`), und zu jeder
steht eine Logzeile mit derselben ID im Log des Agenten: Fehler mit eigenem
Code als `❌ [<Anfrage-ID>] <Status> <Code>: …` (Level ERROR, wo vorhanden mit
Upstream-Status und Schlüsselquelle), übrige Client-Fehler wie `400` oder `422`
als `[<Anfrage-ID>] <Status> <Code>: …` auf Level INFO. Eine ID, die ein Proxy davor
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
verworfen. Der Weiterleitungs-Request selbst (GET) ist dann allerdings schon
gesendet — wer auch das ausschließen muss, braucht Egress-Regeln für den
Container. Anfragen mit Body (Formular-POST) prüft der Agent vor jedem
Weiterleitungs-Hop und leitet sie nie mit Body weiter; jede Anfrage des
Screenshot-Browsers an die API des Agenten selbst wird mit `403` abgelehnt.

Jeder Screenshot startet ein eigenes Chromium. Es laufen höchstens zwei
gleichzeitig, weitere warten; eine Aufnahme darf samt DNS-Prüfung und Wartezeit
höchstens 90 s dauern. Warum eine Aufnahme scheiterte (Netzfehler, Chromium
fehlt), steht im Log — die API-Antwort nennt nur einen neutralen Grund.

Die Prüfung löst DNS **im Container** auf: ein Hostname, der dort auf eine
private Adresse zeigt (z.B. Split-Horizon-DNS im Cluster), wird ebenfalls
abgelehnt — auch wenn er von außen öffentlich erreichbar ist.

---

## Kubernetes

Für Kubernetes gibt es einen Helm-Chart unter
[`helm/metadata-agent/`](helm/metadata-agent/README.md) (Deployment, Service,
NetworkPolicy, Secret, ServiceMonitor, Alarmregeln; kein Ingress hinter der
B-API; schreibgeschütztes Root-Dateisystem). Kurzform:

```bash
kubectl create secret generic metadata-agent \
  --from-literal=B_API_KEY='<B_API_KEY>' \
  --from-literal=WLO_GUEST_USERNAME='<WLO_GUEST_USERNAME>' \
  --from-literal=WLO_GUEST_PASSWORD='<WLO_GUEST_PASSWORD>'
helm install metadata-agent helm/metadata-agent \
  --set image.repository=$DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api \
  --set secret.existingSecret=metadata-agent
```

Das Secret liest der Pod nur beim Start. Nach einer Änderung:
`kubectl rollout restart deployment/metadata-agent`.

### Grenzen der NetworkPolicy

- **Ports nach der Adressumsetzung:** `networkPolicy.egressPorts` sind die
  Ports der Ziel-Pods (`targetPort`), nicht die des Service; die Policy greift
  nach der Umsetzung der Service-Adresse. Die richtigen Ports zeigt
  `kubectl -n b-api get endpoints -o wide`.
- **Öffentlicher Name, Ziel im selben Cluster (LoadBalancer/DNAT):** Läuft das
  Repository oder die B-API hinter dem Ingress bzw. einem LoadBalancer-Service
  desselben Clusters, löst ihr Name zwar auf eine öffentliche Adresse auf,
  kube-proxy setzt sie aber auf eine Pod-Adresse im privaten Netz um – und die
  sperrt `egressExceptCidrs`. Dann den Namespace des Ingress-Controllers (bzw.
  des Dienstes) in `egressNamespaces` aufnehmen. Ob das auf Staging zutrifft,
  klärt die Prüfliste unten.
- **IPv6:** Die Policy erlaubt nach außen nur IPv4 (`0.0.0.0/0` ohne private
  Netze). In einem Dual-Stack-Cluster scheitern Verbindungen über IPv6; Clients
  fallen auf IPv4 zurück, solange das Ziel eine IPv4-Adresse hat. Eine
  IPv6-Freigabe gehört bei Bedarf mit eigenen Ausnahmen (`fc00::/7`,
  `fe80::/10`, `::1/128`) in die Policy.
- **NodeLocal DNSCache:** Der DNS-Cache läuft auf einer Link-Local-Adresse des
  Knotens, nicht als Pod in `kube-system`. Dann `networkPolicy.dns.namespace: ""`
  setzen (Port 53 ist dann zu jeder Adresse offen).
- Die Policy wirkt nur mit einem CNI, das NetworkPolicies durchsetzt (Calico,
  Cilium …).

---

## Erste Installation auf Staging (Prüfliste vor der Übergabe)

Der Chart ist bisher nur statisch geprüft: `helm lint`, kubeconform und die
Tests. Diese Liste ist die erste Probe im Cluster. Die Ergebnisse gehören ins
Übergabeprotokoll. `<ns>` ist der Ziel-Namespace, `<release>` der Release-Name, `<name>` der Name
der Ressourcen: der Release-Name, wenn er „metadata-agent“ enthält, sonst
`<release>-metadata-agent` (die Ausgabe von `helm install` nennt ihn).

1. **Image `v3.0.0` bauen und pushen.** Git-Tag `v3.0.0` setzen; die
   GitLab-Pipeline baut, startet den Smoke-Test (schreibgeschützt, Chromium),
   scannt mit Trivy und pusht erst danach. Prüfen:
   `docker pull $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v3.0.0`.
   Ohne dieses Tag endet `helm install` mit den Defaults in `ImagePullBackOff`.
2. **Namespaces und Ports ermitteln:**
   - `kubectl get ns -L kubernetes.io/metadata.name` – Namespaces der B-API
     (Aufrufer und Ziel) und von Prometheus.
   - `kubectl -n <b-api-ns> get svc,endpoints -o wide` – Ports der Pods von
     B-API und Textextraktion für `networkPolicy.egressPorts`.
   - `kubectl -n kube-system get pods -l k8s-app=kube-dns` – findet das den
     Cluster-DNS nicht, Label bzw. Namespace in `networkPolicy.dns` anpassen;
     bei NodeLocal DNSCache `dns.namespace: ""`.
   - `kubectl get ingress -A | grep -e repository -e redaktion -e b-api` –
     liegen Repository oder B-API im selben Cluster, den Namespace des
     Ingress-Controllers in `egressNamespaces` aufnehmen (siehe oben).
3. **Secret anlegen** (`kubectl -n <ns> create secret generic …`, siehe
   [Kubernetes](#kubernetes)): `B_API_KEY` (nicht nötig im Modus `forward`),
   `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`.
4. **Values-Datei** für Staging, z.B. `staging-values.yaml`:
   `image.repository`, `networkPolicy.*` aus Schritt 2, unter `env` die
   Staging-Adressen (`METADATA_AGENT_B_API_BASE_URL`,
   `METADATA_AGENT_REPOSITORY_URL`, `METADATA_AGENT_TEXT_EXTRACTION_API_URL`)
   und `METADATA_AGENT_B_API_AUTH_MODE`. Aus einer 0.2.x-Datei
   `env.METADATA_AGENT_INTERNAL_PORT` entfernen.
5. **Prüfen und installieren:**
   ```bash
   helm lint helm/metadata-agent --strict -f staging-values.yaml
   helm upgrade --install <release> helm/metadata-agent -n <ns> \
     -f staging-values.yaml --set secret.existingSecret=metadata-agent \
     --wait --timeout 5m
   ```
6. **Pods und Probes:** `kubectl -n <ns> get pods -o wide` – alle Pods
   `Running` und bereit, möglichst auf verschiedenen Knoten.
   `kubectl -n <ns> describe pod -l app.kubernetes.io/name=metadata-agent` –
   keine fehlgeschlagenen Probes. `kubectl -n <ns> logs deploy/<name>`
   – kein `Nicht bereit:`, die Startwarnungen (`⚠️`) lesen.
   Interner Port:
   ```bash
   kubectl -n <ns> port-forward deploy/<name> 9000:9000
   curl -s localhost:9000/ready     # {"status":"ready"}
   curl -s localhost:9000/metrics | grep metadata_agent_llm_requests_total
   ```
   Die zweite Zeile zeigt die beim Start vorbelegten Serien mit `0`.
7. **NetworkPolicy:**
   - Aus dem B-API-Namespace muss der Dienst antworten, aus einem anderen nicht:
     ```bash
     kubectl -n <b-api-ns> run np-test --rm -it --restart=Never \
       --image=curlimages/curl -- curl -s -m 5 http://<name>.<ns>:8000/health
     kubectl -n default run np-test --rm -it --restart=Never \
       --image=curlimages/curl -- curl -s -m 5 http://<name>.<ns>:8000/health
     ```
   - Ausgehend aus dem Pod (DNS, Textextraktion, Repository):
     ```bash
     kubectl -n <ns> exec deploy/<name> -- python -c \
       "import urllib.request as u; print(u.urlopen('<REPOSITORY_URL>/_about', timeout=10).status)"
     ```
     dasselbe mit der Adresse der Textextraktion. Ein Timeout heißt: Namespace,
     Port oder DNAT-Fall aus Schritt 2 prüfen.
8. **Screenshot im schreibgeschützten Pod:**
   ```bash
   kubectl -n <ns> port-forward deploy/<name> 8000:8000
   curl -s -o /dev/null -w "%{http_code} %{content_type}\n" -X POST localhost:8000/screenshot \
     -H "Content-Type: application/json" -d '{"url": "https://example.org/"}'
   ```
   Erwartet: `200` mit einem Bild. Im Log kein `EPERM`, `EROFS` oder `ENOSPC`.
9. **`/widget/info` liefert `https://`:** über den B-API-Proxy (Browserkonsole
   einer Redaktionsseite) `…/proxy/metadata-agent-canvas/widget/info` aufrufen;
   alle URLs in der Antwort beginnen mit `https://` (Audit OPS-01,
   `forwardedAllowIps`).
10. **Ende-zu-Ende über den B-API-Proxy:** die Tests 1 und 2 aus
    [Nach dem Deployment prüfen](#nach-dem-deployment-prüfen-über-den-b-api-proxy).
    Mit `forwardApiKey` zeigt Test 2 `schluessel: forwarded`.
11. **Überwachung:** `kubectl -n <ns> get servicemonitor,prometheusrule`; in
    Prometheus ist das Target `up` und die Regeln `metadata-agent` sind geladen.
12. **Rollback einmal üben** (Abschnitt [Rollback](#rollback)), solange
    Staging noch niemand nutzt.

---

## Rollback

Der Dienst ist zustandslos: keine Datenbank, keine Migration. Ein Rollback
tauscht nur Image und Manifeste.

- **Helm:**
  ```bash
  helm history <release> -n <ns>
  helm rollback <release> <REVISION> -n <ns> --wait
  ```
  Helm stellt die Manifeste und Values der gewählten Revision wieder her. Hatte
  sie (Chart 0.2.x) keine NetworkPolicy, wird diese entfernt. Ein Secret aus
  `secret.existingSecret` bleibt unverändert.
- **Nur das Image zurück:** `helm upgrade <release> helm/metadata-agent -n <ns>
  --reuse-values --set image.tag=<voriges Tag> --wait`.
- **Docker/Compose:** den Image-Tag zurücksetzen und den Container neu starten.
- **Was bleibt:** Nodes, die `/upload` oder `/nodes` schon ins Repository
  geschrieben haben, bleiben dort. Die Webkomponente steckt im Image und geht
  mit zurück; Clients, die auf die Statuscodes von 3.0.0 umgestellt wurden,
  prüfen die Tabelle „Breaking“ im CHANGELOG.
- Zurück auf 2.0.x: siehe [Zurück auf 2.0.x](#zurück-auf-20x).

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
