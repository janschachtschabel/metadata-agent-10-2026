# Metadata Agent API — Installation

**English** · [Deutsch](INSTALL.de.md)

How to get the Metadata Agent API (version 4.0.2) running and how to check that
it works: locally with uv or pip, in Docker, and with Docker Compose.

Production topics — Kubernetes and Helm, proxies, scaling, updates, rollback,
monitoring — are in [DEPLOYMENT.md](DEPLOYMENT.md). Every setting is listed in
[ENV-PARAMETER.md](ENV-PARAMETER.md), the endpoints in
[docs/API.md](docs/API.md), an overview of the service in
[README.md](README.md).

The examples use a POSIX shell (Linux, macOS, WSL, Git Bash).

## Contents

- [Prerequisites](#prerequisites)
- [Run locally with uv](#run-locally-with-uv)
- [Run locally with pip](#run-locally-with-pip)
- [Screenshot browser](#screenshot-browser)
- [Docker](#docker)
- [Docker Compose](#docker-compose)
- [Minimal configuration](#minimal-configuration)
- [Verify the installation](#verify-the-installation)
- [Run the tests](#run-the-tests)
- [Troubleshooting](#troubleshooting)
- [Further documents](#further-documents)

## Prerequisites

Choose one path:

| Path | Needs | Suited for |
|---|---|---|
| [Local with uv](#run-locally-with-uv) | Python 3.13 or newer, uv 0.7.13 or newer, Git | Development, running the tests |
| [Local with pip](#run-locally-with-pip) | Python 3.13 or newer, pip, Git | Hosts without uv |
| [Docker](#docker) | Docker 24 or newer | A container that already contains Chromium |
| [Docker Compose](#docker-compose) | Docker Compose 2.24 or newer | The same container with the runtime options preset |

uv 0.7.13 is the version that wrote `uv.lock`; the `Dockerfile` and both CI
pipelines pin it. Local runs work on Linux, macOS and Windows (on Windows
without `uvloop`). The container image is a Linux image.

Every path also needs:

- **A key for the LLM calls:** `B_API_KEY` for the B-API of OpenEduHub (the
  default; the WLO team issues the keys) or `OPENAI_API_KEY` for OpenAI or any
  OpenAI-compatible endpoint. See [Minimal configuration](#minimal-configuration).
- **Outgoing HTTPS** to the services the configuration names:

| Target | Default | Used for |
|---|---|---|
| B-API | `https://b-api.staging.openeduhub.net` | LLM calls; text extraction for URL input and the geocoder through its proxy paths |
| Repository (edu-sharing) | `https://repository.staging.openeduhub.net/edu-sharing/rest` | Reading nodes, `/upload`, `/nodes` |
| Only with provider `openai` | `https://api.openai.com/v1`, `https://text-extraction.staging.openeduhub.net`, `https://photon.komoot.io/api` | LLM calls, text extraction, geocoder — called directly instead of through the B-API |
| Public web pages | — | Screenshots, taken by the local Chromium |
| During installation | PyPI and the Playwright download server; for `docker build` also Docker Hub, `ghcr.io` and the Debian package mirrors | Python packages, Chromium, base images, system libraries |

Nobody measured the resource needs of a local installation. As a guide: the
Helm chart requests 500m CPU and 512Mi memory and limits the container to
2 CPUs and 2Gi — estimates from `helm/metadata-agent/values.yaml`.

## Run locally with uv

```bash
# Get the code
git clone <repo-url> metadata-agent
cd metadata-agent

# Create .venv and install the versions pinned in uv.lock
uv sync

# Chromium for screenshots (optional, see "Screenshot browser")
uv run playwright install chromium

# Configuration: copy the template, then enter B_API_KEY in .env
cp .env.template .env

# Start with auto-reload, reachable from this machine only
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

Then continue with [Verify the installation](#verify-the-installation).

- **Dependencies:** `uv sync` installs the runtime packages and the development
  tools (pytest, ruff, mypy). `uv sync --no-dev` leaves the tools out.
  `uv sync --locked` also fails when `uv.lock` and `pyproject.toml` do not
  match.
- **Working directory:** start from the repository root. The app is addressed
  as `src.main:app` and is not installed as a package, and the service reads
  `.env` from the working directory.
- **`--reload`** restarts the server when a source file changes. Leave it out
  otherwise. On Windows, screenshots need a start without `--reload` (see
  [Troubleshooting](#troubleshooting)).
- **Other machines:** `--host 0.0.0.0` opens the port on every interface. The
  API checks no credentials itself: whoever reaches the port runs extractions
  on your `B_API_KEY`. Only do this behind an access restriction
  ([DEPLOYMENT.md](DEPLOYMENT.md)).
- **One worker per process:** do not use `--workers`. With
  `METADATA_AGENT_INTERNAL_PORT` every worker would claim the internal port,
  and metrics count per process. Scale with more instances
  ([DEPLOYMENT.md](DEPLOYMENT.md)).

## Run locally with pip

`requirements.txt` is the export of `uv.lock`: the same exact versions, each
with hashes, runtime packages only.

```bash
# Virtual environment with Python 3.13 or newer
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install exactly the locked versions; pip checks every hash
python -m pip install --require-hashes -r requirements.txt

# Chromium for screenshots (optional, see "Screenshot browser")
playwright install chromium

# Configuration: copy the template, then enter B_API_KEY in .env
cp .env.template .env

# Start, reachable from this machine only
uvicorn src.main:app --host 127.0.0.1 --port 8000
```

The notes of the uv path on working directory, host and workers apply here as
well. The test tools are not part of `requirements.txt`; use the uv path to
[run the tests](#run-the-tests).

After every change of `uv.lock`, regenerate the file with the command in its
header (`tests/test_requirements_in_step.py` checks that both agree):

```bash
uv export --frozen --no-dev --format requirements-txt --no-emit-project -o requirements.txt
```

### Installed packages

Both paths install the same versions; `uv.lock` names them. The minimum
versions come from `pyproject.toml`:

| Package | Minimum | Purpose |
|---|---|---|
| fastapi | 0.142.4 | Web framework |
| uvicorn | 0.32.0 | ASGI server, without the extra `standard` |
| httptools | 0.6.3 | Fast HTTP parser for uvicorn |
| uvloop | 0.21.0 | Fast event loop for uvicorn; not installed on Windows |
| pydantic | 2.10.0 | Data validation |
| pydantic-settings | 2.14.2 | Settings from environment variables and `.env` |
| httpx | 0.28.0 | HTTP client, also for every LLM call (there is no OpenAI SDK) |
| playwright | 1.63.0 | Drives Chromium for screenshots and fixes its version |
| prometheus-client | 0.26.0 | Metrics (`/metrics` on the internal port) |
| starlette | 1.3.1 | Middlewares and responses, imported directly |

Three packages that arrive only indirectly have minimum versions because of
known vulnerabilities in older ones: anyio 4.14.2, idna 3.15 and python-dotenv
1.2.2 (`[tool.uv] constraint-dependencies` in `pyproject.toml`). uv applies
them when it writes the lock; pip gets them through the pinned versions in
`requirements.txt`.

## Screenshot browser

Screenshots (`POST /screenshot`, preview images of nodes) are taken by a local
headless Chromium that Playwright drives. No data goes to an external
screenshot service; the method `pageshot` is a legacy alias for the same local
capture. Without Chromium everything else works, and screenshot requests
answer `502`.

| Environment | What to do |
|---|---|
| Docker image, Docker Compose | Nothing: Chromium and its system libraries are part of the image |
| Windows, macOS | `uv run playwright install chromium` (pip path: without `uv run`) |
| Linux | The same, plus the system libraries: `uv run playwright install-deps chromium` (needs root rights) |

On Debian 13 you can install the system libraries by hand instead. This is the
list the image uses (`Dockerfile`); other distributions name some packages
differently, so use `playwright install-deps` there.

```bash
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libx11-xcb1 fonts-liberation fonts-noto-color-emoji
```

Check that the browser starts (no page is loaded):

```bash
uv run python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(headless=True); print('Chromium', b.version); b.close(); p.stop()"
```

- The `playwright` package fixes the Chromium version. After an update of
  `playwright`, run `playwright install chromium` again.
- `uv run python scripts/check_browser_age.py` prints the bundled Chromium
  version and exits with 1 when it is more than two major versions behind the
  stable Chrome. It asks Google's version service; without a connection it
  only prints the version. With `REQUIRE_BROWSER_AGE=1` (set in both CI
  pipelines) it then takes the upload date of the locked `playwright` from
  `uv.lock` instead and fails when that is more than 90 days ago or cannot be
  read.
- The settings `METADATA_AGENT_SCREENSHOT_*` are described in
  [ENV-PARAMETER.md](ENV-PARAMETER.md).

## Docker

The image contains the service, the Webkomponente and Chromium.

### Build and run

```bash
# Build the image
docker build -t metadata-agent-api .

# Start with the settings from .env
docker run -d \
  --name metadata-agent-api \
  -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp --tmpfs /home/appuser \
  --stop-timeout 320 \
  --env-file .env \
  --restart unless-stopped \
  metadata-agent-api
```

Without a `.env` file, pass single settings with `-e`:

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

| Option | Why |
|---|---|
| `-p 127.0.0.1:8000:8000` | Reachable from this machine only. The API checks no credentials itself: whoever reaches the port runs extractions on `B_API_KEY`. To serve other hosts, put a proxy with an access restriction in front ([DEPLOYMENT.md](DEPLOYMENT.md)) |
| `--read-only --tmpfs /tmp --tmpfs /home/appuser` | Read-only root filesystem, as in the Helm chart. Chromium writes its profile and caches to these two directories |
| `--stop-timeout 320` | A running `/generate` may take up to 300 s (`METADATA_AGENT_GENERATE_TIMEOUT`). Docker's default of 10 s would cut it off on every stop or restart |
| `--env-file .env` | Passes the settings as environment variables; the image contains no `.env`. Docker takes each line literally: write values without quotes |
| `--restart unless-stopped` | Restarts the container after a crash or a reboot |

Build variants:

| Command | Effect |
|---|---|
| `docker build --build-arg VERSION=v4.0.2 --build-arg REVISION=$(git rev-parse HEAD) -t metadata-agent-api:v4.0.2 .` | Writes version and commit into the image labels (defaults: `dev`, `unknown`) |
| `docker build --no-cache -t metadata-agent-api .` | Rebuilds every layer |
| `docker build --progress=plain -t metadata-agent-api .` | Shows the full build output |

The CI pipelines publish images to the internal registry (tags `main`,
`develop` and release tags such as `v4.0.2`). Pulling and running them is
described in [DEPLOYMENT.md](DEPLOYMENT.md).

### What the image contains

| Property | Value |
|---|---|
| Base | `python:3.13-slim` (Debian 13 "trixie"), pinned by digest |
| Build | Two stages: the builder installs the packages with uv 0.7.13 (`uv sync --locked --no-install-project --no-dev`); the final image receives only the finished `.venv`, no build tools |
| Contents | The packages from `uv.lock`, Chromium's headless shell in `/opt/playwright-browsers` (`playwright install --only-shell chromium`) with 20 system packages, `tini` as init process, `src/` including the Webkomponente (`src/static/widget/`), `LICENSE` and `NOTICE` |
| User | `appuser` (UID 1000), not root. `/app` belongs to root: the process can read its code but not change it |
| Port | 8000 |
| Start command | `tini -- uvicorn src.main:app --port 8000 --timeout-keep-alive 75` (one worker). uvicorn listens on the address in `UVICORN_HOST`, which the image sets to `0.0.0.0`; idle connections stay open for 75 s |
| Health check | Built in: `/health` every 30 s, timeout 10 s, 3 retries, start period 5 s |
| State | None; no volume is needed |
| Labels | `org.opencontainers.image.version` and `.revision` from the build arguments `VERSION` and `REVISION` |

`.dockerignore` keeps `.git`, `.env` and `.env.*` (except `.env.template`),
`*.md`, `.venv`, caches and logs out of the build context.

### Manage the container

```bash
docker logs -f metadata-agent-api                                       # follow the log
docker inspect --format='{{.State.Health.Status}}' metadata-agent-api   # result of the health check
docker stats metadata-agent-api                                         # CPU and memory
docker exec -it metadata-agent-api bash                                 # shell in the container
docker stop metadata-agent-api && docker rm metadata-agent-api          # stop and remove
```

`scripts/smoke-image.sh` checks a built image the way the CI does. It starts
the image read-only and without a key as container `metadata-agent-smoke`,
waits up to 60 s for `/health`, checks `/ready` on port 8000, `/ready` and
`/metrics` on the internal port (9000, inside the container) and that `tini`
runs as init process, lets Chromium start once and removes the container. It
needs `wget` and a free port 8000, which it publishes on every interface while
it runs.

```bash
sh scripts/smoke-image.sh metadata-agent-api
```

## Docker Compose

```bash
cp .env.template .env           # then enter B_API_KEY in .env
docker compose up -d            # build and start
docker compose logs -f          # follow the log
docker compose up -d --build    # rebuild and restart after a code change
docker compose down             # stop and remove
```

`docker-compose.yml` sets the same runtime options as the `docker run` example
above (excerpt; the file also holds the list of settings and the health check):

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

- **Compose version:** the file needs Docker Compose 2.24 or newer
  (`docker compose version`). Older versions do not know the long form of
  `env_file` with `required` and abort.
- **Settings:** put them in `.env`; do not edit the list in the file. Compose
  reads `.env` twice: to fill the `${VAR:-default}` entries of the
  `environment` list, and as `env_file` for every setting the list does not
  name. Without a `.env` the defaults of the list apply. Three entries have
  the form `${VAR-default}`, so an empty value in `.env` stays empty instead
  of becoming the default: `METADATA_AGENT_LLM_VERBOSITY`,
  `METADATA_AGENT_LLM_REASONING_EFFORT` and `METADATA_AGENT_CORS_ORIGINS`.
- **Health check:** it calls `/health`. The container therefore stays up when
  `/ready` answers `503`, for example without the repository account.
- **Internal port:** not set in the file. To use it, set
  `METADATA_AGENT_INTERNAL_PORT=9000` in `.env` and add
  `"127.0.0.1:9000:9000"` to `ports` (see
  [Verify the installation](#verify-the-installation)).

To compare providers side by side, the file holds two more services, commented
out: AcademicCloud on port 8001 and native OpenAI on port 8002. Remove the
comment signs to start them, for example:

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

These two services receive only the variables listed for them (no `env_file`).
Their `/ready` answers `503` until you add the repository account or
`METADATA_AGENT_REQUIRE_REPOSITORY=false`.

## Minimal configuration

All paths read the same variables: a local start from `.env` in the working
directory and from the environment, a container from its environment
variables. `.env.template` lists the common ones with comments; the full
reference is [ENV-PARAMETER.md](ENV-PARAMETER.md).

- Secrets carry no prefix (`B_API_KEY`, `OPENAI_API_KEY`,
  `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`). Every other variable starts
  with `METADATA_AGENT_`.
- An environment variable wins over the same entry in `.env`. An empty
  variable counts as not set: the default applies (except for the three
  settings where empty is a value, see [Docker Compose](#docker-compose)).
  Unknown variables are ignored, so a typo in a name goes unnoticed. A value
  a setting does not allow stops the start.
- Leave a key empty until you have a real one. `/ready` only checks that a key
  is set; a placeholder would make the service report ready.

### LLM mode

`METADATA_AGENT_LLM_PROVIDER` selects the mode. The default needs nothing but
the key:

```env
METADATA_AGENT_LLM_PROVIDER=b-api-openai
B_API_KEY=<key>
```

| Mode | `METADATA_AGENT_LLM_PROVIDER` | Key | Default model | Also set |
|---|---|---|---|---|
| B-API, OpenAI models (default) | `b-api-openai` | `B_API_KEY` | `gpt-6-luna` | — |
| B-API, AcademicCloud models | `b-api-academiccloud` | `B_API_KEY` | `deepseek-v4-flash` | — |
| B-API router | `b-api-router` | `B_API_KEY` | `openai/gpt-6-luna` | `METADATA_AGENT_B_API_ROUTER_MODEL`: a route defined in the B-API, or `provider/model` |
| OpenAI directly, or a compatible endpoint | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | For another endpoint: `METADATA_AGENT_OPENAI_API_BASE` (default `https://api.openai.com/v1`) and `METADATA_AGENT_OPENAI_MODEL` |

- **Rights of the B-API key:** the LLM endpoints and the proxy services
  `text-extraction` and `photon_geocoding`. With a B-API provider the agent
  calls text extraction and geocoder through the B-API, with the same key
  (`METADATA_AGENT_B_API_SERVICES`, default `auto`). Without these rights
  extraction from a URL fails with `TEXT_EXTRACTION_AUTH_REFUSED`; the
  geocoder is then asked at Photon itself with `auto`, with `proxy` places stay
  without coordinates.
- **Which key carries the calls:** `METADATA_AGENT_B_API_AUTH_MODE`, default
  `auto`. A key that the B-API proxy forwards (`Authorization: Bearer`) is
  used; otherwise `B_API_KEY`. An installation that is called directly, not
  through the proxy, therefore needs `B_API_KEY`. `forward` accepts only
  forwarded keys (a direct call answers `502` with `LLM_AUTH_MISSING`);
  `static` uses only `B_API_KEY`. Details: [ENV-PARAMETER.md](ENV-PARAMETER.md)
  and [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md) (German).
- **AcademicCloud:** the defaults allow 2 requests in flight and 2 per second.
  The quota of the gateway is tighter; see
  [ENV-PARAMETER.md](ENV-PARAMETER.md) before real use.
- **Router:** with a route name (a value without `/`), also set
  `METADATA_AGENT_LLM_REQUEST_STYLE` to `reasoning` or `classic`. Otherwise the
  agent warns at start and sends `classic`.
- **Provider `openai`:** any endpoint works that serves `/chat/completions`
  and accepts a Bearer token. Text extraction then follows the repository
  address (OpenEduHub staging or production), and the geocoder is
  `photon.komoot.io`. With another repository, set
  `METADATA_AGENT_TEXT_EXTRACTION_API_URL`; otherwise extraction from a URL
  answers `503`.

### Repository account and `/ready`

| Variable | Effect |
|---|---|
| `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` | The service account for reading and writing in the repository. Without it `/upload` and `/nodes` answer `503`, and so does `/ready` |
| `METADATA_AGENT_REQUIRE_REPOSITORY=false` | For an installation that only generates metadata: `/ready` answers `200` without the account; `/upload` and `/nodes` still answer `503` |

`/generate` from a text works in both cases.

### Staging or production

All addresses default to staging. For production set both:

```env
METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
```

At start the agent warns when the addresses point to different environments.
The request parameter `repository` is ignored; only the variable selects the
repository.

## Verify the installation

**1. Read the start log.** The agent prints version, provider, model and the
addresses of B-API, text extraction, geocoder and repository, then one line
for every configuration problem it sees (a missing key, a missing repository
account, addresses from different environments). Afterwards it writes one line
per request; `/health` and `/ready` appear only with
`METADATA_AGENT_LOG_LEVEL=DEBUG`.

**2. Call the endpoints.**

```bash
# The process answers
curl http://localhost:8000/health

# The configuration is complete
curl -i http://localhost:8000/ready

# A first extraction (needs a working LLM key)
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Workshop KI in der Bildung am 15. März 2026 in Berlin"}'

# A screenshot (needs Chromium)
curl -X POST http://localhost:8000/screenshot \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

| Check | Expected | Otherwise |
|---|---|---|
| `GET /health` | `{"status":"healthy","version":"4.0.2"}` | The process is not running or listens elsewhere. `/health` says nothing about keys or dependencies |
| `GET /ready` | `200` with `{"status":"ready"}` | `503` with `error.code` `NOT_READY`: the configuration is incomplete. The reasons are in the log (`Nicht bereit: …`) and on the internal port, not in this answer. See [Repository account and `/ready`](#repository-account-and-ready) |
| `/docs` in a browser | Swagger UI, served by the agent itself without a CDN; the specification is at `/openapi.json` | — |
| `POST /generate` | JSON with `contextName`, `schemaVersion` and `metadataset`, the extracted fields under their field ids, and `processing` with `status` (`success` or `partial`), `llm_provider` and `llm_model` | `502` with an `error.code` such as `LLM_AUTH_MISSING`: see [Troubleshooting](#troubleshooting). A run makes many LLM calls (about one per field) and ends after 300 s at the latest (`METADATA_AGENT_GENERATE_TIMEOUT`) |
| `POST /screenshot` | `"success": true` and the image in `image_base64` | `502`: Chromium is missing or the page did not load. `400`: the address is not public |
| `/widget/examples/default.html` in a browser | An example page with the Webkomponente | — |

**3. Optional: the internal port.** With `METADATA_AGENT_INTERNAL_PORT` the
agent serves `/health`, `/ready` and `/metrics` on a second port. There
`/health` also names provider and auth mode, and `/ready` lists its reasons
under `problems`.

```env
METADATA_AGENT_INTERNAL_PORT=9000
METADATA_AGENT_INTERNAL_HOST=127.0.0.1
```

```bash
curl http://localhost:9000/ready
curl http://localhost:9000/metrics
```

The internal port listens on `0.0.0.0` by default, independent of `--host`;
the second line limits it to this machine for a local start. In a container
leave `METADATA_AGENT_INTERNAL_HOST` unset and publish the port for this
machine only (`-p 127.0.0.1:9000:9000`). Monitoring is described in
[DEPLOYMENT.md](DEPLOYMENT.md).

**4. Optional: check the dependencies.** With
`METADATA_AGENT_DEBUG_ENDPOINTS=true`, `GET /debug/dependencies` reports
whether B-API, text extraction, geocoder and repository can be reached and
whether the B-API accepts the key. Switch it off again afterwards
([ENV-PARAMETER.md](ENV-PARAMETER.md)).

## Run the tests

```bash
uv sync                               # also installs the test tools
uv run playwright install chromium    # for the browser tests
uv run pytest -q -rs                  # whole suite; -rs lists skipped tests with their reason
uv run pytest -q -rs --cov            # as in CI: with the coverage floor from pyproject.toml
```

The suite runs offline and needs no keys. `tests/conftest.py` removes the
`METADATA_AGENT_*` variables and the keys from the environment, ignores
`.env`, and refuses every connection that does not go to this machine.

| Variable | Effect |
|---|---|
| `REQUIRE_BROWSER=1` | Without Chromium or the widget bundle the browser tests fail instead of being skipped (set in both CI pipelines) |
| `REQUIRE_HELM=1` | The same for the tests that render the Helm chart; they need `helm` in the `PATH` |
| `RUN_NETWORK_TESTS=1` | Lifts the offline guard and runs the tests marked `slow` against real services |

Lint and type check as in CI: `uv run ruff check`, `uv run ruff format --check`
and `uv run mypy`.

Both CI pipelines (`.gitlab-ci.yml`, `.github/workflows/docker-publish.yml`)
run these commands, then build the image and check it with
`scripts/smoke-image.sh` and a vulnerability scan. A scheduled run (a GitLab
pipeline schedule, `.github/workflows/scheduled-checks.yml`) repeats the
dependency audit, the browser age check and the scan of the newest release
image. Pipeline variables and publishing are described in
[UEBERGABE.md](UEBERGABE.md) (German) and [DEPLOYMENT.md](DEPLOYMENT.md).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `uvicorn: command not found`, `playwright: command not found` | The tools are installed in `.venv` | Put `uv run` in front, or activate the virtual environment |
| `ModuleNotFoundError: No module named 'src'` | Started outside the repository root | Start from the directory that contains `src/` |
| The start aborts with a validation error that names a setting | A variable holds a value outside its allowed set or range, for example an unknown `METADATA_AGENT_LLM_PROVIDER` or `METADATA_AGENT_LLM_VERBOSITY`, a temperature above 2, or `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=0` | Correct the value; allowed values and bounds are in [ENV-PARAMETER.md](ENV-PARAMETER.md) |
| Log: `Interner Port … ist belegt oder nicht verfügbar` | The internal port is taken, more than one worker runs, or `METADATA_AGENT_INTERNAL_HOST` names an address the host does not have (`::` without IPv6) | Free the port, run one worker, correct the address. The API keeps running without the internal port |
| A request with a trailing slash (`/generate/`, `/docs/`) answers `404` | The agent redirects no path | Call the path without the slash |
| `/ready` answers `503` (`NOT_READY`) | The repository account is missing; or `B_API_KEY` in mode `static`; or `OPENAI_API_KEY` with provider `openai` | Set the missing values, or `METADATA_AGENT_REQUIRE_REPOSITORY=false` for an installation that only generates |
| `/generate` answers `502` with `LLM_AUTH_MISSING` | No key for the B-API: `B_API_KEY` is empty and no key was forwarded | Set `B_API_KEY`; in mode `forward` call through the B-API proxy |
| `502` with `LLM_AUTH_REJECTED` or `LLM_AUTH_FORBIDDEN` | The B-API does not accept the key, or the key lacks the right for the call | Check the key, and that `METADATA_AGENT_B_API_BASE_URL` names the B-API (staging or production) it belongs to |
| Extraction from a URL answers `502` with `TEXT_EXTRACTION_AUTH_REFUSED` | The key lacks the right for the proxy service `text-extraction` | Have the right added, or set `METADATA_AGENT_B_API_SERVICES=direct` |
| Extraction from a URL answers `503` with `TEXT_EXTRACTION_NOT_CONFIGURED` | No B-API provider, and the repository is neither OpenEduHub staging nor production | Set `METADATA_AGENT_TEXT_EXTRACTION_API_URL` |
| LLM calls time out; `/generate` answers `504` with `GENERATION_TIMEOUT` | One call took longer than `METADATA_AGENT_REQUEST_TIMEOUT` (90 s), or the whole run longer than `METADATA_AGENT_GENERATE_TIMEOUT` (300 s) | Raise `METADATA_AGENT_REQUEST_TIMEOUT`; how the limits fit together is in [ENV-PARAMETER.md](ENV-PARAMETER.md) |
| CORS error in the browser | The calling origin is not allowed | Set `METADATA_AGENT_CORS_ORIGINS` to the origins, comma-separated, or to `*` (the default) |
| `/screenshot` answers `502`; log: `Executable doesn't exist` | Chromium is not installed, or `playwright` was updated since | `uv run playwright install chromium`. In the image Chromium is in `/opt/playwright-browsers` (`PLAYWRIGHT_BROWSERS_PATH`); do not override the variable |
| Log: `Host system is missing dependencies to run browsers` | System libraries for Chromium are missing (Linux) | `uv run playwright install-deps chromium` |
| Windows: `/screenshot` answers `500` after a start with `--reload` | With `--reload`, uvicorn uses an event loop on Windows that cannot start the browser process | Start without `--reload` |
| The screenshot is empty or white | The page needs longer to load | Raise `delay` in the request (milliseconds, up to 10000) or `METADATA_AGENT_SCREENSHOT_DELAY` |
| A screenshot or a URL input answers `400` | Only public http(s) targets are allowed. The check resolves DNS where the service runs; a host name that resolves to a private address there (split-horizon DNS) is refused as well | Use a publicly reachable address |
| Chromium crashes in the container | Too little memory | Raise the memory limit of the container. `--shm-size` does not help: Playwright starts Chromium with `--disable-dev-shm-usage` |
| `docker compose` aborts at `env_file` | Docker Compose older than 2.24 | Update Docker Compose |
| `docker build` fails at `playwright install --only-shell chromium` or at a package download | The build has no network access | Check network and proxy of the build; `docker build --network host` can help |
| The container runs, but another host cannot reach the port | The port is published on `127.0.0.1` only, on purpose | Put a proxy with an access restriction in front ([DEPLOYMENT.md](DEPLOYMENT.md)). Locally, check the mapping with `docker ps` |
| The container restarts again and again | The start fails | Read the reason: `docker logs metadata-agent-api` |
| `Read-only file system` or `Permission denied` in the container | The root filesystem is read-only and `/app` belongs to root; only `/tmp` and `/home/appuser` are writable | Start with both `--tmpfs` options; write only to these two directories |
| `docker stop` takes minutes | Docker waits up to 320 s for a running `/generate` | Intended; an idle container stops at once |
| Windows: browser tests fail with `EPERM … playwright-artifacts` | Chromium cannot write to the temporary directory | In PowerShell set `TEMP` and `TMP` to a directory in your workspace. Do not change `TEMP` in Git Bash: MSYS rewrites the path, and pytest then creates `pytest-of-*/` in the repository |

## Further documents

| Document | Content |
|---|---|
| [README.md](README.md) | Overview of the service |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Production: Kubernetes, proxies, scaling, updates, rollback, monitoring |
| [ENV-PARAMETER.md](ENV-PARAMETER.md) | Every environment variable with default and effect |
| [docs/API.md](docs/API.md) | Endpoints, request and response formats |
| [helm/metadata-agent/README.md](helm/metadata-agent/README.md) (German) | Helm chart for Kubernetes |
| [UEBERGABE.md](UEBERGABE.md) (German) | Handover to operations: CI, ports, metrics, alerts |
| [docs/steckbrief.md](docs/steckbrief.md) (German) | Service profile: status, open points |
| [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md) (German) | B-API routing and authentication in brief |
| [CHANGELOG.md](CHANGELOG.md) (German) | Changes per version, including the unreleased ones |
