# Metadata Agent API — Deployment

[English](DEPLOYMENT.md) · **Deutsch**

Wie die Metadata Agent API (Dienst-ID `metadata-agent`, Image
`metadata-agent-api`, Version 4.0.1) produktiv läuft: mit Docker oder auf
Kubernetes, hinter dem B-API-Proxy oder eigenständig.

Verwandte Dokumente:

| Dokument | Inhalt |
|---|---|
| [INSTALL.de.md](INSTALL.de.md) | Lokaler Start, Installation mit pip, erster Lauf |
| [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md) | Jede Umgebungsvariable mit ihrem Default |
| [docs/API.de.md](docs/API.de.md) | Endpunkte, Anfragen, Antworten, Fehlercodes |
| [README.de.md](README.de.md) | Überblick über den Dienst |
| [helm/metadata-agent/README.md](helm/metadata-agent/README.md) | Der Helm-Chart und wie er geprüft wird |
| [UEBERGABE.md](UEBERGABE.md) | Übergabe an den Betrieb: offene Entscheidungen, Messungen, Kosten |
| [docs/steckbrief.md](docs/steckbrief.md) | Steckbrief des Dienstes |
| [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md) | B-API-Routing und -Authentifizierung in Kürze |
| [docs/architektur/README.md](docs/architektur/README.md) | Grafiken zu Architektur und Ablauf für beide Betriebsarten |
| [CHANGELOG.md](CHANGELOG.md) | Alle Änderungen je Version |

## Inhalt

1. [Betriebsarten](#betriebsarten)
2. [Image](#image)
3. [Docker und Docker Compose](#docker-und-docker-compose)
4. [Kubernetes mit dem Helm-Chart](#kubernetes-mit-dem-helm-chart)
5. [Ports und Probes](#ports-und-probes)
6. [Timeouts und Größe der Anfragen](#timeouts-und-größe-der-anfragen)
7. [Skalierung, Durchsatz und Ressourcen](#skalierung-durchsatz-und-ressourcen)
8. [Logging und Tracing](#logging-und-tracing)
9. [Ausgehende Anfragen: nur öffentliche Ziele](#ausgehende-anfragen-nur-öffentliche-ziele)
10. [Nach dem Deployment prüfen](#nach-dem-deployment-prüfen)
11. [Erste Installation im Cluster: Prüfliste](#erste-installation-im-cluster-prüfliste)
12. [Updates](#updates)
13. [Rollback](#rollback)

---

## Betriebsarten

Der Agent prüft keine Zugangsdaten seiner Aufrufer. Den Zugang muss etwas davor
absichern: der B-API-Proxy oder ein eigener Proxy.

| | Hinter dem B-API-Proxy | Eigenständig mit B-API-Schlüssel | Eigenständig mit OpenAI |
|---|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` (Default), `b-api-academiccloud` oder `b-api-router` | wie links | `openai` |
| Schlüssel der LLM-Aufrufe | Der Schlüssel des Aufrufers, den der Proxy als `Authorization: Bearer <key>` weiterreicht. `B_API_KEY` ist nur der Rückfall (`auto`) oder nicht nötig (`forward`) | `B_API_KEY` | `OPENAI_API_KEY` |
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` (Default) oder `forward` | `auto` (Default) oder `static` | — |
| Textextraktion und Geocoder (Default `auto`) | Über die B-API, mit dem Schlüssel des Aufrufers | Über die B-API, mit `B_API_KEY` | Direkt: Die Textextraktion folgt der Repository-URL, der Geocoder ist Photon |
| Zu setzende Adressen | `METADATA_AGENT_B_API_BASE_URL`, `METADATA_AGENT_REPOSITORY_URL` | wie links | `METADATA_AGENT_REPOSITORY_URL`; `METADATA_AGENT_OPENAI_API_BASE` für einen anderen OpenAI-kompatiblen Endpunkt |
| Repository-Konto | `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` | wie links | wie links |
| Wer Port 8000 erreichen darf | Nur die B-API | Nur ein Proxy, der den Zugang beschränkt | Nur ein Proxy, der den Zugang beschränkt |

Die Defaults im Code für `METADATA_AGENT_B_API_BASE_URL` und
`METADATA_AGENT_REPOSITORY_URL` zeigen auf Staging. Der Helm-Chart setzt die
Adressen der Produktion. Mit Docker für die Produktion beide setzen:

| Umgebung | `METADATA_AGENT_B_API_BASE_URL` | `METADATA_AGENT_REPOSITORY_URL` |
|---|---|---|
| Staging (Default im Code) | `https://b-api.staging.openeduhub.net` | `https://repository.staging.openeduhub.net/edu-sharing/rest` |
| Produktion (Default im Chart) | `https://b-api.prod.openeduhub.net` | `https://redaktion.openeduhub.net/edu-sharing/rest` |

Das Repository-Konto wird für `/upload`, `/nodes`, `/workflow`, `/screenshot`
mit `node_id` und für Quellen gebraucht, die aus einem Node gelesen werden.
Ohne es antwortet `/ready` mit `503`. Ein Deployment, das nur Metadaten
erzeugt, setzt `METADATA_AGENT_REQUIRE_REPOSITORY=false`: Es ist ohne das Konto
bereit, und `/upload` und `/nodes` antworten mit `503`.

Beim Start schreibt der Agent die verwendeten Adressen ins Log und warnt
(`⚠️`) vor Einstellungen, die nicht zusammenpassen: kein Schlüssel für den
gewählten Modus, ein `B_API_KEY`, den der Modus `forward` nie verwendet, ein
fehlendes Repository-Konto, eingeschaltete Debug-Endpunkte, Zeitlimits, die
nicht in die Grace-Period passen, eine LLM-Adresse außerhalb von
`METADATA_AGENT_B_API_BASE_URL`, keine Textextraktion oder Adressen, die auf
verschiedene Umgebungen zeigen (Staging und Produktion gemischt). Benutzername
und Passwort in einer Adresse lässt der Agent in diesen Zeilen weg. Die Zeilen
nach jeder Änderung der Konfiguration lesen.

Eine Einstellung mit einem Wert, den sie nicht zulässt, verhindert dagegen den
Start: ein unbekannter Provider, Auth-Modus, Verbosity-Wert oder Reasoning
Effort, eine Temperatur außerhalb von 0–2, ein Timeout oder ein Limit unter
seinem Minimum, eine Screenshot-Größe außerhalb ihrer Grenzen. Der Fehler nennt
die Einstellung; die erlaubten Werte stehen in
[ENV-PARAMETER.de.md](ENV-PARAMETER.de.md).

### Hinter dem B-API-Proxy

Die B-API ruft den Agent über ihren Proxy-Pfad `/api/v1/proxy/<dienst>/…` auf.
Der Agent ist dort als **`metadata-agent-canvas`** eingetragen; edu-sharing ist
auf diesen Namen eingestellt. Aufrufer erreichen ihn unter:

```
<B-API>/api/v1/proxy/metadata-agent-canvas/<pfad>
/edu-sharing/rest/bapi/api/v1/proxy/metadata-agent-canvas/<pfad>   (im Browser, über edu-sharing)
```

Der Proxy-Eintrag gehört zur B-API und wird dort eingerichtet. Er braucht:

- die Adresse des Agents im Cluster, zum Beispiel
  `http://metadata-agent.<namespace>:8000`;
- die Option `forwardApiKey`: Der Proxy reicht dann den Schlüssel des Aufrufers
  als `Authorization: Bearer <key>` weiter. edu-sharing wählt den Schlüssel,
  einen für eingeloggte Nutzer und einen für Gäste, und die B-API bucht Nutzung
  und Limits auf ihn;
- ein Timeout von mindestens ~310 s (siehe
  [Timeouts und Größe der Anfragen](#timeouts-und-größe-der-anfragen)).

Der Agent gibt den weitergereichten Schlüssel unverändert weiter: an die
LLM-Endpunkte der B-API und an ihre Proxy-Dienste für Textextraktion und
Geocoder.

**Der Agent darf nur über die B-API erreichbar sein.** Er prüft den Header
nicht selbst. Erreicht jemand anderes Port 8000, laufen Anfragen ohne Header im
Modus `auto` auf `B_API_KEY`. Keinen Ingress davor setzen. Der Helm-Chart
erzwingt das mit einer NetworkPolicy.

### Eigenständig

Ohne den B-API-Proxy davor reicht niemand einen Schlüssel weiter:

- **Mit B-API-Schlüssel:** Die LLM-Aufrufe gehen mit `B_API_KEY` an die B-API
  (Modus `auto` ohne eingehenden Header, oder `static`). Textextraktion und
  Geocoder laufen mit diesem Schlüssel ebenfalls über die B-API, außer mit
  `METADATA_AGENT_B_API_SERVICES=direct`.
- **Mit OpenAI:** `METADATA_AGENT_LLM_PROVIDER=openai` und `OPENAI_API_KEY`.
  Textextraktion und Geocoder werden direkt aufgerufen, ohne Schlüssel. Die
  Textextraktion richtet sich nach der Repository-URL (siehe Tabelle unten).

Ein eigener Proxy muss den Zugang beschränken. Schickt dieser Proxy einen
`Authorization: Bearer`-Header mit, der kein B-API-Schlüssel ist (etwa ein
Sitzungs-Token), `METADATA_AGENT_B_API_AUTH_MODE=static` setzen. Im Modus
`auto` gäbe der Agent diesen Header an die B-API weiter, und jeder Aufruf
scheiterte mit `502` `LLM_AUTH_REJECTED`.

### Welchen Schlüssel der Agent nutzt

`METADATA_AGENT_B_API_AUTH_MODE` legt fest, welchen Schlüssel die Aufrufe an
die B-API tragen: LLM, Textextraktion und Geocoder. Das gilt für die drei
`b-api-*`-Provider. Es geht genau ein Header hinaus.

| Modus | Anfrage mit `Authorization: Bearer …` | Anfrage ohne den Header |
|---|---|---|
| `auto` (Default) | Der Header wird unverändert weitergereicht | `B_API_KEY`, gesendet als `X-API-KEY` |
| `forward` | Der Header wird unverändert weitergereicht | `502` `LLM_AUTH_MISSING`; aus einer URL: `502` `TEXT_EXTRACTION_AUTH_MISSING` |
| `static` | Der Header wird ignoriert | `B_API_KEY`, gesendet als `X-API-KEY` |

Es zählt nur das Schema `Bearer`. Der weitergereichte Schlüssel gilt für die
eine Anfrage, die ihn brachte: Er wird nicht gespeichert und nicht geloggt. Die
Antwort von `/generate` nennt die Quelle in `processing.auth_source`
(`forwarded`, `static` oder `openai`), nie den Schlüssel.

`/ready` meldet einen Modus, der nicht arbeiten kann: `static` ohne
`B_API_KEY` oder `openai` ohne `OPENAI_API_KEY`. Ein fehlender `B_API_KEY` im
Modus `auto` ist für `/ready` kein Fehler, weil hinter dem Proxy jeder Aufruf
einen Schlüssel mitbringt; der Agent warnt nur beim Start.

### Wie Textextraktion und Geocoder erreicht werden

`METADATA_AGENT_B_API_SERVICES` legt den Weg fest:

| Wert | Textextraktion (`/from-url`) | Geocoder |
|---|---|---|
| `auto` (Default) | Wie `proxy`, wenn der Provider ein `b-api-*`-Provider ist und seine LLM-Adresse unter `METADATA_AGENT_B_API_BASE_URL` liegt. Sonst wie `direct` | wie links |
| `proxy` | `<B-API>/api/v1/proxy/text-extraction`, mit dem Schlüssel der LLM-Aufrufe | `<B-API>/api/v1/proxy/photon_geocoding/api`, mit dem Schlüssel der LLM-Aufrufe |
| `direct` | Nach der Repository-URL, ohne Schlüssel (siehe unten) | `https://photon.komoot.io/api`, ohne Schlüssel |

Über die B-API gehören die Dienste immer zur selben Umgebung wie die B-API:
Staging zu Staging, Produktion zu Produktion.

Für `direct` leitet der Agent die Textextraktion aus
`METADATA_AGENT_REPOSITORY_URL` ab:

| Repository-Host | Textextraktion |
|---|---|
| `*.openeduhub.net` mit `staging` im Namen | `https://text-extraction.staging.openeduhub.net` |
| `redaktion.openeduhub.net` oder `*.prod.*` unter `openeduhub.net` | `https://text-extraction.prod.openeduhub.net` |
| Jeder andere Host | Keine. Die Erschließung aus einer URL antwortet mit `503` `TEXT_EXTRACTION_NOT_CONFIGURED`, bis `METADATA_AGENT_TEXT_EXTRACTION_API_URL` gesetzt ist |

`METADATA_AGENT_TEXT_EXTRACTION_API_URL` nennt eine eigene Textextraktion, zum
Beispiel den Dienst im Cluster. Sie überschreibt nur die Wahl für die
Textextraktion; der Geocoder folgt weiter `METADATA_AGENT_B_API_SERVICES`. Den
Schlüssel bekommt eine solche Textextraktion nur, wenn ihre Adresse unter
`METADATA_AGENT_B_API_BASE_URL` liegt.

### Rechte der Schlüssel in der B-API

Jeder Schlüssel, mit dem der Agent aufgerufen wird, braucht in der B-API:

| Recht | Nötig für | Ohne das Recht |
|---|---|---|
| Den LLM-Endpunkt des Providers (`openai`, `academiccloud` oder `router`) | Jede Erschließung | `502` `LLM_AUTH_FORBIDDEN` |
| Proxy-Dienst `text-extraction` | Erschließung aus einer URL | `502` `TEXT_EXTRACTION_AUTH_REFUSED` |
| Proxy-Dienst `photon_geocoding` | Koordinaten von Orten | Orte bleiben ohne Koordinaten; `processing.warnings` nennt den Grund |

Das gilt für den Nutzer- und den Gast-Schlüssel, die edu-sharing verwendet, und
für `B_API_KEY`. Die Rechte werden in der B-API vergeben, nicht im Agent. Wer
die beiden Proxy-Dienste nicht nutzen will, setzt
`METADATA_AGENT_B_API_SERVICES=direct`.

---

## Image

Die GitLab-Pipeline (`.gitlab-ci.yml`) baut das Image und pusht es in die
interne Registry:

```
$DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api
```

| Tag | Gebaut bei |
|---|---|
| `main` | Jedem Push auf `main` |
| `develop` | Jedem Push auf `develop` |
| `v4.0.1` | Dem Git-Tag `v4.0.1`. Ein Release-Tag muss die Form `vX.Y.Z` haben und zur Version in `pyproject.toml` passen; andere Tags veröffentlichen nichts |

Vor dem Push startet die Pipeline das Image schreibgeschützt und ohne
Schlüssel (`scripts/smoke-image.sh`): Sie wartet auf `/health`, prüft `/ready`,
den internen Port (`/ready`, `/metrics`) und dass `tini` der Init-Prozess ist,
und startet darin Chromium. Danach scannt sie das Image mit Trivy. Eine
kritische Lücke, für die es eine Korrektur gibt, verhindert die
Veröffentlichung. Für ein Release-Tag schreibt die Pipeline außerdem eine
Stückliste der enthaltenen Software (CycloneDX, Job-Artefakt `sbom.cdx.json`,
ein Jahr aufbewahrt). In der Produktion Versions-Tags verwenden; `main` und
`develop` wandern.

```bash
docker pull $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v4.0.1
```

Der GitHub-Spiegel (`.github/workflows/docker-publish.yml`) führt dieselben
Prüfungen aus und veröffentlicht nach Docker Hub, für `main` und Tags `v*`,
wenn das Secret `DOCKERHUB_PASSWORD` gesetzt ist.

Ein veröffentlichtes Image wird nach Zeitplan erneut geprüft, weil Lücken auch
nach einem Release bekannt werden:

| Wo | Läuft | Prüft |
|---|---|---|
| GitLab: ein Pipeline-Zeitplan auf `main` (einmal unter CI/CD > Schedules anzulegen) | Nur die Jobs `scheduled dependency checks` und `scheduled image scan` | `pip-audit` über `requirements.txt`; das Alter des mitgelieferten Chromium (`scripts/check_browser_age.py` mit `REQUIRE_BROWSER_AGE=1`); einen Trivy-Scan des neuesten Release-Images (oder des Tags in der Zeitplan-Variable `SCAN_TAG`), der bei einer Lücke der Stufe `HIGH` oder `CRITICAL` mit verfügbarer Korrektur fehlschlägt. Die Stückliste bleibt 30 Tage als Artefakt erhalten |
| GitHub: `.github/workflows/scheduled-checks.yml`, täglich um 04:23 UTC und bei manuellem Start | Die Jobs `dependencies` und `image` | Dieselben Prüfungen gegen das neueste Release-Image auf Docker Hub. Die Stückliste wird nicht aufbewahrt; die Job-Zusammenfassung nennt die Zahl ihrer Komponenten |

Ein fehlschlagender Image-Scan ist das Zeichen für ein neues Release.

Was das Image enthält:

| | |
|---|---|
| Basis | `python:3.13-slim`, per Digest festgelegt |
| Nutzer | `appuser` (UID 1000). Der Code unter `/app` gehört root und ist für den Prozess nur lesbar |
| Nötige beschreibbare Pfade | `/tmp` und `/home/appuser` (Profil und Caches von Chromium). Die Anwendung selbst schreibt nichts |
| Chromium | Für Screenshots enthalten: die Headless-Shell, die Playwright installiert (`playwright install --only-shell chromium`). Screenshots entstehen immer im Container |
| Prozess | `tini` als Init-Prozess (`ENTRYPOINT`), der `uvicorn src.main:app` auf Port 8000 startet, ein Prozess. uvicorn lauscht auf `UVICORN_HOST` (im Image `0.0.0.0`) und hält ungenutzte Verbindungen 75 s offen, länger als die 60 s, die ein Proxy sie üblicherweise hält |
| `HEALTHCHECK` | `/health` auf Port 8000 alle 30 s, Timeout 10 s, 3 Wiederholungen |
| Labels | `org.opencontainers.image.version` und `org.opencontainers.image.revision` (der Commit) |

Aus welchem Commit ein Image gebaut wurde:

```bash
docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' <image>
```

Eine Laufzeitumgebung ohne dieses Image muss dasselbe bieten: Chromium für
Playwright (ohne gibt es keine Screenshots), die gebaute Webkomponente unter
`src/static/widget/dist/` zusammen mit der Anwendung und Anfragen, die bis zu
300 s laufen dürfen.

---

## Docker und Docker Compose

### docker run

```bash
docker run -d \
  --name metadata-agent-api \
  -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp --tmpfs /home/appuser --stop-timeout 320 \
  -e B_API_KEY=<b-api-key> \
  -e WLO_GUEST_USERNAME=<user> \
  -e WLO_GUEST_PASSWORD=<password> \
  -e METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net \
  -e METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest \
  $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v4.0.1
```

| Option | Warum |
|---|---|
| `-p 127.0.0.1:8000:8000` | Nur dieser Rechner erreicht den Port. Im Modus `auto` löst jeder, der ihn erreicht, Erschließungen auf `B_API_KEY` aus. Für andere Rechner (`-p 8000:8000`) nur hinter einer Zugangsbeschränkung freigeben |
| `--read-only --tmpfs /tmp --tmpfs /home/appuser` | Schreibgeschütztes Root-Dateisystem wie im Helm-Chart, mit den zwei beschreibbaren Pfaden, die Chromium braucht |
| `--stop-timeout 320` | Ein laufendes `/generate` darf bis zu 300 s dauern. Dockers Default von 10 Sekunden bräche es bei jedem Neustart ab |

Weitere Einstellungen kommen als weitere `-e`-Optionen dazu, oder alle
zusammen in einer Datei mit `--env-file <datei>`. In der Produktion üblich:

| Einstellung | Wirkung |
|---|---|
| `METADATA_AGENT_LOG_FORMAT=json` | Ein JSON-Objekt je Logzeile, für einen Log-Sammler |
| `METADATA_AGENT_INTERNAL_PORT=9000` | Zweiter Port mit `/health`, `/ready` und `/metrics`. Nicht über den Rechner oder das Netz hinaus freigeben, das ihn abfragt |
| `FORWARDED_ALLOW_IPS=<Proxy-IP>` | Wessen `X-Forwarded-*`-Header uvicorn übernimmt, siehe [unten](#hinter-tls-ingress-proxy) |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS=<Sekunden>` | Nur mit einem anderen Stop-Timeout als 320 s: Die Startwarnungen vergleichen die Zeitlimits dann mit diesem Wert |

### Docker Compose

Ein Dienst für die Produktion mit dem Image aus der Registry:

```yaml
services:
  metadata-agent-api:
    image: ${DOCKER_REGISTRY}/projects/wlo/meta-services/metadata-agent-api:v4.0.1
    ports:
      - "127.0.0.1:8000:8000"   # nur dieser Rechner; andere nur hinter einer Zugangsbeschränkung
    read_only: true
    tmpfs:
      - /tmp
      - /home/appuser
    stop_grace_period: 320s      # ein laufendes /generate dauert bis zu 300 s
    restart: unless-stopped
    environment:
      - B_API_KEY=${B_API_KEY:-}
      - WLO_GUEST_USERNAME=${WLO_GUEST_USERNAME:-}
      - WLO_GUEST_PASSWORD=${WLO_GUEST_PASSWORD:-}
      - METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
      - METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
      - METADATA_AGENT_LOG_FORMAT=json
```

Compose füllt `${…}` aus der Shell oder aus einer `.env`-Datei neben der
Compose-Datei. Diese Datei nicht in die Versionsverwaltung aufnehmen.

Die `docker-compose.yml` im Repository folgt denselben Regeln, baut das Image
aber aus dem Quellbaum (`build: .`) und ist auf die Staging-Adressen
voreingestellt. Sie reicht außerdem eine ganze `.env`-Datei an den Container
durch (`env_file`, optional; diese Form braucht Docker Compose 2.24 oder
neuer). Ihr Healthcheck nutzt `/health`; ein Container ohne Repository-Konto
bleibt deshalb oben, obwohl `/ready` mit `503` antwortet. Drei Einstellungen
behalten einen leeren Wert aus der `.env`, statt auf ihren Default
zurückzufallen (`${VAR-default}` in der Datei):
`METADATA_AGENT_LLM_VERBOSITY`, `METADATA_AGENT_LLM_REASONING_EFFORT` und
`METADATA_AGENT_CORS_ORIGINS`.

### Hinter TLS (Ingress, Proxy)

uvicorn übernimmt `X-Forwarded-Proto` und `X-Forwarded-For` nur von den
Adressen in `FORWARDED_ALLOW_IPS` (uvicorn-Variable, Default `127.0.0.1`).
Keine Antwort des Agents hängt von diesen Headern ab: `/widget/info` liefert
relative Verweise statt absoluter URLs, und der Agent sendet keine Weiterleitung
(ein Pfad mit abschließendem Schrägstrich wie `/generate/` antwortet mit
`404`). Die Variable entscheidet nur, wessen Header uvicorn für Schema und
Client-Adresse einer Anfrage übernimmt.

- **Docker:** `-e FORWARDED_ALLOW_IPS=<Proxy-IP>` (mehrere mit Komma getrennt).
- **Helm:** Der Chart setzt sie aus `forwardedAllowIps`, Default `"*"`. Das ist
  nur vertretbar, weil die NetworkPolicy nichts außer der B-API und Prometheus
  an den Pod lässt. Mit `networkPolicy.enabled=false` die Adressen der Proxys
  eintragen.

---

## Kubernetes mit dem Helm-Chart

Der Chart liegt unter [`helm/metadata-agent/`](helm/metadata-agent/README.md).
Seine Version ist die Version des Dienstes: Chart 4.0.1 deployt das Image-Tag
`v4.0.1`. Der Chart ist bisher statisch geprüft (`helm lint`, kubeconform,
Tests), nicht in einem Cluster. Die erste Installation ist die
[Prüfliste unten](#erste-installation-im-cluster-prüfliste).

### Installieren

```bash
kubectl create secret generic metadata-agent \
  --from-literal=B_API_KEY='<B_API_KEY>' \
  --from-literal=WLO_GUEST_USERNAME='<WLO_GUEST_USERNAME>' \
  --from-literal=WLO_GUEST_PASSWORD='<WLO_GUEST_PASSWORD>'
helm install metadata-agent helm/metadata-agent \
  --set image.repository=$DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api \
  --set secret.existingSecret=metadata-agent
```

`helm install` nennt den Namen der Ressourcen und warnt zu den gewählten
Werten. Die Pods lesen das Secret nur beim Start, und ein geändertes Secret
startet sie nicht neu. Nach einer Änderung:

```bash
kubectl rollout restart deployment/metadata-agent
```

### Werte, die vor der Installation zu setzen sind

| Wert | Default | Was zu tun ist |
|---|---|---|
| `image.repository` | leer | **Pflicht.** Ohne ihn scheitert das Rendern |
| `image.tag` | `v4.0.1` | Das Image muss in der Registry liegen und aus demselben Stand gebaut sein wie der Chart (siehe [Updates](#updates)) |
| `image.digest` | leer | `sha256:` und 64 Hex-Ziffern legen das Image fest: Es wird als `<repository>@<digest>` geholt, das Tag wird nicht verwendet. Ein Tag lässt sich auf ein anderes Image umsetzen, ein Digest nicht |
| `secret.existingSecret` | leer | Name des Secrets mit `B_API_KEY`, `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`. Leer: Der Chart erwartet ein Secret, das wie seine Ressourcen heißt. `B_API_KEY` ist im Modus `forward` nicht nötig |
| `env.METADATA_AGENT_B_API_BASE_URL`, `env.METADATA_AGENT_REPOSITORY_URL` | Produktion | Für Staging beide setzen. Keine der beiden darf leer sein |
| `env.METADATA_AGENT_WLO_INBOX_ID` | `21144164-30c0-4c01-ae16-264452197063` (der Default des Codes) | Der Ordner, in dem neue Nodes angelegt werden. Prüfen, ob er im Ziel-Repository existiert und das Konto aus dem Secret dort schreiben darf; eine falsche ID zeigt sich erst an einem scheiternden `/upload` |
| `env.METADATA_AGENT_B_API_AUTH_MODE` | `auto` | `forward`, wenn nur weitergereichte Schlüssel genutzt werden dürfen |
| `env.METADATA_AGENT_REQUIRE_REPOSITORY` | `"true"` | `"false"` für ein Deployment, das nur erschließt und kein Repository-Konto hat |
| `networkPolicy.ingressNamespaces` | `[b-api]` | Namespace der B-API-Pods, die den Agent aufrufen |
| `networkPolicy.prometheusNamespaces` | `[monitoring]` | Namespace von Prometheus |
| `networkPolicy.egressNamespaces`, `networkPolicy.egressPorts` | `[b-api]`, `[80, 443, 8000, 8080]` | Namespaces, die der Agent im Cluster aufruft, und die Ports ihrer Pods |
| `networkPolicy.dns` | `kube-system`, `k8s-app: kube-dns` | Namespace und Pod-Label des Cluster-DNS |
| `serviceMonitor.labels`, `prometheusRule.labels` | `{}` | Labels, die die Selektoren des eigenen Prometheus erwarten |
| `sharedLlmLimit.enabled` | `false` | `true` mit AcademicCloud-Modellen (siehe [Skalierung](#academiccloud-ein-limit-für-alle-instanzen)) |
| `listenHost` | `"0.0.0.0"` | `"::"` in einem Cluster, dessen Pods IPv6-Adressen haben (nur IPv6, oder Dual-Stack mit IPv6 zuerst) |

Weitere Umgebungsvariablen stehen unter `env`. Fünf Variablen setzt der Chart
selbst und lehnt sie unter `env` ab:

| Variable | Gesetzt aus |
|---|---|
| `METADATA_AGENT_INTERNAL_PORT` | `service.internalPort` |
| `FORWARDED_ALLOW_IPS` | `forwardedAllowIps` |
| `UVICORN_HOST`, `METADATA_AGENT_INTERNAL_HOST` | `listenHost`: eine Adresse für den API-Port und den internen Port |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS` | `terminationGracePeriodSeconds` |

Mit `listenHost: "::"` erreichen Probes und Services den Pod über IPv6; der
API-Port lauscht dann nur auf IPv6.

Der Chart bricht das Rendern ab (`helm install`, `helm upgrade`,
`helm template`), statt etwas zu installieren, das im Cluster scheitert:

- `image.repository` ist leer, oder `image.digest` ist nicht `sha256:` und
  64 Hex-Ziffern;
- `service.internalPort` ist kein Port von 1 bis 65535;
- `env.METADATA_AGENT_B_API_BASE_URL` oder `env.METADATA_AGENT_REPOSITORY_URL`
  ist leer (der Agent fiele auf die Staging-Adresse seines Codes zurück);
- eine der fünf Variablen oben steht unter `env`, oder eines der beiden
  LLM-Limits, während `sharedLlmLimit.enabled` `true` ist;
- `sharedLlmLimit.requestsPerMinute` ist 0 oder kleiner, oder ergibt je Pod
  gerundet eine Rate von 0;
- mit `ingress.enabled`: Die Annotation `proxy-body-size` nennt nicht die Größe
  von `env.METADATA_AGENT_MAX_REQUEST_BYTES` (`20m` für 20971520, `40m` für
  41943040);
- mit `secret.create`: `secret.values` enthält einen Schlüssel, der nicht in
  `secret.keys` steht.

### Was der Chart setzt

| Ressource | Inhalt |
|---|---|
| Deployment | 2 Replicas, wo möglich über Knoten verteilt (`topologySpread`, `ScheduleAnyway`). Probes auf dem internen Port. `terminationGracePeriodSeconds: 320` und ein `preStop` von 5 s. Läuft als UID 1000 ohne Capabilities und ohne Service-Account-Token, Root-Dateisystem schreibgeschützt; `/tmp` (1Gi) und `/home/appuser` (512Mi) sind `emptyDir`-Volumes mit Größenlimit. Das Label `app.kubernetes.io/version` aller Ressourcen ist die Version, die der Chart ausrollt (`appVersion`), nicht `image.tag` |
| Umgebung | Die Werte unter `env`: Adressen der Produktion, der Inbox-Ordner, JSON-Logs, Dienstname `metadata-agent`, die Timeouts. Dazu die fünf Variablen, die der Chart selbst setzt (siehe oben). Jeder Secret-Schlüssel ist optional eingebunden; `/ready` meldet, was fehlt |
| Service | Zwei Services. `<name>`: Typ `service.type` (`ClusterIP`), nur Port 8000 (API). `<name>-internal`: immer `ClusterIP`, Port 9000 (`/health`, `/ready`, `/metrics`), nur für Prometheus; `service.type` gibt ihn nie nach außen frei |
| NetworkPolicy | Standardmäßig an, siehe unten |
| PodDisruptionBudget | Bei mehr als einer Replica: `minAvailable: 1`, damit ein Pod weiterläuft, während ein Knoten geleert wird |
| ServiceMonitor, PrometheusRule | Abruf von `/metrics` und die Alarmregeln, siehe unten |
| Secret | Nur mit `secret.create` und `--set secret.values.<KEY>=…`; sonst wird ein vorhandenes Secret genutzt |
| Ingress | **Aus.** Nicht für einen Agent hinter der B-API: Der Proxy ruft den Service im Cluster auf. `ingress.enabled` ist für den eigenständigen Betrieb hinter einer Zugangsbeschränkung gedacht; die Annotationen setzen 320 s Timeout und 20 MB Body für ingress-nginx, und die Body-Größe muss zu `env.METADATA_AGENT_MAX_REQUEST_BYTES` passen. Mit aktiver NetworkPolicy den Namespace des Ingress-Controllers in `networkPolicy.ingressNamespaces` aufnehmen |

### NetworkPolicy

| Richtung | Erlaubt |
|---|---|
| Eingehend, Port 8000 | Nur aus `networkPolicy.ingressNamespaces` (Default `b-api`) |
| Eingehend, Port 9000 | Nur aus `networkPolicy.prometheusNamespaces` (Default `monitoring`) |
| Ausgehend, DNS (Port 53) | Nur zum Cluster-DNS (`networkPolicy.dns`) |
| Ausgehend, im Cluster | Nur zu `networkPolicy.egressNamespaces`, auf `networkPolicy.egressPorts` |
| Ausgehend, Internet | IPv4, alle Ports, ohne die privaten Netze in `networkPolicy.egressExceptCidrs` |

Die Namespaces werden über das Label `kubernetes.io/metadata.name` gewählt, das
Kubernetes selbst setzt. Namespaces und Ports vor der Installation anpassen.
Sonst erreicht die B-API den Agent nicht oder der Agent seine Dienste nicht.

### Grenzen der NetworkPolicy

- **Ports nach der Adressumsetzung:** `networkPolicy.egressPorts` sind die
  Ports der Ziel-Pods (`targetPort`), nicht die des Service. Die Policy greift
  nach der Umsetzung der Service-Adresse. Die richtigen Ports zeigt
  `kubectl -n b-api get endpoints -o wide`.
- **Öffentlicher Name, Ziel im selben Cluster (LoadBalancer/DNAT):** Läuft das
  Repository oder die B-API hinter dem Ingress oder einem LoadBalancer-Service
  desselben Clusters, löst ihr Name auf eine öffentliche Adresse auf.
  kube-proxy setzt sie auf eine Pod-Adresse im privaten Netz um, und die sperrt
  `egressExceptCidrs`. Dann den Namespace des Ingress-Controllers (oder des
  Dienstes) in `egressNamespaces` aufnehmen. Mit den Defaults trifft dieser
  Fall LLM, Textextraktion und Geocoder zugleich, weil alle drei die
  öffentliche Adresse der B-API nutzen. Ob er auf den eigenen Cluster zutrifft,
  ist vor Ort zu prüfen (Prüfliste, Schritt 2).
- **IPv6:** Die Policy erlaubt nach außen nur IPv4 (`0.0.0.0/0` ohne private
  Netze). In einem Dual-Stack-Cluster scheitern Verbindungen über IPv6; Clients
  fallen auf IPv4 zurück, solange das Ziel eine IPv4-Adresse hat. Wer IPv6
  braucht, ergänzt die Policy um eine Regel mit eigenen Ausnahmen (`fc00::/7`,
  `fe80::/10`, `::1/128`).
- **NodeLocal DNSCache:** Der DNS-Cache läuft auf einer Link-Local-Adresse des
  Knotens, nicht als Pod in `kube-system`. `networkPolicy.dns.namespace: ""`
  setzen; Port 53 ist dann zu jeder Adresse offen.
- **CNI:** Die Policy wirkt nur mit einem CNI, das NetworkPolicies durchsetzt
  (Calico, Cilium und andere).

### Überwachung: ServiceMonitor und PrometheusRule

Beide brauchen die CRDs des Prometheus Operators (zum Beispiel
kube-prometheus-stack). Ohne sie bricht `helm install` mit
`no matches for kind "ServiceMonitor"` ab: zuerst die CRDs installieren, oder
beide mit `serviceMonitor.enabled=false` und `prometheusRule.enabled=false`
abschalten.

Der **ServiceMonitor** wählt den Service `<name>-internal` und ruft `/metrics`
auf dem internen Port alle 30 s ab (`serviceMonitor.interval`). Gezählt wird je
Prozess:

| Metrik | Labels |
|---|---|
| `metadata_agent_http_requests_total` | `method`, `route` (die Routen-Vorlage, nie eine ID), `status` |
| `metadata_agent_http_request_duration_seconds` | `method`, `route`. Buckets bis 300 s, ab 120 s in Schritten von 30 s |
| `metadata_agent_llm_requests_total` | `provider`, `result` (`ok`, `cancelled` oder der Fehlercode), `auth_source` (`forwarded`, `static`, `openai`, `none`), `config` (`default`, oder `override`, wenn die Anfrage Provider oder Modell gewählt hat) |
| `metadata_agent_llm_request_duration_seconds` | `provider`. Was der Aufrufer eines KI-Aufrufs wartet: auf einen freien Platz innerhalb der LLM-Grenzen des Prozesses (gleichzeitige Aufrufe, Aufrufe je Sekunde), auf die Anfrage, ihre Wiederholungen und die Pausen dazwischen |
| `metadata_agent_llm_last_failure_timestamp_seconds` | Die Labels von `metadata_agent_llm_requests_total`. Unix-Zeit des letzten KI-Aufrufs, der mit einem Fehler endete (nicht `ok`, nicht `cancelled`) |
| `metadata_agent_http_last_internal_error_timestamp_seconds` | `route`. Unix-Zeit der letzten Antwort `500` |
| `metadata_agent_llm_tokens_total` | `provider`, `kind`: `prompt` (alle Eingabe-Tokens), `cached` (der Teil, der aus dem Prompt-Cache gelesen wurde, ein Zehntel des Eingabepreises), `cache_write` (der Teil, der hineingeschrieben wurde, das 1,25-Fache des Eingabepreises), `completion` |
| `metadata_agent_generations_total` | `status`: wie ein `/generate`-Lauf endete (`success`, `partial`, `failed`), `cancelled`, wenn der Aufrufer die Verbindung geschlossen hat, `refused` für `503` `TOO_MANY_GENERATIONS` |
| `metadata_agent_upstream_requests_total` | `service` (`llm`, `text-extraction`, `repository`, `geocoder`, `other`) |
| `metadata_agent_upstream_responses_total` | `service`, `status` (der HTTP-Status; für `other` nur dessen Klasse, zum Beispiel `2xx` oder `4xx`) |
| `metadata_agent_upstream_response_duration_seconds` | `service` |

Dazu kommen die Prozess- und Python-Laufzeitmetriken von `prometheus_client`.
Gesendete Aufrufe minus Antworten sind die Aufrufe, die keine Antwort bekamen.

Die **PrometheusRule** installiert die 13 Regeln aus
[`helm/metadata-agent/files/alerts.yaml`](helm/metadata-agent/files/alerts.yaml),
ausgewertet je Namespace. Kritisch sind nur Fehler mit dem eigenen Schlüssel
und dem konfigurierten Modell. Fehler mit dem weitergereichten Schlüssel eines
Aufrufers oder einem per Anfrage gewählten Modell warnen nur.

| Regel | Schwere | Löst aus bei |
|---|---|---|
| `MetadataAgentLlmKeyRefused` | critical | Einem `LLM_AUTH_*` in 10 min mit eigenem oder fehlendem Schlüssel und dem konfigurierten Modell |
| `MetadataAgentLlmRouteMisconfigured` | critical | Einem `LLM_ROUTE_*` oder `LLM_MODEL_NOT_FOUND` in 10 min mit dem konfigurierten Modell |
| `MetadataAgentInternalError` | critical | Einer Antwort `500` (`INTERNAL_ERROR`) in 10 min |
| `MetadataAgentLlmCallerKeyRefused` | warning | Mehr als 5 `LLM_AUTH_*` in 15 min mit weitergereichtem Schlüssel |
| `MetadataAgentLlmRequestedModelRejected` | warning | Mehr als 5 Fehlern zu Route, Modell oder Berechtigung in 15 min mit einem per Anfrage gewählten Modell |
| `MetadataAgentLlmThrottled` | warning | Mehr als 5 `LLM_RATE_LIMITED` in 15 min |
| `MetadataAgentLlmUnreachable` | warning | Mehr als 3 `LLM_UNREACHABLE` in 10 min |
| `MetadataAgentGenerationsFailing` | warning | Mehr als 50 % der `/generate`-Läufe `failed` in 30 min, bei mindestens 3 Läufen. Es zählen nur beendete Läufe (`success`, `partial`, `failed`), nicht `cancelled` oder `refused` |
| `MetadataAgentUpstreamUnanswered` | warning | Mehr als 3 Aufrufen ohne Antwort in 10 min, 5 min lang (je Dienst, ohne `llm` und `other`) |
| `MetadataAgentUpstreamServerErrors` | warning | Mehr als 5 Antworten `5xx` in 10 min (je Dienst, ohne `other`) |
| `MetadataAgentUpstreamKeyRefused` | warning | Mehr als 3 Antworten `401` oder `403` in 10 min von Textextraktion oder Geocoder: Über die B-API fehlt dem Schlüssel das Recht für den Proxy-Dienst; direkt aufgerufen verlangt der Dienst selbst eine Anmeldung |
| `MetadataAgentGenerateSlow` | warning | Mehr als 5 % der `/generate`-Läufe dauern länger als 270 s, 15 min lang |
| `MetadataAgentServerErrors` | warning | Mehr als 5 eigenen Antworten `5xx` in 10 min (je Route, ohne `/health` und `/ready` und ohne die `503` `TOO_MANY_GENERATIONS` von `/generate`) |

Die drei kritischen Regeln lösen beim ersten Fehler aus, auch auf einem gerade
gestarteten Pod: Neben dem Zähler lesen sie die beiden Metriken
`…_timestamp_seconds`, die sagen, wann zuletzt ein Fehler auftrat, und lösen
aus, solange das weniger als 10 Minuten her ist. Eine Abruflücke, gleich wie
lang, lässt einen alten Fehler nicht noch einmal auslösen. Die Regeln
vergleichen die Zeit, die der Pod geschrieben hat, mit der Zeit von Prometheus:
Beide Uhren müssen auf deutlich weniger als diese 10 Minuten übereinstimmen.
Ein Pod eines älteren Images hat diese Metriken nicht; bei ihm entscheidet
allein der Zähler, und der sieht den ersten Fehler einer neuen Zeitreihe nicht.
`MetadataAgentGenerateSlow` liest den Anteil aus dem 270-s-Bucket und zählt nur
Pods, die ihn ausgeben; Pods eines älteren Images gehen nicht ein, solange
beide laufen.

Einen ausgefallenen oder neu startenden Pod melden die Regeln des Clusters
(`TargetDown`, `KubePodCrashLooping`).

---

## Ports und Probes

| Port | Inhalt | Erreichbar für |
|---|---|---|
| `8000` | API, `/docs` (Swagger UI, vom Agent selbst ausgeliefert), `/openapi.json`, die Webkomponente unter `/widget/*`. Außerdem `/health` und `/ready`, ohne Einzelheiten | Den B-API-Proxy oder den eigenen Proxy |
| `METADATA_AGENT_INTERNAL_PORT` (im Default nicht gesetzt; Chart: `9000`) | `/health`, `/ready` und `/metrics`, mit Einzelheiten | Nur Probes und Prometheus. Nie über einen Ingress führen. Im Chart hat er einen eigenen Service, `<name>-internal` |

- Ohne `METADATA_AGENT_INTERNAL_PORT` gibt es keinen zweiten Port und kein
  `/metrics`.
- Der interne Port braucht genau einen Worker je Container. Lässt er sich nicht
  binden (belegt, oder eine Adresse, die der Host nicht kennt), läuft die API
  weiter, im Log steht ein Fehler, und Probes auf dem internen Port scheitern.
- `METADATA_AGENT_INTERNAL_HOST` ist die Adresse, auf der der interne Port
  lauscht (Default `0.0.0.0`), `UVICORN_HOST` die Adresse des API-Ports
  (uvicorn-Variable; im Image `0.0.0.0`). In einem IPv6-Cluster sind beide
  `::`; der Chart setzt sie gemeinsam aus `listenHost`.
- Der Agent leitet keinen Pfad weiter: `/generate/` oder `/docs/` mit
  abschließendem Schrägstrich antworten mit `404`.
- `/health`, `/ready` und `/debug/*` stehen nicht in der OpenAPI-Spec.
  `/debug/auth` und `/debug/dependencies` antworten mit `404`, außer mit
  `METADATA_AGENT_DEBUG_ENDPOINTS=true`.

| Pfad | Antwort | Bedeutung |
|---|---|---|
| `/health` | `200` `{"status": "healthy", "version": "4.0.1"}`. Auf dem internen Port zusätzlich `llm_provider` und `b_api_auth_mode` | Der Prozess antwortet. Über die abhängigen Dienste sagt das nichts |
| `/ready` | `200` `{"status": "ready"}`, oder `503` mit `status: "not_ready"`, `detail` und `error.code` `NOT_READY`. Auf dem internen Port zusätzlich die Gründe (`problems`) | Die Konfiguration kann arbeiten |

`/ready` antwortet mit `503`, wenn:

- kein Schema-Kontext geladen werden konnte;
- `B_API_KEY` im Modus `static` fehlt (mit einem `b-api-*`-Provider);
- `OPENAI_API_KEY` mit dem Provider `openai` fehlt;
- `WLO_GUEST_USERNAME` oder `WLO_GUEST_PASSWORD` fehlt, außer mit
  `METADATA_AGENT_REQUIRE_REPOSITORY=false`.

Die Gründe stehen einmal im Log (`Nicht bereit: …`) und auf dem internen Port,
nicht auf dem öffentlichen. Die abhängigen Dienste ruft `/ready` bewusst nicht
auf: Ein Ausfall der B-API soll nicht alle Pods auf einmal aus dem Service
nehmen. Ob die LLM-Aufrufe durchgehen, zeigen erst die
[Prüfungen nach dem Deployment](#nach-dem-deployment-prüfen).

Probes des Helm-Charts, alle auf dem internen Port:

| Probe | Pfad | Takt |
|---|---|---|
| Startup | `/health` | Alle 2 s, bis zu 30 Fehlversuche |
| Liveness | `/health` | Alle 15 s, Timeout 5 s |
| Readiness | `/ready` | Alle 10 s, Timeout 5 s |

---

## Timeouts und Größe der Anfragen

| Was | Einstellung | Default | Bei Überschreitung |
|---|---|---|---|
| Ein LLM-Aufruf | `METADATA_AGENT_REQUEST_TIMEOUT` | 90 s | Der Aufruf wird abgebrochen. 90 s lassen einen Puffer unter dem Limit des ganzen Laufs |
| Ein ganzes `/generate`, `/detect-content-type` oder `/extract-field` | `METADATA_AGENT_GENERATE_TIMEOUT` | 300 s | `504` `GENERATION_TIMEOUT`. Feld-Aufrufe von `/generate`, die 5 s vor dem Limit noch laufen, werden beendet; was bis dahin fertig ist, kommt als `partial` zurück |
| `/upload`, `/nodes`, `/workflow`, `/screenshot` mit `node_id` | `METADATA_AGENT_UPLOAD_TIMEOUT` | 100 s | `504` `UPLOAD_TIMEOUT` |
| Ein Screenshot | fest | 90 s | Die Aufnahme scheitert |
| Textextraktion aus einer URL | fest | 10 s für den Verbindungsaufbau, 30 s für die Antwort | `504` `TEXT_EXTRACTION_CONNECT_TIMEOUT` oder `TEXT_EXTRACTION_TIMEOUT` |
| Body einer Anfrage | `METADATA_AGENT_MAX_REQUEST_BYTES` | 20 MB | `413` `REQUEST_TOO_LARGE` |
| Beenden: Grace-Period (Chart: `terminationGracePeriodSeconds`), `--stop-timeout`, `stop_grace_period` | `METADATA_AGENT_GRACE_PERIOD_SECONDS` nennt dem Agent den Wert | 320 s | Laufende Anfragen werden abgebrochen |
| Ingress des Charts (`proxy-read-timeout`, `proxy-send-timeout`) | `ingress.annotations` | 320 s | Der Ingress antwortet mit `504` |

Die Limits des Agents müssen samt Aufräumen und `preStop` unter den 320 s von
Ingress und Grace-Period bleiben. Der Agent warnt beim Start, wenn
`METADATA_AGENT_GENERATE_TIMEOUT` + 10 s oder `METADATA_AGENT_UPLOAD_TIMEOUT` +
15 s über 320 s liegt. Bei einer anderen Grace-Period diese in
`METADATA_AGENT_GRACE_PERIOD_SECONDS` nennen: Die Warnungen vergleichen dann
mit diesem Wert. Der Chart setzt die Variable aus
`terminationGracePeriodSeconds`. Sonst ändert sie nichts; den Prozess beendet
weiterhin die Plattform.

Was ein Proxy vor dem Agent erlauben muss:

- **Mindestens ~310 s für eine Antwort** (300 s Limit plus die Antwort). Das
  gilt für den B-API-Proxy, für edu-sharing und für einen eigenen Ingress; die
  ersten beiden kann der Chart nicht setzen. Ein kürzeres Timeout bricht lange
  Erschließungen ab, und der Client sieht das `504` des Proxys statt des
  Ergebnisses. ingress-nginx bricht ohne die Annotation `proxy-read-timeout`
  nach 60 s ab.
- **Bodys bis 20 MB.** ingress-nginx erlaubt im Default 1 MB
  (`proxy-body-size`); mit weniger als 20 MB scheitert ein `/upload` mit
  Vorschaubild schon am Ingress mit `413`.
- **Durchgereichte Header:** `Authorization` (hinter dem B-API-Proxy), die
  Tracing-Header (siehe [Logging und Tracing](#logging-und-tracing)) und
  `X-Forwarded-Proto` / `X-Forwarded-For`.

Schließt ein Aufrufer die Verbindung, etwa ein Proxy nach seinem eigenen
Timeout, beenden `/generate`, `/extract-field` und `/detect-content-type` ihre
LLM-Aufrufe. Im Log und in den Metriken steht dafür der Status `499`, und
`metadata_agent_generations_total` zählt einen solchen `/generate`-Lauf als
`cancelled`, nicht als `failed`.

Die 300 s gelten auch ohne Ingress (Docker, Compose). Für längere Läufe mit
langsamen Modellen `METADATA_AGENT_GENERATE_TIMEOUT` zusammen mit dem
Stop-Timeout und den Timeouts der Clients davor erhöhen.

---

## Skalierung, Durchsatz und Ressourcen

- **Zustandslos:** keine Datenbank, keine Sitzungen, keine Dateien zur
  Laufzeit. Jede Instanz kann jede Anfrage beantworten.
- **Ein Worker je Container.** Der interne Port und die Metriken setzen das
  voraus. Skaliert wird über Replicas (`replicaCount`, Default 2).
- **Grenzen je Prozess:** Sie addieren sich mit jeder Replica.

| Grenze | Default | Einstellung |
|---|---|---|
| Gleichzeitige LLM-Aufrufe | 20 (`b-api-openai`, `b-api-router`, `openai`), 2 (`b-api-academiccloud`) | `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` |
| LLM-Aufrufe pro Sekunde | Keine Grenze; 2 mit `b-api-academiccloud` | `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` (`0` = keine Grenze) |
| Gleichzeitige `/generate`-Läufe | Keine Grenze | `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS`; ein weiterer Lauf bekommt `503` `TOO_MANY_GENERATIONS` mit `Retry-After` |
| Gleichzeitige Screenshots | 2, weitere warten | fest |

Ein gesetzter Wert für die beiden LLM-Grenzen gilt für jeden Provider.
`METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` zählt pro Sekunde, nicht pro
Minute: `2` sind 120 Aufrufe pro Minute. Antwortet die B-API mit
`b-api-openai` mit `429`, `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` senken.

Dauer eines `/generate` mit etwa 50 Feldern, wie gemessen:

| Provider / Modell | Dauer | Gemessen |
|---|---:|---|
| `b-api-openai` / `gpt-6-luna` (Default), Text mit 4.400 Zeichen | Median 5,2 s; 7,8 s mit 10 Workern (9,3 s vor den Änderungen dieses Tages) | 15.11.2026 |
| `b-api-openai` / `gpt-6-luna`, Webseiten | 12–22 s | 16.10.2026 |
| `b-api-openai` / `gpt-5.6-luna` | 25,2 s | 12.08.2026 |
| `b-api-academiccloud` / `deepseek-v4-flash` | 32–90 s (Warteschlange am Gateway) | 12.08.2026 |

**Ressourcen** (Defaults des Charts): Requests 500m CPU und 512Mi Speicher,
Limits 2 CPU und 2Gi. Diese Werte sind geschätzt, nicht unter Last gemessen.
Speicherspitzen entstehen durch Chromium: Jeder Screenshot startet einen
eigenen Browser.

### AcademicCloud: ein Limit für alle Instanzen

AcademicCloud-Modelle (GWDG; Provider `b-api-academiccloud` oder eine
`b-api-router`-Route aus AcademicCloud-Modellen) haben zwei gemessene Grenzen.
Beide gelten für den B-API-Schlüssel, also für alle Instanzen zusammen:

- **Höchstens 2 gleichzeitige Aufrufe** (12.08.2026, Staging,
  `deepseek-v4-flash`). Ein dritter paralleler Aufruf wird sofort mit `429`
  abgewiesen.
- **Etwa 10 Aufrufe pro Minute und etwa 200 pro Stunde** (15.10.2026, Router
  mit Qwen-Modellen). Ob diese Grenze auch für `deepseek-v4-flash` über
  `b-api-academiccloud` gilt, ist nicht nachgemessen; geplant wird mit etwa 10
  pro Minute.

Eine ganze Erschließung braucht etwa 50 Aufrufe. Mit 10 Aufrufen pro Minute
dauert sie 5 bis 6 Minuten und endet an `METADATA_AGENT_GENERATE_TIMEOUT` mit
Teilergebnissen. Für die interaktive Nutzung bleibt `b-api-openai` der
Standard.

Der Agent drosselt je Prozess, und seine Defaults (2 gleichzeitig, 2 pro
Sekunde) bilden nur die erste Grenze ab. Das gemeinsame Limit durch die Zahl
der Instanzen teilen:

- **Helm:** `sharedLlmLimit.enabled=true` teilt
  `sharedLlmLimit.concurrentRequests` (2) und
  `sharedLlmLimit.requestsPerMinute` (10) durch `replicaCount` und setzt beide
  Variablen je Pod. Mit 2 Replicas bekommt jeder Pod 1 gleichzeitigen Aufruf
  und 0,0833 Aufrufe pro Sekunde. Skalieren von Hand (`kubectl scale`, ein HPA)
  umgeht die Aufteilung. Mit mehr Replicas als gleichzeitigen Aufrufen
  überschreiten die Pods zusammen das Limit, weil jeder Pod mindestens 1
  bekommt. `helm install` warnt davor, und ebenso, wenn AcademicCloud oder der
  Router mit mehreren Replicas ohne die Aufteilung läuft.
- **Ohne den Chart:** beide Variablen von Hand setzen. Für eine Instanz:

```
METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL=deepseek-v4-flash
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16   # etwa 10 pro Minute
```

Für `b-api-openai` und `openai` bleibt `sharedLlmLimit` aus.

---

## Logging und Tracing

Die Logs gehen nach stdout.

| Einstellung | Default | Wirkung |
|---|---|---|
| `METADATA_AGENT_LOG_FORMAT` | `text` (Chart: `json`) | `text`: nur die Meldung, je eine Zeile. `json`: ein Objekt je Zeile |
| `METADATA_AGENT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` oder `ERROR` |
| `METADATA_AGENT_SERVICE_NAME` | `metadata-agent` | Das Feld `service` jeder JSON-Zeile |

Felder einer JSON-Zeile:

| Feld | Inhalt |
|---|---|
| `timestamp` | UTC mit Millisekunden, zum Beispiel `2026-11-13T09:15:02.417Z` |
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `service` | Die Dienst-ID, `metadata-agent` |
| `logger` | Das Modul, zum Beispiel `src.api.responses` (Fehlerzeilen), `src.api.routers.*` (Endpunkte), `src.services.llm_transport` (LLM-Aufrufe), `metadata_agent.request` (Anfragezeile) |
| `message` | Der Text |
| `trace_id`, `span_id` | Die B3-IDs der Anfrage; `null` außerhalb einer Anfrage |
| `request_id` | Die Anfrage-ID, wie im Antwort-Header `X-Request-ID`; `null` außerhalb einer Anfrage |
| `client_trace_id` | Nur, wenn der Aufrufer `x-client-trace-id` geschickt hat |
| `method`, `path`, `status`, `duration_ms` | Nur in der Anfragezeile |
| `exception` | Der Traceback, nur in Zeilen, die eine Ausnahme loggen |

**Anfragezeile.** Eine Zeile je Anfrage ersetzt das Access-Log von uvicorn, in
beiden Formaten: `<Methode> <Pfad> <Status> <Dauer> ms`, zum Beispiel
`POST /generate 200 14230 ms`. Der Pfad steht ohne Query-String und ohne Header
da. `/health` und `/ready` werden nur auf `DEBUG` geloggt. Anfragen auf dem
internen Port werden nicht geloggt.

**Fehlerzeilen.** Jede Fehlerantwort trägt die Anfrage-ID als
`error.request_id`, und zu ihr steht eine Logzeile mit derselben ID:

| Zeile | Level | Für |
|---|---|---|
| `❌ [<Anfrage-ID>] <Status> <Code>: …` | `ERROR` | Fehler des Agents oder eines Dienstes, von dem er abhängt (Status 5xx), mit eigenem Code (`LLM_*`, `TEXT_EXTRACTION_*`, `GENERATION_TIMEOUT` …), wo vorhanden mit Upstream-Status und Schlüsselquelle |
| `[<Anfrage-ID>] <Status> <Code>: …` | `INFO`; `WARNING` für `503` `TOO_MANY_GENERATIONS`; `ERROR` für übrige Fehler ab Status 500 | Fehler des Aufrufers (4xx) wie `400` `BAD_REQUEST` oder `422` `VALIDATION_ERROR`, auch solche mit eigenem Code |

Wer einen Fehler meldet, nennt die Anfrage-ID. Feldwerte, Orte und Adressen aus
Anfragen stehen nur auf `DEBUG` im Log.

**Header.**

| Header | Richtung | Wozu |
|---|---|---|
| `Authorization: Bearer <key>` | ein | Der Schlüssel des Aufrufers vom B-API-Proxy (`forwardApiKey`). Er geht unverändert an die B-API weiter |
| `x-client-trace-id`, `x-request-id`, `x-b3-traceid`, `x-b3-spanid`, `x-b3-parentspanid`, `x-b3-sampled`, `x-b3-flags`, `b3` | ein, weitergereicht | Tracing (B3). Gehen unverändert an die B-API, die Textextraktion und das Repository weiter. Nie an Dritte wie ein direkt aufgerufenes Photon oder Seiten, von denen ein Screenshot entsteht |
| `X-Request-ID` | aus | Die Anfrage-ID, in jeder Antwort |
| `X-Forwarded-Proto`, `X-Forwarded-For` | ein | Nur von `FORWARDED_ALLOW_IPS` übernommen (siehe [Hinter TLS](#hinter-tls-ingress-proxy)) |

- Die Anfrage-ID ist die eingehende `x-client-trace-id`, sonst die eingehende
  `x-request-id`, sonst die B3-Trace-ID. Ein eingehender Wert wird nur
  übernommen, wenn er aus Buchstaben, Ziffern, `.`, `_` und `-` besteht
  (höchstens 64 Zeichen). Eine `x-request-id`, die nicht passt, wird auch
  nicht weitergegeben; an ihre Stelle tritt die Anfrage-ID des Agents.
- Ohne eingehende B3-IDs beginnt der Agent eine eigene Trace und gibt sie
  weiter, damit die Aufrufe einer Anfrage trotzdem zusammengehören. Eine
  `x-b3-traceid`, die keine gültige ist (16 oder 32 Hex-Zeichen), beginnt
  ebenfalls eine neue Trace. Eigene Spans meldet der Agent nicht.

---

## Ausgehende Anfragen: nur öffentliche Ziele

Jede URL, die der Agent selbst öffnet, muss ein öffentliches http(s)-Ziel sein:
`source_url` bei `input_source=url`, die Seiten der Screenshots (`/screenshot`,
`/generate`, `/upload`) und Vorschaubild-URLs bei `POST /nodes`.

| Regel | Einzelheit |
|---|---|
| Abgelehnt | `localhost`, `*.localhost`, `*.local`, `*.internal`, `*.lan`, `*.home.arpa`, Loopback, private Netze, Link-Local (auch `169.254.169.254`), Zugangsdaten in der URL, andere Schemata |
| Vorschaubilder | Jede Weiterleitung wird geprüft; höchstens 5 Weiterleitungen, höchstens 10 MB |
| Screenshot-Browser | Direkte Anfragen an interne Hosts werden blockiert. Führt eine Weiterleitung oder Navigation der Seite (auch in Frames oder für Bilder) zu einem internen Ziel, wird die Aufnahme verworfen |
| Die eigene API des Agents | Jede Anfrage des Screenshot-Browsers an den Agent selbst wird mit `403` abgelehnt |
| Screenshots | Ein Chromium je Aufnahme, höchstens zwei gleichzeitig, je höchstens 90 s samt DNS-Prüfung und Wartezeit |

Zwei Grenzen bleiben:

- Führt eine Weiterleitung zu einem internen Ziel, ist die
  Weiterleitungs-Anfrage selbst (ein GET) schon gesendet. Wer auch das
  ausschließen muss, braucht Egress-Regeln für den Container. Die NetworkPolicy
  des Helm-Charts sperrt die privaten Netze.
- Die Prüfung löst DNS **im Container** auf. Ein Hostname, der dort auf eine
  private Adresse zeigt, etwa bei Split-Horizon-DNS im Cluster, wird ebenfalls
  abgelehnt, auch wenn er von außen öffentlich ist.

Warum eine Aufnahme scheiterte (Netzfehler, Chromium fehlt), steht im Log. Die
API-Antwort nennt nur einen neutralen Grund.

---

## Nach dem Deployment prüfen

- [ ] `/health` antwortet mit `200` und der erwarteten Version.
- [ ] `/ready` antwortet mit `{"status": "ready"}`.
- [ ] Das Startlog zeigt die beabsichtigten Adressen (`LLM API Base`,
      `Text Extraction URL`, `Geocoder URL`, `Repository URL`), kein
      `Nicht bereit:` und keine unerwartete Warnung (`⚠️`).
- [ ] Mit internem Port: `/metrics` antwortet und enthält
      `metadata_agent_llm_requests_total`.
- [ ] Die Tests 1 bis 4 unten gelingen, die Tests 3 und 4 je einmal eingeloggt
      und als Gast.
- [ ] Hinter einem Proxy: `/widget/info` antwortet darüber, und das Skript, das
      es nennt (`dist/main.js`, relativ zur Adresse von `/widget/info`), lässt
      sich über denselben Pfad laden: `…/widget/dist/main.js`.

`/health` sagt nur, dass der Prozess läuft. Ob die LLM-Aufrufe durchgehen,
zeigen erst die Tests.

### Tests über den B-API-Proxy

Die Befehle laufen in der Browserkonsole einer edu-sharing-Seite, auf der man
angemeldet ist. Sie nutzen die bestehende Sitzung; einen API-Key braucht man
dafür nicht. Der Dienstname im Pfad ist der Proxy-Eintrag
`metadata-agent-canvas`. Die Beispieltexte sind deutsch, wie die Inhalte, die
der Agent üblicherweise verarbeitet.

**Test 1: ein Feld, ein LLM-Aufruf.** Der kleinste echte Test:

```js
(async () => {
  const r = await fetch("/edu-sharing/rest/bapi/api/v1/proxy/metadata-agent-canvas/extract-field", {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: "Am 12. November findet in Weimar ein Konzert statt.",
                           schema_file: "core.json", field_id: "cclom:title", language: "de" })
  });
  const data = await r.json();
  console.log("HTTP", r.status, "| request id", data.error?.request_id || "–");
  console.log(r.ok ? `title: ${data.value}` : `${data.error?.code}: ${data.detail}`);
})();
```

**Test 2: eine ganze Erschließung.**

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
    requestId: data.error?.request_id || "–",
    status: p.status,
    fields: `${p.fields_extracted} of ${p.fields_total}, ${p.fields_failed} failed`,
    contentType: `${data.metadataset} (${p.content_type_detection?.method})`,
    keySource: p.auth_source,
    error: data.error?.code || p.error?.code || "–",
  });
  console.log(data.detail || p.error?.message || data["cclom:title"]);
})();
```

| Ergebnis | Bedeutung |
|---|---|
| `200`, `success`, Inhaltstyp `(llm)` | In Ordnung. `keySource: forwarded` heißt, der Schlüssel kam über den Proxy |
| `502`, `LLM_AUTH_FORBIDDEN` | Die B-API lehnt den Schlüssel ab (`403`). Rechte des Schlüssels prüfen |
| `502`, `LLM_AUTH_REJECTED` | Die B-API nimmt den Schlüssel nicht an (`401`) |
| `502`, `LLM_AUTH_MISSING` | Modus `forward`, aber kein `Authorization`-Header angekommen. `forwardApiKey` prüfen |
| `503`, `LLM_RATE_LIMITED` | Kontingent oder Limit des Schlüssels ist erschöpft |
| `partial` | Ein Teil der Felder ist gescheitert; `processing.errors` nennt die Ursachen |
| Inhaltstyp `(keywords)` | Der Inhaltstyp ist nur über Schlüsselwörter geraten; das LLM gab keine brauchbare Antwort |

**Test 3: Erschließung aus einer URL** (Textextraktion über die B-API). Wie
Test 2, mit diesem Body:

```js
    body: JSON.stringify({ input_source: "url", source_url: "https://klexikon.zum.de/wiki/Saturn",
                           schema_file: "auto", language: "de", enable_geocoding: false })
```

| Ergebnis | Bedeutung |
|---|---|
| `200`, `success` | In Ordnung: Der Schlüssel darf den Proxy-Dienst `text-extraction` nutzen |
| `502`, `TEXT_EXTRACTION_AUTH_REFUSED` | Die B-API lehnt den Schlüssel für die Textextraktion ab. Recht für den Proxy-Dienst `text-extraction` ergänzen |
| `502`, `TEXT_EXTRACTION_AUTH_MISSING` | Kein Schlüssel für den Aufruf: Modus `forward` ohne `Authorization`, oder `auto` ohne `B_API_KEY` |
| `503`, `TEXT_EXTRACTION_NOT_CONFIGURED` | Betrieb ohne B-API mit einem Repository, das weder Staging noch Produktion ist: `METADATA_AGENT_TEXT_EXTRACTION_API_URL` setzen |
| `504`, `TEXT_EXTRACTION_CONNECT_TIMEOUT` | Der Textextraktionsdienst ist nicht erreichbar |

**Test 4: Orte mit Koordinaten** (Geocoder über die B-API). Wie Test 2, mit
`enable_geocoding: true` und einem Text mit Ort, zum Beispiel „Workshop am
15. März 2025 im Bauhaus-Museum Weimar“. Zusätzlich ausgeben:
`console.log(data["schema:location"], p.warnings)`.

| Ergebnis | Bedeutung |
|---|---|
| Ort mit `geo.latitude` und `geo.longitude` | In Ordnung: Der Schlüssel darf den Proxy-Dienst `photon_geocoding` nutzen |
| Ort ohne `geo`, und „Orte bleiben ohne Koordinaten …“ in `processing.warnings` | Die B-API lehnt den Schlüssel für den Geocoder ab oder drosselt ihn; die Warnung nennt den Status. Recht für `photon_geocoding` ergänzen |

Die Tests 3 und 4 je einmal eingeloggt und als Gast ausführen: edu-sharing
nutzt dafür zwei verschiedene Schlüssel.

### Tests ohne den Proxy

Eigenständig oder direkt an einem Container gehen dieselben Anfragen an
Port 8000. Der Agent nutzt dann `B_API_KEY` oder `OPENAI_API_KEY`:

```bash
curl -s http://127.0.0.1:8000/extract-field \
  -H "Content-Type: application/json" \
  -d '{"text": "Am 12. November findet in Weimar ein Konzert statt.", "schema_file": "core.json", "field_id": "cclom:title", "language": "de"}'
```

### Wenn ein Test scheitert

1. Die Anfrage-ID notieren (`X-Request-ID`, `error.request_id`) und die
   Logzeile mit derselben ID suchen.
2. `METADATA_AGENT_DEBUG_ENDPOINTS=true` setzen und `/debug/auth` sowie
   `/debug/dependencies` über denselben Pfad aufrufen. `/debug/auth` zeigt, ob
   ein weitergereichter Schlüssel ankommt (nur Vorhandensein, Schema und
   Länge). `/debug/dependencies` zeigt, ob B-API, Textextraktion, Geocoder und
   Repository erreichbar sind und ob der Schlüssel angenommen wird.
   Einzelheiten: [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md).
3. Die Debug-Endpunkte wieder ausschalten.

---

## Erste Installation im Cluster: Prüfliste

Der Chart wurde noch nicht in einem Cluster installiert. Diese Liste ist die
erste Probe; die Ergebnisse gehören ins Übergabeprotokoll. `<ns>` ist der
Ziel-Namespace, `<release>` der Release-Name und `<name>` der Name der
Ressourcen: der Release-Name, wenn er „metadata-agent“ enthält, sonst
`<release>-metadata-agent` (`helm install` nennt ihn).

1. **Image bauen und pushen.** Das Git-Tag der Version (`v4.0.1`) auf den
   Commit setzen, aus dem der Chart stammt. Die GitLab-Pipeline baut, startet
   den Smoke-Test (schreibgeschützt, Chromium), scannt mit Trivy und pusht
   erst danach. Prüfen:
   `docker pull $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v4.0.1`.
   Ohne dieses Tag in der Registry endet `helm install` mit den Defaults in
   `ImagePullBackOff`.
2. **Namespaces und Ports ermitteln:**
   - `kubectl get ns -L kubernetes.io/metadata.name` zeigt die Namespaces der
     B-API (Aufrufer und Ziel) und von Prometheus.
   - `kubectl -n <b-api-ns> get svc,endpoints -o wide` zeigt die Ports der
     B-API-Pods für `networkPolicy.egressPorts`. Im Default ruft der Agent LLM,
     Textextraktion und Geocoder über `METADATA_AGENT_B_API_BASE_URL` auf. Die
     Textextraktion im Cluster braucht ihre Ports nur, wenn
     `METADATA_AGENT_TEXT_EXTRACTION_API_URL` auf sie zeigt.
   - `kubectl -n kube-system get pods -l k8s-app=kube-dns` muss den Cluster-DNS
     finden. Wenn nicht, Label und Namespace in `networkPolicy.dns` anpassen;
     bei NodeLocal DNSCache `dns.namespace: ""` setzen.
   - `kubectl get ingress -A | grep -e repository -e redaktion -e b-api`:
     Liegen Repository oder B-API im selben Cluster, den Namespace des
     Ingress-Controllers in `egressNamespaces` aufnehmen (siehe
     [Grenzen der NetworkPolicy](#grenzen-der-networkpolicy)). **Das zuerst
     prüfen:** Es betrifft LLM, Textextraktion und Geocoder zugleich.
3. **Secret anlegen** (`kubectl -n <ns> create secret generic …`, siehe
   [Installieren](#installieren)): `B_API_KEY` (im Modus `forward` nicht
   nötig), `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`.
4. **Values-Datei schreiben**, zum Beispiel `staging-values.yaml`:
   `image.repository`, `networkPolicy.*` aus Schritt 2 und unter `env` die
   Adressen der Umgebung (`METADATA_AGENT_B_API_BASE_URL`,
   `METADATA_AGENT_REPOSITORY_URL`), den Inbox-Ordner dieses Repositorys
   (`METADATA_AGENT_WLO_INBOX_ID`) sowie `METADATA_AGENT_B_API_AUTH_MODE`.
   Textextraktion und Geocoder folgen der B-API; die Schlüssel brauchen die
   Rechte für die Proxy-Dienste `text-extraction` und `photon_geocoding`.
5. **Prüfen und installieren:**
   ```bash
   helm lint helm/metadata-agent --strict -f staging-values.yaml
   helm upgrade --install <release> helm/metadata-agent -n <ns> \
     -f staging-values.yaml --set secret.existingSecret=metadata-agent \
     --wait --timeout 5m
   ```
6. **Pods und Probes:**
   - `kubectl -n <ns> get pods -o wide`: alle Pods `Running` und bereit, wo
     möglich auf verschiedenen Knoten.
   - `kubectl -n <ns> describe pod -l app.kubernetes.io/name=metadata-agent`:
     keine fehlgeschlagenen Probes.
   - `kubectl -n <ns> logs deploy/<name>`: kein `Nicht bereit:`; die
     Startwarnungen (`⚠️`) lesen.
   - Der interne Port:
     ```bash
     kubectl -n <ns> port-forward deploy/<name> 9000:9000
     curl -s localhost:9000/ready     # {"status":"ready"}
     curl -s localhost:9000/metrics | grep metadata_agent_llm_requests_total
     ```
     Der letzte Befehl zeigt die beim Start angelegten Serien mit dem Wert `0`.
     `kubectl -n <ns> get svc` nennt zwei Services: `<name>` mit Port 8000 und
     `<name>-internal` mit Port 9000.
7. **NetworkPolicy:**
   - Aus dem Namespace der B-API muss der Agent antworten, aus einem anderen
     nicht:
     ```bash
     kubectl -n <b-api-ns> run np-test --rm -it --restart=Never \
       --image=curlimages/curl -- curl -s -m 5 http://<name>.<ns>:8000/health
     kubectl -n default run np-test --rm -it --restart=Never \
       --image=curlimages/curl -- curl -s -m 5 http://<name>.<ns>:8000/health
     ```
   - Ausgehend aus dem Pod (DNS, B-API, Repository):
     ```bash
     kubectl -n <ns> exec deploy/<name> -- python -c \
       "import urllib.request as u; print(u.urlopen('<REPOSITORY_URL>/_about', timeout=10).status)"
     kubectl -n <ns> exec deploy/<name> -- python -c \
       "import urllib.request as u, urllib.error as e
     try: print(u.urlopen('<B_API_BASE_URL>/api/v1/llm/openai/models', timeout=10).status)
     except e.HTTPError as x: print('reachable, HTTP', x.code)"
     ```
     Die B-API antwortet ohne Schlüssel mit `401`; das heißt erreichbar. Ein
     Timeout heißt: Namespace, Port oder den DNAT-Fall aus Schritt 2 prüfen. Ob
     der Schlüssel Textextraktion und Geocoder nutzen darf, zeigt erst
     Schritt 10.
8. **Screenshot im schreibgeschützten Pod:**
   ```bash
   kubectl -n <ns> port-forward deploy/<name> 8000:8000
   curl -s -o /dev/null -w "%{http_code} %{content_type}\n" -X POST localhost:8000/screenshot \
     -H "Content-Type: application/json" -d '{"url": "https://example.org/"}'
   ```
   Erwartet: `200 application/json`; die Antwort enthält das Bild als
   `image_base64`. Im Log kein `EPERM`, `EROFS` oder `ENOSPC`.
9. **Webkomponente über den Proxy:**
   `…/proxy/metadata-agent-canvas/widget/info` über den B-API-Proxy aufrufen
   (Browserkonsole einer edu-sharing-Seite). Die Antwort nennt relative
   Verweise (`dist/main.js`), aufzulösen gegen die Adresse des Aufrufs. Danach
   muss `…/proxy/metadata-agent-canvas/widget/dist/main.js` mit `200`
   antworten.
10. **Ende zu Ende über den B-API-Proxy:** die Tests 1 bis 4 aus
    [Nach dem Deployment prüfen](#nach-dem-deployment-prüfen). Mit
    `forwardApiKey` zeigt Test 2 `keySource: forwarded`. Die Tests 3 und 4
    prüfen die Rechte für `text-extraction` und `photon_geocoding`, je einmal
    eingeloggt und als Gast.
11. **Überwachung:** `kubectl -n <ns> get servicemonitor,prometheusrule`. In
    Prometheus ist das Target (der Service `<name>-internal`) `up`, und die
    Regeln `metadata-agent` sind geladen.
12. **Rollback einmal üben** ([Rollback](#rollback)), solange noch niemand die
    Umgebung nutzt.

---

## Updates

Bei jedem Update:

- [ ] Den Update-Hinweis der Version im [CHANGELOG.md](CHANGELOG.md) und den
      Abschnitt unten lesen.
- [ ] Chart und Image aus demselben Stand verwenden.
- [ ] Docker: das neue Tag ziehen und den Container neu anlegen. Helm:
      `helm upgrade <release> helm/metadata-agent -n <ns> -f <values> --wait`.
- [ ] Die [Prüfungen nach dem Deployment](#nach-dem-deployment-prüfen)
      ausführen.

Der Agent ist zustandslos: Es gibt keine Datenbank und keine Migration.

### Update von 3.0.x auf 4.0.0

Die Einzelheiten stehen im CHANGELOG unter 4.0.0 und 4.0.1. Der Chart nennt
`image.tag: v4.0.1` und braucht das Image dieser Version: Mit
dem am 20.10.2026 als 3.0.1 veröffentlichten Image funktioniert er nicht (siehe
„Chart und Image zusammenhalten“ unten).

**Vor dem Update:**

- [ ] **Schlüsselrechte in der B-API ergänzen.** Jeder Schlüssel, mit dem der
      Agent aufgerufen wird (Nutzer- und Gast-Schlüssel aus edu-sharing,
      `B_API_KEY`), braucht zusätzlich zu den LLM-Endpunkten die Proxy-Dienste
      `text-extraction` und `photon_geocoding`. Sonst endet die Erschließung
      aus einer URL mit `502` `TEXT_EXTRACTION_AUTH_REFUSED`, und Orte bleiben
      ohne Koordinaten. Wer beim bisherigen Weg bleiben will, setzt
      `METADATA_AGENT_B_API_SERVICES=direct`.
- [ ] **Eigene Values-Dateien durchsehen.** Steht dort
      `METADATA_AGENT_TEXT_EXTRACTION_API_URL` (der Chart setzte bisher
      `http://bildungs-api-services-full-text.b-api`), bleibt die
      Textextraktion bei dieser Adresse, und nur der Geocoder wechselt zur
      B-API. Für den Weg über die B-API die Zeile entfernen.
- [ ] **Chart und Image zusammenhalten.** Der Chart trägt jetzt die Version
      des Dienstes (bisher 0.3.1). Mit einem Image bis zum veröffentlichten
      3.0.1 funktioniert er nicht: Der Chart lässt die Adresse der
      Textextraktion leer, und das ältere Image liest das als „keine Adresse“.
- [ ] **Log-Auswertungen anpassen.** In den JSON-Zeilen heißt `time` jetzt
      `timestamp`. `trace_id` ist die B3-Trace-ID; der bisherige Wert steht in
      `request_id`. Neu sind `service` und `span_id`. Das Access-Log von
      uvicorn entfällt; dafür gibt es eine Zeile je Anfrage.
- [ ] **Alarmregeln:** Neu ist `MetadataAgentUpstreamKeyRefused` (die B-API
      lehnt den Schlüssel für Textextraktion oder Geocoder ab).
      `MetadataAgentGenerateSlow` löst jetzt beim Anteil der Läufe über 270 s
      aus und braucht die Buckets des neuen Images.
- [ ] **Werte der Einstellungen prüfen.** Ein Wert, den eine Einstellung nicht
      zulässt, verhindert jetzt den Start, statt übernommen zu werden: ein
      unbekannter Wert für `METADATA_AGENT_LLM_VERBOSITY` oder Reasoning
      Effort, eine Temperatur außerhalb von 0–2,
      `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS`,
      `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS`,
      `METADATA_AGENT_LLM_MAX_TOKENS` oder `METADATA_AGENT_REQUEST_TIMEOUT`
      unter 1, eine negative Rate, eine Screenshot-Größe oder -Wartezeit
      außerhalb ihrer Grenzen, ein `METADATA_AGENT_ALLOWED_LLM_MODELS`, das
      kein Modell nennt. Die Grenzen stehen in
      [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md).
      `METADATA_AGENT_SCREENSHOT_METHOD` und `METADATA_AGENT_DEFAULT_CONTEXT`
      gibt es nicht mehr; beide entfernen.
- [ ] **Interner Port des Charts.** Port 9000 ist vom Service `<name>` in
      einen eigenen Service `<name>-internal` gewandert (immer `ClusterIP`).
      Der ServiceMonitor des Charts folgt; eine eigene Scrape-Konfiguration
      oder ein Port-Forward, die den alten Service nennen, anpassen.
- [ ] **Values-Dateien des Charts.** `env.METADATA_AGENT_INTERNAL_HOST` wird
      jetzt abgelehnt: stattdessen `listenHost` setzen (`"::"` in einem
      IPv6-Cluster). Eine Ingress-Annotation `proxy-body-size`, die von
      `env.METADATA_AGENT_MAX_REQUEST_BYTES` abweicht, und eine leere B-API-
      oder Repository-Adresse brechen das Rendern ab.
- [ ] **Clients und Proxys, die sich auf eine Weiterleitung verlassen.** Ein
      Pfad mit abschließendem Schrägstrich (`/generate/`, `/docs/`) antwortet
      mit `404`; bisher leitete der Agent mit `307` weiter. `/widget/info`
      liefert relative Verweise (`dist/main.js`) statt absoluter URLs, und
      seine Snippets enthalten den Platzhalter `{AGENT_URL}`.

**Nach dem Update:**

- [ ] Die Tests 2 bis 4 aus
      [Nach dem Deployment prüfen](#nach-dem-deployment-prüfen) ausführen, je
      einmal eingeloggt und als Gast.

Ebenfalls neu, ohne dass etwas zu tun ist:

- `METADATA_AGENT_SERVICE_NAME` (Default `metadata-agent`) und
  `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` (Default: keine Grenze).
- Eine leere Umgebungsvariable (`VAR=` in einer `.env`, `""` in den
  Chart-Werten) gilt als nicht gesetzt; es gilt der Default. Ausnahmen:
  `METADATA_AGENT_LLM_VERBOSITY`, `METADATA_AGENT_LLM_REASONING_EFFORT` und
  `METADATA_AGENT_CORS_ORIGINS`, bei denen „leer“ ein Wert ist.
- Eine vom Agent erzeugte Anfrage-ID ist jetzt die B3-Trace-ID: 32 statt 16
  Hex-Zeichen.
- Status `499` im Log und in den Metriken: Der Aufrufer hat die Verbindung
  geschlossen. `metadata_agent_generations_total` hat die neuen Status
  `cancelled` und `refused`.
- `METADATA_AGENT_GRACE_PERIOD_SECONDS` (setzt der Chart) und die Chart-Werte
  `image.digest`, `listenHost` und `env.METADATA_AGENT_WLO_INBOX_ID`.
- Das Image startet über `tini`, enthält von Chromium nur die Headless-Shell
  und hält ungenutzte Verbindungen 75 s offen.

### Update von 2.1.x auf 3.0.0

Mit Docker oder Compose genügt es, das Image zu tauschen. Mit dem Helm-Chart
zuerst die Namespaces der NetworkPolicy anpassen. Clients, die auf Statuscodes
verzweigen, prüfen die Tabelle „Breaking“ im CHANGELOG; die mitgelieferte
Webkomponente ist nicht betroffen. Die Webkomponente 3.x braucht den Agent 3.x.

- [ ] **NetworkPolicy** (neu, standardmäßig an):
      `networkPolicy.ingressNamespaces`, `prometheusNamespaces`,
      `egressNamespaces`, `egressPorts` und `dns` an den Cluster anpassen
      (siehe [NetworkPolicy](#networkpolicy)).
- [ ] **`METADATA_AGENT_INTERNAL_PORT`** kommt jetzt aus
      `service.internalPort`. Einen eigenen Wert unter `env` entfernen.
- [ ] **`/ready` verlangt das Repository-Konto.** Ohne `WLO_GUEST_USERNAME`
      oder `WLO_GUEST_PASSWORD`, etwa nach einem Tippfehler in
      `secret.existingSecret`, wird der Pod nicht bereit. Ein Deployment, das
      nur erschließt, setzt `env.METADATA_AGENT_REQUIRE_REPOSITORY: "false"`.
- [ ] **Secret-Änderungen starten die Pods nicht mehr neu.** Die Annotation
      `checksum/secret` ist entfallen. Nach einer Änderung:
      `kubectl rollout restart deployment/<name>`.
- [ ] **Timeouts vor dem Agent: mindestens ~310 s** im B-API-Proxy, in
      edu-sharing und in einem eigenen Ingress. `/generate` darf jetzt 300 s
      laufen (`METADATA_AGENT_GENERATE_TIMEOUT`), auch ohne Ingress; 2.1.x lief
      dort unbegrenzt. `/upload` und `/nodes` enden nach 100 s
      (`METADATA_AGENT_UPLOAD_TIMEOUT`).
- [ ] **Log-Filter:** Die Logger heißen nach den neuen Modulen
      (`src.api.responses`, `src.api.routers.*`, `src.services.llm_transport`).
      Filter auf `logger="src.main"` anpassen.
- [ ] **Hinter TLS:** `forwardedAllowIps` (Helm) oder `FORWARDED_ALLOW_IPS`
      (Docker) setzen, siehe [Hinter TLS](#hinter-tls-ingress-proxy).
- [ ] **Docker Compose:** Der Port ist nur noch auf `127.0.0.1` freigegeben,
      der Container läuft schreibgeschützt, und das Stop-Timeout beträgt
      320 s. Wer den Agent von anderen Rechnern erreichen muss, setzt
      `"8000:8000"`, nur hinter einer Zugangsbeschränkung. Eine vorhandene
      `.env` erreicht den Container jetzt vollständig (Docker Compose 2.24
      oder neuer).

Geändert, ohne dass etwas zu tun ist: Das Root-Dateisystem ist schreibgeschützt
(`/tmp` und `/home/appuser` sind `emptyDir`-Volumes mit Größenlimit), der Pod
bekommt kein Service-Account-Token, die Replicas werden über Knoten verteilt,
und die Basis-Images sind per Digest festgelegt. Der Quelltext einer
Erschließung wird auf 150.000 Zeichen gekürzt; `processing.warnings` nennt dann
die Originallänge.

### Ältere Versionen

Von 2.0.x zuerst auf 2.1.x aktualisieren; die Hinweise stehen in
[docs/update-2.0-auf-2.1.md](docs/update-2.0-auf-2.1.md). Was für den Betrieb weiter zählt, steht oben: der weitergereichte
`Authorization`-Header und der Modus `static`
([Eigenständig](#eigenständig)), die Größengrenze der Anfragen und die Probe
auf `/ready`. Zwei Änderungen dieses Updates werden leicht übersehen:
Fehlschläge kommen als HTTP-Fehler (`502`, `503`, `504` mit `error.code`) statt
als `200` mit leeren Feldern, und ein unbekannter Wert von
`METADATA_AGENT_LLM_PROVIDER` lässt den Agent nicht starten.

---

## Rollback

Ein Rollback tauscht nur Image und Manifeste. Nodes, die `/upload` oder
`/nodes` schon geschrieben haben, bleiben im Repository. Die Webkomponente
steckt im Image und geht mit zurück.

- **Helm:**
  ```bash
  helm history <release> -n <ns>
  helm rollback <release> <REVISION> -n <ns> --wait
  ```
  Helm stellt die Manifeste und Values der gewählten Revision wieder her. Hatte
  diese Revision keine NetworkPolicy (Chart 0.2.x), wird die Policy entfernt.
  Ein Secret aus `secret.existingSecret` bleibt unverändert.
- **Nur das Image zurück:**
  ```bash
  helm upgrade <release> helm/metadata-agent -n <ns> \
    --reuse-values --set image.tag=<previous tag> --wait
  ```
  **Nicht auf ein Image bis zum veröffentlichten 3.0.1.** Der aktuelle Chart
  lässt die Adresse der Textextraktion leer, und erst ab 4.0.0
  liest „leer“ als „über die B-API“. Ein älteres Image ruft dann `/from-url`
  ohne Adresse auf, und die Erschließung aus URLs scheitert. Dafür
  `helm rollback` nehmen (stellt auch die alten Values wieder her) oder
  zusätzlich `--set env.METADATA_AGENT_TEXT_EXTRACTION_API_URL=<address>`
  setzen.
- **Docker / Compose:** das vorige Image-Tag setzen und den Container neu
  anlegen.
- **Clients:** Wer Clients auf die Statuscodes von 3.0.0 umgestellt hat, prüft
  die Tabelle „Breaking“ im CHANGELOG, bevor es zurück auf 2.1.x geht.
- **Zurück auf 2.0.x:** das Image-Tag zurücksetzen. 2.0.x ignoriert
  Einstellungen, die es nicht kennt, auch in einer `.env`-Datei.
