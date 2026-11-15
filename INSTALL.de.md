# Metadata Agent API — Installation

[English](INSTALL.md) · **Deutsch**

So kommt die Metadata Agent API (Version 4.0.1) zum Laufen und so lässt sich
prüfen, dass sie arbeitet: lokal mit uv oder pip, in Docker und mit Docker
Compose.

Themen des Produktivbetriebs — Kubernetes und Helm, Proxys, Skalierung,
Updates, Rollback, Monitoring — stehen in [DEPLOYMENT.de.md](DEPLOYMENT.de.md).
Alle Einstellungen nennt [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md), die
Endpunkte [docs/API.de.md](docs/API.de.md), einen Überblick über den Dienst
gibt [README.de.md](README.de.md).

Die Beispiele verwenden eine POSIX-Shell (Linux, macOS, WSL, Git Bash).

## Inhalt

- [Voraussetzungen](#voraussetzungen)
- [Lokal starten mit uv](#lokal-starten-mit-uv)
- [Lokal starten mit pip](#lokal-starten-mit-pip)
- [Screenshot-Browser](#screenshot-browser)
- [Docker](#docker)
- [Docker Compose](#docker-compose)
- [Minimale Konfiguration](#minimale-konfiguration)
- [Installation prüfen](#installation-prüfen)
- [Tests ausführen](#tests-ausführen)
- [Fehlersuche](#fehlersuche)
- [Weitere Dokumente](#weitere-dokumente)

## Voraussetzungen

Einen Weg wählen:

| Weg | Braucht | Geeignet für |
|---|---|---|
| [Lokal mit uv](#lokal-starten-mit-uv) | Python 3.13 oder neuer, uv 0.7.13 oder neuer, Git | Entwicklung, Tests ausführen |
| [Lokal mit pip](#lokal-starten-mit-pip) | Python 3.13 oder neuer, pip, Git | Rechner ohne uv |
| [Docker](#docker) | Docker 24 oder neuer | Einen Container, der Chromium schon enthält |
| [Docker Compose](#docker-compose) | Docker Compose 2.24 oder neuer | Denselben Container mit voreingestellten Laufzeitoptionen |

uv 0.7.13 ist die Version, die `uv.lock` geschrieben hat; das `Dockerfile` und
beide CI-Pipelines legen sie fest. Lokal läuft der Dienst unter Linux, macOS
und Windows (unter Windows ohne `uvloop`). Das Container-Image ist ein
Linux-Image.

Jeder Weg braucht außerdem:

- **Einen Schlüssel für die LLM-Aufrufe:** `B_API_KEY` für die B-API von
  OpenEduHub (der Standard; die Schlüssel vergibt das WLO-Team) oder
  `OPENAI_API_KEY` für OpenAI oder einen OpenAI-kompatiblen Endpunkt. Siehe
  [Minimale Konfiguration](#minimale-konfiguration).
- **Ausgehendes HTTPS** zu den Diensten, die die Konfiguration nennt:

| Ziel | Standard | Wofür |
|---|---|---|
| B-API | `https://b-api.staging.openeduhub.net` | LLM-Aufrufe; Textextraktion für URL-Eingaben und Geocoder über ihre Proxy-Pfade |
| Repository (edu-sharing) | `https://repository.staging.openeduhub.net/edu-sharing/rest` | Nodes lesen, `/upload`, `/nodes` |
| Nur mit Provider `openai` | `https://api.openai.com/v1`, `https://text-extraction.staging.openeduhub.net`, `https://photon.komoot.io/api` | LLM-Aufrufe, Textextraktion, Geocoder — direkt aufgerufen statt über die B-API |
| Öffentliche Webseiten | — | Screenshots, aufgenommen vom lokalen Chromium |
| Während der Installation | PyPI und der Download-Server von Playwright; für `docker build` zusätzlich Docker Hub, `ghcr.io` und die Debian-Paketquellen | Python-Pakete, Chromium, Basis-Images, Systembibliotheken |

Den Ressourcenbedarf einer lokalen Installation hat niemand gemessen. Als
Anhaltspunkt: Der Helm-Chart fordert 500m CPU und 512Mi Arbeitsspeicher an und
begrenzt den Container auf 2 CPUs und 2Gi — Schätzwerte aus
`helm/metadata-agent/values.yaml`.

## Lokal starten mit uv

```bash
# Code holen
git clone <repo-url> metadata-agent
cd metadata-agent

# .venv anlegen und die in uv.lock festgelegten Versionen installieren
uv sync

# Chromium für Screenshots (optional, siehe „Screenshot-Browser“)
uv run playwright install chromium

# Konfiguration: Vorlage kopieren, dann B_API_KEY in .env eintragen
cp .env.template .env

# Mit Auto-Reload starten, nur von diesem Rechner erreichbar
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

Weiter mit [Installation prüfen](#installation-prüfen).

- **Abhängigkeiten:** `uv sync` installiert die Laufzeitpakete und die
  Entwicklungswerkzeuge (pytest, ruff, mypy). `uv sync --no-dev` lässt die
  Werkzeuge weg. `uv sync --locked` scheitert zusätzlich, wenn `uv.lock` und
  `pyproject.toml` nicht zusammenpassen.
- **Arbeitsverzeichnis:** aus dem Wurzelverzeichnis des Repositorys starten.
  Die App wird als `src.main:app` angesprochen und ist nicht als Paket
  installiert, und der Dienst liest `.env` aus dem Arbeitsverzeichnis.
- **`--reload`** startet den Server neu, sobald sich eine Quelldatei ändert.
  Sonst weglassen. Unter Windows brauchen Screenshots einen Start ohne
  `--reload` (siehe [Fehlersuche](#fehlersuche)).
- **Andere Rechner:** `--host 0.0.0.0` öffnet den Port auf allen
  Schnittstellen. Die API prüft selbst keine Zugangsdaten: Wer den Port
  erreicht, löst Erschließungen auf dem `B_API_KEY` der Installation aus. Nur
  hinter einer Zugangsbeschränkung verwenden
  ([DEPLOYMENT.de.md](DEPLOYMENT.de.md)).
- **Ein Worker je Prozess:** `--workers` nicht verwenden. Mit
  `METADATA_AGENT_INTERNAL_PORT` würde jeder Worker den internen Port
  belegen, und Metriken zählen je Prozess. Skaliert wird über weitere
  Instanzen ([DEPLOYMENT.de.md](DEPLOYMENT.de.md)).

## Lokal starten mit pip

`requirements.txt` ist der Export von `uv.lock`: dieselben exakten Versionen,
jede mit Hashes, nur die Laufzeitpakete.

```bash
# Virtuelle Umgebung mit Python 3.13 oder neuer
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Genau die festgelegten Versionen installieren; pip prüft jeden Hash
python -m pip install --require-hashes -r requirements.txt

# Chromium für Screenshots (optional, siehe „Screenshot-Browser“)
playwright install chromium

# Konfiguration: Vorlage kopieren, dann B_API_KEY in .env eintragen
cp .env.template .env

# Starten, nur von diesem Rechner erreichbar
uvicorn src.main:app --host 127.0.0.1 --port 8000
```

Die Hinweise des uv-Wegs zu Arbeitsverzeichnis, Host und Workern gelten auch
hier. Die Testwerkzeuge gehören nicht zu `requirements.txt`; die
[Tests](#tests-ausführen) laufen über den uv-Weg.

Nach jeder Änderung an `uv.lock` die Datei mit dem Befehl aus ihrem Kopf neu
erzeugen (`tests/test_requirements_in_step.py` prüft, dass beide
übereinstimmen):

```bash
uv export --frozen --no-dev --format requirements-txt --no-emit-project -o requirements.txt
```

### Installierte Pakete

Beide Wege installieren dieselben Versionen; `uv.lock` nennt sie. Die
Mindestversionen stammen aus `pyproject.toml`:

| Paket | Mindestversion | Zweck |
|---|---|---|
| fastapi | 0.142.4 | Web-Framework |
| uvicorn | 0.32.0 | ASGI-Server, ohne das Extra `standard` |
| httptools | 0.6.3 | Schneller HTTP-Parser für uvicorn |
| uvloop | 0.21.0 | Schnelle Event-Loop für uvicorn; unter Windows nicht installiert |
| pydantic | 2.10.0 | Datenvalidierung |
| pydantic-settings | 2.14.2 | Einstellungen aus Umgebungsvariablen und `.env` |
| httpx | 0.28.0 | HTTP-Client, auch für jeden LLM-Aufruf (es gibt kein OpenAI-SDK) |
| playwright | 1.63.0 | Steuert Chromium für Screenshots und legt dessen Version fest |
| prometheus-client | 0.26.0 | Metriken (`/metrics` auf dem internen Port) |
| starlette | 1.3.1 | Middlewares und Antworten, direkt importiert |

Drei Pakete, die nur indirekt installiert werden, haben Mindestversionen wegen
bekannter Sicherheitslücken in älteren: anyio 4.14.2, idna 3.15 und
python-dotenv 1.2.2 (`[tool.uv] constraint-dependencies` in `pyproject.toml`).
uv wendet sie an, wenn es den Lock schreibt; pip erhält sie über die
festgelegten Versionen in `requirements.txt`.

## Screenshot-Browser

Screenshots (`POST /screenshot`, Vorschaubilder von Nodes) nimmt ein lokales
Chromium ohne Oberfläche auf, das Playwright steuert. Es gehen keine Daten an
einen externen Screenshot-Dienst; die Methode `pageshot` ist ein Legacy-Alias
für dieselbe lokale Aufnahme. Ohne Chromium funktioniert alles andere, und
Screenshot-Anfragen antworten mit `502`.

| Umgebung | Was zu tun ist |
|---|---|
| Docker-Image, Docker Compose | Nichts: Chromium und seine Systembibliotheken gehören zum Image |
| Windows, macOS | `uv run playwright install chromium` (pip-Weg: ohne `uv run`) |
| Linux | Dasselbe, dazu die Systembibliotheken: `uv run playwright install-deps chromium` (braucht Root-Rechte) |

Unter Debian 13 lassen sich die Systembibliotheken stattdessen von Hand
installieren. Das ist die Liste, die das Image verwendet (`Dockerfile`); andere
Distributionen benennen einige Pakete anders, dort `playwright install-deps`
verwenden.

```bash
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libx11-xcb1 fonts-liberation fonts-noto-color-emoji
```

Prüfen, dass der Browser startet (es wird keine Seite geladen):

```bash
uv run python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(headless=True); print('Chromium', b.version); b.close(); p.stop()"
```

- Das Paket `playwright` legt die Chromium-Version fest. Nach einem Update von
  `playwright` erneut `playwright install chromium` ausführen.
- `uv run python scripts/check_browser_age.py` gibt die mitgelieferte
  Chromium-Version aus und endet mit 1, wenn sie mehr als zwei Hauptversionen
  hinter dem stabilen Chrome liegt. Das Skript fragt den Versionsdienst von
  Google; ohne Verbindung gibt es nur die Version aus. Mit
  `REQUIRE_BROWSER_AGE=1` (in beiden CI-Pipelines gesetzt) nimmt es dann
  stattdessen das Upload-Datum des gesperrten `playwright` aus `uv.lock` und
  schlägt fehl, wenn es mehr als 90 Tage zurückliegt oder nicht lesbar ist.
- Die Einstellungen `METADATA_AGENT_SCREENSHOT_*` beschreibt
  [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md).

## Docker

Das Image enthält den Dienst, die Webkomponente und Chromium.

### Bauen und starten

```bash
# Image bauen
docker build -t metadata-agent-api .

# Mit den Einstellungen aus .env starten
docker run -d \
  --name metadata-agent-api \
  -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp --tmpfs /home/appuser \
  --stop-timeout 320 \
  --env-file .env \
  --restart unless-stopped \
  metadata-agent-api
```

Ohne `.env`-Datei einzelne Einstellungen mit `-e` übergeben:

```bash
docker run -d \
  --name metadata-agent-api \
  -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp --tmpfs /home/appuser \
  --stop-timeout 320 \
  -e B_API_KEY=<key> \
  -e METADATA_AGENT_REQUIRE_REPOSITORY=false \
  metadata-agent-api
```

| Option | Warum |
|---|---|
| `-p 127.0.0.1:8000:8000` | Nur von diesem Rechner erreichbar. Die API prüft selbst keine Zugangsdaten: Wer den Port erreicht, löst Erschließungen auf `B_API_KEY` aus. Für andere Rechner einen Proxy mit Zugangsbeschränkung davorsetzen ([DEPLOYMENT.de.md](DEPLOYMENT.de.md)) |
| `--read-only --tmpfs /tmp --tmpfs /home/appuser` | Schreibgeschütztes Wurzeldateisystem wie im Helm-Chart. Chromium schreibt Profil und Caches in diese beiden Verzeichnisse |
| `--stop-timeout 320` | Ein laufendes `/generate` darf bis zu 300 s dauern (`METADATA_AGENT_GENERATE_TIMEOUT`). Dockers Standard von 10 s würde es bei jedem Stopp oder Neustart abschneiden |
| `--env-file .env` | Übergibt die Einstellungen als Umgebungsvariablen; das Image enthält keine `.env`. Docker übernimmt jede Zeile wörtlich: Werte ohne Anführungszeichen schreiben |
| `--restart unless-stopped` | Startet den Container nach einem Absturz oder Neustart des Rechners wieder |

Varianten beim Bauen:

| Befehl | Wirkung |
|---|---|
| `docker build --build-arg VERSION=v4.0.1 --build-arg REVISION=$(git rev-parse HEAD) -t metadata-agent-api:v4.0.1 .` | Schreibt Version und Commit in die Labels des Image (Standard: `dev`, `unknown`) |
| `docker build --no-cache -t metadata-agent-api .` | Baut jede Ebene neu |
| `docker build --progress=plain -t metadata-agent-api .` | Zeigt die vollständige Build-Ausgabe |

Die CI-Pipelines veröffentlichen Images in der internen Registry (Tags `main`,
`develop` und Release-Tags wie `v4.0.1`). Wie man sie bezieht und startet,
steht in [DEPLOYMENT.de.md](DEPLOYMENT.de.md).

### Was das Image enthält

| Eigenschaft | Wert |
|---|---|
| Basis | `python:3.13-slim` (Debian 13 „trixie“), per Digest festgelegt |
| Build | Zwei Stufen: Der Builder installiert die Pakete mit uv 0.7.13 (`uv sync --locked --no-install-project --no-dev`); das fertige Image erhält nur die fertige `.venv`, keine Build-Werkzeuge |
| Inhalt | Die Pakete aus `uv.lock`, die Headless-Shell von Chromium in `/opt/playwright-browsers` (`playwright install --only-shell chromium`) mit 20 Systempaketen, `tini` als Init-Prozess, `src/` samt Webkomponente (`src/static/widget/`), `LICENSE` und `NOTICE` |
| Benutzer | `appuser` (UID 1000), nicht root. `/app` gehört root: Der Prozess kann seinen Code lesen, aber nicht ändern |
| Port | 8000 |
| Startbefehl | `tini -- uvicorn src.main:app --port 8000 --timeout-keep-alive 75` (ein Worker). uvicorn lauscht auf der Adresse in `UVICORN_HOST`, die das Image auf `0.0.0.0` setzt; ungenutzte Verbindungen bleiben 75 s offen |
| Health Check | Eingebaut: `/health` alle 30 s, Timeout 10 s, 3 Wiederholungen, Startphase 5 s |
| Zustand | Keiner; es wird kein Volume gebraucht |
| Labels | `org.opencontainers.image.version` und `.revision` aus den Build-Argumenten `VERSION` und `REVISION` |

`.dockerignore` hält `.git`, `.env` und `.env.*` (außer `.env.template`),
`*.md`, `.venv`, Caches und Logs aus dem Build-Kontext heraus.

### Container verwalten

```bash
docker logs -f metadata-agent-api                                       # Log mitlesen
docker inspect --format='{{.State.Health.Status}}' metadata-agent-api   # Ergebnis des Health Checks
docker stats metadata-agent-api                                         # CPU und Arbeitsspeicher
docker exec -it metadata-agent-api bash                                 # Shell im Container
docker stop metadata-agent-api && docker rm metadata-agent-api          # stoppen und entfernen
```

`scripts/smoke-image.sh` prüft ein gebautes Image so, wie es die CI tut. Das
Skript startet das Image schreibgeschützt und ohne Schlüssel als Container
`metadata-agent-smoke`, wartet bis zu 60 s auf `/health`, prüft `/ready` auf
Port 8000, `/ready` und `/metrics` auf dem internen Port (9000, im Container)
und dass `tini` als Init-Prozess läuft, lässt Chromium einmal starten und
entfernt den Container. Es braucht `wget` und einen freien Port 8000, den es
während des Laufs auf allen Schnittstellen veröffentlicht.

```bash
sh scripts/smoke-image.sh metadata-agent-api
```

## Docker Compose

```bash
cp .env.template .env           # dann B_API_KEY in .env eintragen
docker compose up -d            # bauen und starten
docker compose logs -f          # Log mitlesen
docker compose up -d --build    # nach einer Code-Änderung neu bauen und starten
docker compose down             # stoppen und entfernen
```

`docker-compose.yml` setzt dieselben Laufzeitoptionen wie das Beispiel mit
`docker run` oben (Auszug; die Datei enthält außerdem die Liste der
Einstellungen und den Health Check):

```yaml
services:
  metadata-agent-api:
    build: .
    container_name: metadata-agent-api
    ports:
      - "127.0.0.1:8000:8000"
    read_only: true
    tmpfs:
      - /tmp
      - /home/appuser
    stop_grace_period: 320s
    env_file:
      - path: .env
        required: false
    restart: unless-stopped
```

- **Compose-Version:** Die Datei braucht Docker Compose 2.24 oder neuer
  (`docker compose version`). Ältere Versionen kennen die Langform von
  `env_file` mit `required` nicht und brechen ab.
- **Einstellungen:** in `.env` eintragen; die Liste in der Datei nicht
  bearbeiten. Compose liest `.env` zweimal: um die Einträge
  `${VAR:-default}` der Liste `environment` zu füllen, und als `env_file` für
  jede Einstellung, die die Liste nicht nennt. Ohne `.env` gelten die
  Standardwerte der Liste. Drei Einträge haben die Form `${VAR-default}`; ein
  leerer Wert in `.env` bleibt bei ihnen leer, statt zum Standardwert zu
  werden: `METADATA_AGENT_LLM_VERBOSITY`,
  `METADATA_AGENT_LLM_REASONING_EFFORT` und `METADATA_AGENT_CORS_ORIGINS`.
- **Health Check:** Er ruft `/health` auf. Der Container bleibt deshalb auch
  dann oben, wenn `/ready` mit `503` antwortet, etwa ohne Repository-Zugang.
- **Interner Port:** in der Datei nicht gesetzt. Um ihn zu nutzen,
  `METADATA_AGENT_INTERNAL_PORT=9000` in `.env` setzen und
  `"127.0.0.1:9000:9000"` unter `ports` ergänzen (siehe
  [Installation prüfen](#installation-prüfen)).

Für den Vergleich von Providern nebeneinander enthält die Datei zwei weitere,
auskommentierte Dienste: AcademicCloud auf Port 8001 und natives OpenAI auf
Port 8002. Zum Starten die Kommentarzeichen entfernen, zum Beispiel:

```yaml
  metadata-agent-api-academiccloud:
    build: .
    container_name: metadata-agent-api-academiccloud
    ports:
      - "127.0.0.1:8001:8000"
    read_only: true
    tmpfs: [/tmp, /home/appuser]
    stop_grace_period: 320s
    environment:
      - METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
      - B_API_KEY=${B_API_KEY:-}
      - METADATA_AGENT_DEFAULT_MAX_WORKERS=10
```

Diese beiden Dienste erhalten nur die für sie aufgeführten Variablen (kein
`env_file`). Ihr `/ready` antwortet mit `503`, bis der Repository-Zugang oder
`METADATA_AGENT_REQUIRE_REPOSITORY=false` ergänzt ist.

## Minimale Konfiguration

Alle Wege lesen dieselben Variablen: ein lokaler Start aus `.env` im
Arbeitsverzeichnis und aus der Umgebung, ein Container aus seinen
Umgebungsvariablen. `.env.template` führt die gängigen mit Kommentaren auf; die
vollständige Referenz ist [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md).

- Secrets tragen kein Präfix (`B_API_KEY`, `OPENAI_API_KEY`,
  `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`). Jede andere Variable beginnt
  mit `METADATA_AGENT_`.
- Eine Umgebungsvariable hat Vorrang vor demselben Eintrag in `.env`. Eine
  leere Variable gilt als nicht gesetzt: Es gilt der Standardwert (außer bei
  den drei Einstellungen, bei denen „leer“ ein Wert ist, siehe
  [Docker Compose](#docker-compose)). Unbekannte Variablen werden ignoriert,
  ein Tippfehler in einem Namen fällt also nicht auf. Ein Wert, den eine
  Einstellung nicht zulässt, verhindert den Start.
- Einen Schlüssel leer lassen, bis ein echter da ist. `/ready` prüft nur, ob
  ein Schlüssel gesetzt ist; mit einem Platzhalter würde sich der Dienst
  bereit melden.

### LLM-Betriebsart

`METADATA_AGENT_LLM_PROVIDER` wählt die Betriebsart. Der Standard braucht nur
den Schlüssel:

```env
METADATA_AGENT_LLM_PROVIDER=b-api-openai
B_API_KEY=<key>
```

| Betriebsart | `METADATA_AGENT_LLM_PROVIDER` | Schlüssel | Standardmodell | Zusätzlich setzen |
|---|---|---|---|---|
| B-API, OpenAI-Modelle (Standard) | `b-api-openai` | `B_API_KEY` | `gpt-6-luna` | — |
| B-API, AcademicCloud-Modelle | `b-api-academiccloud` | `B_API_KEY` | `deepseek-v4-flash` | — |
| B-API-Router | `b-api-router` | `B_API_KEY` | `openai/gpt-6-luna` | `METADATA_AGENT_B_API_ROUTER_MODEL`: eine in der B-API definierte Route oder `provider/modell` |
| OpenAI direkt oder ein kompatibler Endpunkt | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | Für einen anderen Endpunkt: `METADATA_AGENT_OPENAI_API_BASE` (Standard `https://api.openai.com/v1`) und `METADATA_AGENT_OPENAI_MODEL` |

- **Rechte des B-API-Schlüssels:** die LLM-Endpunkte und die Proxy-Dienste
  `text-extraction` und `photon_geocoding`. Mit einem B-API-Provider ruft der
  Agent Textextraktion und Geocoder über die B-API auf, mit demselben
  Schlüssel (`METADATA_AGENT_B_API_SERVICES`, Standard `auto`). Ohne diese
  Rechte scheitert die Erschließung aus einer URL mit
  `TEXT_EXTRACTION_AUTH_REFUSED`, und Orte bleiben ohne Koordinaten.
- **Welcher Schlüssel die Aufrufe trägt:** `METADATA_AGENT_B_API_AUTH_MODE`,
  Standard `auto`. Ein Schlüssel, den der B-API-Proxy durchreicht
  (`Authorization: Bearer`), wird verwendet; sonst `B_API_KEY`. Eine
  Installation, die direkt und nicht über den Proxy aufgerufen wird, braucht
  deshalb `B_API_KEY`. `forward` nimmt nur durchgereichte Schlüssel an (ein
  Direktaufruf antwortet mit `502` und `LLM_AUTH_MISSING`); `static` verwendet
  nur `B_API_KEY`. Einzelheiten: [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md)
  und [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md).
- **AcademicCloud:** Die Standardwerte erlauben 2 gleichzeitige Anfragen und
  2 je Sekunde. Das Kontingent des Gateways ist enger; vor dem echten Einsatz
  [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md) lesen.
- **Router:** Bei einem Routennamen (einem Wert ohne `/`) zusätzlich
  `METADATA_AGENT_LLM_REQUEST_STYLE` auf `reasoning` oder `classic` setzen.
  Sonst warnt der Agent beim Start und sendet `classic`.
- **Provider `openai`:** Es funktioniert jeder Endpunkt, der
  `/chat/completions` bedient und einen Bearer-Token annimmt. Die
  Textextraktion folgt dann der Repository-Adresse (OpenEduHub Staging oder
  Produktion), und der Geocoder ist `photon.komoot.io`. Bei einem anderen
  Repository `METADATA_AGENT_TEXT_EXTRACTION_API_URL` setzen; sonst antwortet
  die Erschließung aus einer URL mit `503`.

### Repository-Zugang und `/ready`

| Variable | Wirkung |
|---|---|
| `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` | Der Service-Account für Lesen und Schreiben im Repository. Ohne ihn antworten `/upload` und `/nodes` mit `503`, ebenso `/ready` |
| `METADATA_AGENT_REQUIRE_REPOSITORY=false` | Für eine Installation, die nur Metadaten erzeugt: `/ready` antwortet ohne den Account mit `200`; `/upload` und `/nodes` antworten weiter mit `503` |

`/generate` aus einem Text funktioniert in beiden Fällen.

### Staging oder Produktion

Alle Adressen sind auf Staging voreingestellt. Für die Produktion beide setzen:

```env
METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
```

Beim Start warnt der Agent, wenn die Adressen auf verschiedene Umgebungen
zeigen. Der Request-Parameter `repository` wird ignoriert; nur die Variable
bestimmt das Repository.

## Installation prüfen

**1. Das Startlog lesen.** Der Agent gibt Version, Provider, Modell und die
Adressen von B-API, Textextraktion, Geocoder und Repository aus, danach eine
Zeile für jedes Konfigurationsproblem, das er erkennt (ein fehlender
Schlüssel, ein fehlender Repository-Zugang, Adressen aus verschiedenen
Umgebungen). Anschließend schreibt er eine Zeile je Anfrage; `/health` und
`/ready` erscheinen nur mit `METADATA_AGENT_LOG_LEVEL=DEBUG`.

**2. Die Endpunkte aufrufen.**

```bash
# Der Prozess antwortet
curl http://localhost:8000/health

# Die Konfiguration ist vollständig
curl -i http://localhost:8000/ready

# Eine erste Erschließung (braucht einen funktionierenden LLM-Schlüssel)
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Workshop KI in der Bildung am 15. März 2026 in Berlin"}'

# Ein Screenshot (braucht Chromium)
curl -X POST http://localhost:8000/screenshot \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

| Prüfung | Erwartet | Sonst |
|---|---|---|
| `GET /health` | `{"status":"healthy","version":"4.0.1"}` | Der Prozess läuft nicht oder lauscht woanders. `/health` sagt nichts über Schlüssel oder abhängige Dienste |
| `GET /ready` | `200` mit `{"status":"ready"}` | `503` mit `error.code` `NOT_READY`: Die Konfiguration ist unvollständig. Die Gründe stehen im Log (`Nicht bereit: …`) und auf dem internen Port, nicht in dieser Antwort. Siehe [Repository-Zugang und `/ready`](#repository-zugang-und-ready) |
| `/docs` im Browser | Swagger UI, vom Agenten selbst ausgeliefert, ohne CDN; die Spezifikation liegt unter `/openapi.json` | — |
| `POST /generate` | JSON mit `contextName`, `schemaVersion` und `metadataset`, den erschlossenen Feldern unter ihren Feld-IDs und `processing` mit `status` (`success` oder `partial`), `llm_provider` und `llm_model` | `502` mit einem `error.code` wie `LLM_AUTH_MISSING`: siehe [Fehlersuche](#fehlersuche). Ein Lauf macht viele LLM-Aufrufe (etwa einen je Feld) und endet spätestens nach 300 s (`METADATA_AGENT_GENERATE_TIMEOUT`) |
| `POST /screenshot` | `"success": true` und das Bild in `image_base64` | `502`: Chromium fehlt oder die Seite wurde nicht geladen. `400`: Die Adresse ist nicht öffentlich |
| `/widget/examples/default.html` im Browser | Eine Beispielseite mit der Webkomponente | — |

**3. Optional: der interne Port.** Mit `METADATA_AGENT_INTERNAL_PORT` liefert
der Agent `/health`, `/ready` und `/metrics` auf einem zweiten Port. Dort nennt
`/health` zusätzlich Provider und Auth-Modus, und `/ready` führt seine Gründe
unter `problems` auf.

```env
METADATA_AGENT_INTERNAL_PORT=9000
METADATA_AGENT_INTERNAL_HOST=127.0.0.1
```

```bash
curl http://localhost:9000/ready
curl http://localhost:9000/metrics
```

Der interne Port lauscht standardmäßig auf `0.0.0.0`, unabhängig von `--host`;
die zweite Zeile beschränkt ihn bei einem lokalen Start auf diesen Rechner. In
einem Container `METADATA_AGENT_INTERNAL_HOST` nicht setzen und den Port nur
für diesen Rechner veröffentlichen (`-p 127.0.0.1:9000:9000`). Das Monitoring
beschreibt [DEPLOYMENT.de.md](DEPLOYMENT.de.md).

**4. Optional: die abhängigen Dienste prüfen.** Mit
`METADATA_AGENT_DEBUG_ENDPOINTS=true` meldet `GET /debug/dependencies`, ob
B-API, Textextraktion, Geocoder und Repository erreichbar sind und ob die
B-API den Schlüssel annimmt. Danach wieder ausschalten
([ENV-PARAMETER.de.md](ENV-PARAMETER.de.md)).

## Tests ausführen

```bash
uv sync                               # installiert auch die Testwerkzeuge
uv run playwright install chromium    # für die Browser-Tests
uv run pytest -q -rs                  # ganze Suite; -rs nennt übersprungene Tests mit Grund
uv run pytest -q -rs --cov            # wie in der CI: mit der Coverage-Untergrenze aus pyproject.toml
```

Die Suite läuft offline und braucht keine Schlüssel. `tests/conftest.py`
entfernt die `METADATA_AGENT_*`-Variablen und die Schlüssel aus der Umgebung,
ignoriert `.env` und weist jede Verbindung ab, die nicht zu diesem Rechner
geht.

| Variable | Wirkung |
|---|---|
| `REQUIRE_BROWSER=1` | Ohne Chromium oder das Widget-Bundle schlagen die Browser-Tests fehl, statt übersprungen zu werden (in beiden CI-Pipelines gesetzt) |
| `REQUIRE_HELM=1` | Dasselbe für die Tests, die den Helm-Chart rendern; sie brauchen `helm` im `PATH` |
| `RUN_NETWORK_TESTS=1` | Hebt die Offline-Sperre auf und führt die mit `slow` markierten Tests gegen echte Dienste aus |

Lint und Typprüfung wie in der CI: `uv run ruff check`,
`uv run ruff format --check` und `uv run mypy`.

Beide CI-Pipelines (`.gitlab-ci.yml`, `.github/workflows/docker-publish.yml`)
führen diese Befehle aus, bauen danach das Image und prüfen es mit
`scripts/smoke-image.sh` und einem Schwachstellen-Scan. Ein Lauf nach Zeitplan
(ein GitLab-Pipeline-Zeitplan, `.github/workflows/scheduled-checks.yml`)
wiederholt die Prüfung der Abhängigkeiten, die Prüfung des Browser-Alters und
den Scan des neuesten Release-Images. Pipeline-Variablen und Veröffentlichung
beschreiben [UEBERGABE.md](UEBERGABE.md) und
[DEPLOYMENT.de.md](DEPLOYMENT.de.md).

## Fehlersuche

| Symptom | Ursache | Abhilfe |
|---|---|---|
| `uvicorn: command not found`, `playwright: command not found` | Die Werkzeuge sind in `.venv` installiert | `uv run` davorsetzen oder die virtuelle Umgebung aktivieren |
| `ModuleNotFoundError: No module named 'src'` | Außerhalb des Wurzelverzeichnisses gestartet | Aus dem Verzeichnis starten, das `src/` enthält |
| Der Start bricht mit einem Validierungsfehler ab, der eine Einstellung nennt | Eine Variable hat einen Wert außerhalb der erlaubten Werte oder Grenzen, zum Beispiel ein unbekannter `METADATA_AGENT_LLM_PROVIDER` oder `METADATA_AGENT_LLM_VERBOSITY`, eine Temperatur über 2 oder `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=0` | Wert korrigieren; die erlaubten Werte und Grenzen stehen in [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md) |
| Log: `Interner Port … ist belegt oder nicht verfügbar` | Der interne Port ist belegt, es läuft mehr als ein Worker, oder `METADATA_AGENT_INTERNAL_HOST` nennt eine Adresse, die der Rechner nicht hat (`::` ohne IPv6) | Port freigeben, einen Worker betreiben, Adresse korrigieren. Die API läuft ohne internen Port weiter |
| Eine Anfrage mit abschließendem Schrägstrich (`/generate/`, `/docs/`) antwortet mit `404` | Der Agent leitet keinen Pfad weiter | Den Pfad ohne den Schrägstrich aufrufen |
| `/ready` antwortet mit `503` (`NOT_READY`) | Der Repository-Zugang fehlt; oder `B_API_KEY` im Modus `static`; oder `OPENAI_API_KEY` mit Provider `openai` | Fehlende Werte setzen, oder `METADATA_AGENT_REQUIRE_REPOSITORY=false` für eine Installation, die nur erschließt |
| `/generate` antwortet mit `502` und `LLM_AUTH_MISSING` | Kein Schlüssel für die B-API: `B_API_KEY` ist leer, und es wurde kein Schlüssel durchgereicht | `B_API_KEY` setzen; im Modus `forward` über den B-API-Proxy aufrufen |
| `502` mit `LLM_AUTH_REJECTED` oder `LLM_AUTH_FORBIDDEN` | Die B-API nimmt den Schlüssel nicht an, oder dem Schlüssel fehlt das Recht für den Aufruf | Schlüssel prüfen, und ob `METADATA_AGENT_B_API_BASE_URL` die B-API (Staging oder Produktion) nennt, zu der er gehört |
| Die Erschließung aus einer URL antwortet mit `502` und `TEXT_EXTRACTION_AUTH_REFUSED` | Dem Schlüssel fehlt das Recht für den Proxy-Dienst `text-extraction` | Recht ergänzen lassen oder `METADATA_AGENT_B_API_SERVICES=direct` setzen |
| Die Erschließung aus einer URL antwortet mit `503` und `TEXT_EXTRACTION_NOT_CONFIGURED` | Kein B-API-Provider, und das Repository ist weder OpenEduHub Staging noch Produktion | `METADATA_AGENT_TEXT_EXTRACTION_API_URL` setzen |
| LLM-Aufrufe laufen ins Zeitlimit; `/generate` antwortet mit `504` und `GENERATION_TIMEOUT` | Ein Aufruf dauerte länger als `METADATA_AGENT_REQUEST_TIMEOUT` (90 s) oder der ganze Lauf länger als `METADATA_AGENT_GENERATE_TIMEOUT` (300 s) | `METADATA_AGENT_REQUEST_TIMEOUT` erhöhen; wie die Grenzen zusammenpassen, steht in [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md) |
| CORS-Fehler im Browser | Der aufrufende Origin ist nicht erlaubt | `METADATA_AGENT_CORS_ORIGINS` auf die Origins setzen, kommagetrennt, oder auf `*` (der Standard) |
| `/screenshot` antwortet mit `502`; Log: `Executable doesn't exist` | Chromium ist nicht installiert, oder `playwright` wurde seither aktualisiert | `uv run playwright install chromium`. Im Image liegt Chromium in `/opt/playwright-browsers` (`PLAYWRIGHT_BROWSERS_PATH`); die Variable nicht überschreiben |
| Log: `Host system is missing dependencies to run browsers` | Systembibliotheken für Chromium fehlen (Linux) | `uv run playwright install-deps chromium` |
| Windows: `/screenshot` antwortet nach einem Start mit `--reload` mit `500` | Mit `--reload` verwendet uvicorn unter Windows eine Event-Loop, die den Browser-Prozess nicht starten kann | Ohne `--reload` starten |
| Der Screenshot ist leer oder weiß | Die Seite braucht länger zum Laden | `delay` im Request erhöhen (Millisekunden, bis 10000) oder `METADATA_AGENT_SCREENSHOT_DELAY` |
| Ein Screenshot oder eine URL-Eingabe antwortet mit `400` | Erlaubt sind nur öffentliche http(s)-Ziele. Die Prüfung löst DNS dort auf, wo der Dienst läuft; ein Hostname, der dort auf eine private Adresse zeigt (Split-Horizon-DNS), wird ebenfalls abgelehnt | Eine öffentlich erreichbare Adresse verwenden |
| Chromium stürzt im Container ab | Zu wenig Arbeitsspeicher | Speicherlimit des Containers erhöhen. `--shm-size` hilft nicht: Playwright startet Chromium mit `--disable-dev-shm-usage` |
| `docker compose` bricht bei `env_file` ab | Docker Compose älter als 2.24 | Docker Compose aktualisieren |
| `docker build` scheitert bei `playwright install --only-shell chromium` oder bei einem Paket-Download | Der Build hat keinen Netzwerkzugriff | Netzwerk und Proxy des Builds prüfen; `docker build --network host` kann helfen |
| Der Container läuft, aber ein anderer Rechner erreicht den Port nicht | Der Port ist absichtlich nur auf `127.0.0.1` veröffentlicht | Einen Proxy mit Zugangsbeschränkung davorsetzen ([DEPLOYMENT.de.md](DEPLOYMENT.de.md)). Lokal die Zuordnung mit `docker ps` prüfen |
| Der Container startet immer wieder neu | Der Start scheitert | Grund nachlesen: `docker logs metadata-agent-api` |
| `Read-only file system` oder `Permission denied` im Container | Das Wurzeldateisystem ist schreibgeschützt und `/app` gehört root; beschreibbar sind nur `/tmp` und `/home/appuser` | Mit beiden `--tmpfs`-Optionen starten; nur in diese beiden Verzeichnisse schreiben |
| `docker stop` dauert Minuten | Docker wartet bis zu 320 s auf ein laufendes `/generate` | Beabsichtigt; ein Container ohne laufende Anfrage stoppt sofort |
| Windows: Browser-Tests scheitern mit `EPERM … playwright-artifacts` | Chromium kann nicht in das temporäre Verzeichnis schreiben | In PowerShell `TEMP` und `TMP` auf ein Verzeichnis im Arbeitsbereich setzen. In Git Bash `TEMP` nicht ändern: MSYS schreibt den Pfad um, und pytest legt dann `pytest-of-*/` im Repository an |

## Weitere Dokumente

| Dokument | Inhalt |
|---|---|
| [README.de.md](README.de.md) | Überblick über den Dienst |
| [DEPLOYMENT.de.md](DEPLOYMENT.de.md) | Produktivbetrieb: Kubernetes, Proxys, Skalierung, Updates, Rollback, Monitoring |
| [ENV-PARAMETER.de.md](ENV-PARAMETER.de.md) | Jede Umgebungsvariable mit Standardwert und Wirkung |
| [docs/API.de.md](docs/API.de.md) | Endpunkte, Request- und Antwortformate |
| [helm/metadata-agent/README.md](helm/metadata-agent/README.md) | Helm-Chart für Kubernetes |
| [UEBERGABE.md](UEBERGABE.md) | Übergabe an den Betrieb: CI, Ports, Metriken, Alarmregeln |
| [docs/steckbrief.md](docs/steckbrief.md) | Steckbrief des Dienstes: Status, offene Punkte |
| [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md) | B-API-Routing und -Authentifizierung in Kürze |
| [CHANGELOG.md](CHANGELOG.md) | Änderungen je Version, auch die unveröffentlichten |
