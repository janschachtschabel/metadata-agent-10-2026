# Metadata Agent API — Deployment

**English** · [Deutsch](DEPLOYMENT.de.md)

How to run the Metadata Agent API (service id `metadata-agent`, image
`metadata-agent-api`, version 4.0.4) in production: with Docker or on
Kubernetes, behind the B-API proxy or standalone.

Related documents:

| Document | Content |
|---|---|
| [INSTALL.md](INSTALL.md) | Local start, installation with pip, first run |
| [ENV-PARAMETER.md](ENV-PARAMETER.md) | Every environment variable with its default |
| [docs/API.md](docs/API.md) | Endpoints, requests, responses, error codes |
| [README.md](README.md) | Overview of the service |
| [helm/metadata-agent/README.md](helm/metadata-agent/README.md) (German) | The Helm chart and how to check it |
| [UEBERGABE.md](UEBERGABE.md) (German) | Handover to operations: open decisions, measurements, costs |
| [docs/steckbrief.md](docs/steckbrief.md) (German) | Service profile |
| [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md) (German) | B-API routing and authentication in short |
| [docs/architektur/README.md](docs/architektur/README.md) (German) | Architecture and request flow diagrams for both ways of operation |
| [CHANGELOG.md](CHANGELOG.md) (German) | All changes per version |

## Contents

1. [Ways of operation](#ways-of-operation)
2. [Image](#image)
3. [Docker and Docker Compose](#docker-and-docker-compose)
4. [Kubernetes with the Helm chart](#kubernetes-with-the-helm-chart)
5. [Ports and probes](#ports-and-probes)
6. [Timeouts and request size](#timeouts-and-request-size)
7. [Scaling, throughput and resources](#scaling-throughput-and-resources)
8. [Logging and tracing](#logging-and-tracing)
9. [Outgoing requests: public targets only](#outgoing-requests-public-targets-only)
10. [Checks after a deployment](#checks-after-a-deployment)
11. [First installation in a cluster: checklist](#first-installation-in-a-cluster-checklist)
12. [Updates](#updates)
13. [Rollback](#rollback)

---

## Ways of operation

The agent checks no credentials of its callers. Something in front of it must
restrict access: the B-API proxy, or a proxy of your own.

| | Behind the B-API proxy | Standalone with a B-API key | Standalone with OpenAI |
|---|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` (default), `b-api-academiccloud` or `b-api-router` | as on the left | `openai` |
| Key of the LLM calls | The caller's key, forwarded by the proxy as `Authorization: Bearer <key>`. `B_API_KEY` is only the fallback (`auto`) or not needed (`forward`) | `B_API_KEY` | `OPENAI_API_KEY` |
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` (default) or `forward` | `auto` (default) or `static` | — |
| Text extraction and geocoder (default `auto`) | Through the B-API, with the caller's key | Through the B-API, with `B_API_KEY` | Direct: the text extraction follows the repository URL, the geocoder is Photon |
| Addresses to set | `METADATA_AGENT_B_API_BASE_URL`, `METADATA_AGENT_REPOSITORY_URL` | as on the left | `METADATA_AGENT_REPOSITORY_URL`; `METADATA_AGENT_OPENAI_API_BASE` for another OpenAI-compatible endpoint |
| Repository account | `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` | as on the left | as on the left |
| Who may reach port 8000 | Only the B-API | Only a proxy that restricts access | Only a proxy that restricts access |

The code defaults of `METADATA_AGENT_B_API_BASE_URL` and
`METADATA_AGENT_REPOSITORY_URL` point to staging. The Helm chart sets the
production addresses. With Docker, set both for production:

| Environment | `METADATA_AGENT_B_API_BASE_URL` | `METADATA_AGENT_REPOSITORY_URL` |
|---|---|---|
| Staging (code default) | `https://b-api.staging.openeduhub.net` | `https://repository.staging.openeduhub.net/edu-sharing/rest` |
| Production (chart default) | `https://b-api.prod.openeduhub.net` | `https://redaktion.openeduhub.net/edu-sharing/rest` |

The repository account is needed for `/upload`, `/nodes`, `/workflow`,
`/screenshot` with `node_id` and for sources read from a node. Without it
`/ready` answers `503`. A deployment that only generates metadata sets
`METADATA_AGENT_REQUIRE_REPOSITORY=false`: it is ready without the account,
and `/upload` and `/nodes` answer `503`.

At startup the agent logs the addresses it uses and warns (`⚠️`) about settings
that cannot work together: no key for the chosen mode, a `B_API_KEY` that mode
`forward` never uses, a missing repository account, debug endpoints left on,
deadlines that do not fit into the grace period, an LLM address outside
`METADATA_AGENT_B_API_BASE_URL`, no text extraction, or addresses that point to
different environments (staging and production mixed). A user name or password
inside an address is left out of these lines. Read them after every change of
the configuration.

A setting with a value it does not allow stops the start instead: an unknown
provider, auth mode, verbosity or reasoning effort, a temperature outside 0–2,
a timeout or a limit below its minimum, a screenshot size outside its bounds.
The error names the setting; the allowed values are in
[ENV-PARAMETER.md](ENV-PARAMETER.md).

### Behind the B-API proxy

The B-API calls the agent through its proxy path
`/api/v1/proxy/<service>/…`. The agent is registered there as
**`metadata-agent-canvas`**; edu-sharing is wired to that name. Callers reach
it at:

```
<B-API>/api/v1/proxy/metadata-agent-canvas/<path>
/edu-sharing/rest/bapi/api/v1/proxy/metadata-agent-canvas/<path>   (in the browser, through edu-sharing)
```

The proxy entry belongs to the B-API and is set up there. It needs:

- the address of the agent inside the cluster, for example
  `http://metadata-agent.<namespace>:8000`;
- forwarding of the caller's credential. Since 2026-11-10
  `forwardDelegation: true`: the B-API forwards a delegation token `bdt_…`, as
  `Authorization: Bearer bdt_…` (or raw in the header named by
  `delegationHeader`; then set `METADATA_AGENT_B_API_DELEGATION_HEADER` to the
  same name). The caller's key does not leave the B-API; usage and limits keep
  being booked on it. Older B-API versions forward the key itself with
  `forwardApiKey`; the agent understands that as well;
- `delegationScopes: llm`. Allowed are `llm`, `edu-sharing-llm` and
  `edu-sharing-repo` (read), never write scopes; the agent needs only `llm`,
  because it calls the repository directly. Whether the proxy services
  `text-extraction` and `photon_geocoding` can be scopes is open: without them
  the B-API answers `401` there (staging, 2026-11-16), and the agent goes
  around it. Leave `delegationLifetimeSeconds` empty (the response timeout then
  applies) or ≥ 320 s;
- a `responseTimeout` of at least 310 000 ms, better 320 000 (see
  [Timeouts and request size](#timeouts-and-request-size)). On staging the
  entry stood at 300 000 ms on 2026-11-16: the B-API then cuts runs close to
  the deadline of `/generate`;
- the releases for headers and content types where the B-API asks for them:
  `allowedRequestHeaders` `Content-Type`, `x-client-trace-id`, `X-Request-ID`,
  `x-b3-traceid`, `x-b3-spanid`, `b3`; `allowedResponseHeaders` `X-Request-ID`,
  `Retry-After`; `allowedResponseContentTypes` `application/json` and, for the
  bundle of the web component, `text/javascript`, `text/css`, `font/woff2`,
  `text/html`.

The agent passes the forwarded token or key on unchanged, within the request
only and never into the log: to the LLM endpoints of the B-API and to its proxy
services for text extraction and geocoder. It sets neither `X-Edu-User-Id` nor
`X-API-KEY` next to it. If the B-API refuses the token for text extraction or
geocoder, the agent with `METADATA_AGENT_B_API_SERVICES=auto` tries its own
`B_API_KEY` (if set) and then the service directly ([How text extraction and
geocoder are reached](#how-text-extraction-and-geocoder-are-reached)).

Recommended behind the current B-API, because a service key as fallback is no
longer intended there: `METADATA_AGENT_B_API_AUTH_MODE=forward` without
`B_API_KEY`, and `METADATA_AGENT_TEXT_EXTRACTION_FALLBACK_URL` pointing to the
text extraction inside the cluster. Whether the B-API grants the token the proxy
services, the agent then finds out itself (ENV-PARAMETER.md, "Behind the
current B-API").

**Writing to the repository** (`/upload`, `/nodes`, `/workflow`) is not
possible with the token. The agent therefore keeps writing directly with its
own account (`WLO_GUEST_USERNAME`/`WLO_GUEST_PASSWORD`). That stays the way
until the B-API offers services a way to write; whether T6 allows it like this
is open. The defaults
(`auto`) stay, so existing deployments and operation without the B-API run
unchanged.

**The agent must be reachable only through the B-API.** It does not check the
header itself. If anyone else can reach port 8000, requests without a header
run on `B_API_KEY` in mode `auto`. Do not put an ingress in front of it. The
Helm chart enforces this with a NetworkPolicy.

### Standalone

Without the B-API proxy in front, nothing forwards a key:

- **With a B-API key:** the LLM calls go to the B-API with `B_API_KEY`
  (mode `auto` without an incoming header, or `static`). Text extraction and
  geocoder also go through the B-API with this key, unless
  `METADATA_AGENT_B_API_SERVICES=direct` is set.
- **With OpenAI:** `METADATA_AGENT_LLM_PROVIDER=openai` and `OPENAI_API_KEY`.
  Text extraction and geocoder are called directly, without a key. The text
  extraction is chosen by the repository URL (see the table below).

A proxy of your own must restrict access. If that proxy sends an
`Authorization: Bearer` header that is not a B-API key (a session token, for
example), set `METADATA_AGENT_B_API_AUTH_MODE=static`. In mode `auto` the agent
would pass that header on to the B-API, and every call would fail with `502`
`LLM_AUTH_REJECTED`.

### Which key the agent uses

`METADATA_AGENT_B_API_AUTH_MODE` decides which key the calls to the B-API
carry: LLM, text extraction and geocoder. It applies to the three `b-api-*`
providers. Exactly one header is sent.

| Mode | Request with `Authorization: Bearer …` | Request without the header |
|---|---|---|
| `auto` (default) | The header is passed on unchanged | `B_API_KEY`, sent as `X-API-KEY` |
| `forward` | The header is passed on unchanged | `502` `LLM_AUTH_MISSING`; from a URL: `502` `TEXT_EXTRACTION_AUTH_MISSING` |
| `static` | The header is ignored | `B_API_KEY`, sent as `X-API-KEY` |

Only the scheme `Bearer` counts. The forwarded key lives for the one request
that brought it: it is not stored and not logged. The answer of `/generate`
names the source in `processing.auth_source` (`forwarded`, `static` or
`openai`), never the key.

`/ready` reports a mode that cannot work: `static` without `B_API_KEY`, or
`openai` without `OPENAI_API_KEY`. A missing `B_API_KEY` in mode `auto` is not
an error for `/ready`, because behind the proxy every call brings a key; the
agent only warns at startup.

### How text extraction and geocoder are reached

`METADATA_AGENT_B_API_SERVICES` decides the way:

| Value | Text extraction (`/from-url`) | Geocoder |
|---|---|---|
| `auto` (default) | As `proxy`, if the provider is a `b-api-*` one and its LLM address lies under `METADATA_AGENT_B_API_BASE_URL`, with `direct` as the last way out (see below). Otherwise as `direct` | as on the left |
| `proxy` | `<B-API>/api/v1/proxy/text-extraction`, with the credential of the LLM calls, then with `B_API_KEY` | `<B-API>/api/v1/proxy/photon_geocoding/api`, the same |
| `direct` | By the repository URL, without a key (see below) | `https://photon.komoot.io/api`, without a key |

Through the B-API the services always belong to the same environment as the
B-API: staging to staging, production to production.

With `auto` a call tries three ways until one gives a usable answer
(`src/services/service_ways.py`):

| Way | Credential | On to the next way on |
|---|---|---|
| 1. through the B-API | the forwarded token or the caller's key | `401`, `403`, `429` |
| 2. through the B-API | the agent's own `B_API_KEY` (not with `B_API_AUTH_MODE=forward`) | `401`, `403`, `429` |
| 3. directly | none: Photon, or the text extraction of the repository's environment | – |

- If the B-API gives no usable answer otherwise (another error status such as
  `404` or `5xx`, no JSON, no connection, connecting takes too long), the call
  goes straight to way 3; another key would not help. A `504` of the B-API
  counts as a page that is too slow (see the next point).
- A `429` on way 1 means the caller's quota is used up. Then the agent's key
  does not step in, the call goes to way 3; with `proxy` the text extraction
  answers `503` `TEXT_EXTRACTION_RATE_LIMITED`.
- A page that is too slow (read time exceeded) and a page without text are the
  answer: another way would wait as long or find as little.
- A way the B-API refused with `401` or `403` is skipped by the process for 10
  minutes and then tried again: the agent's own key for all requests, a
  forwarded credential only for itself (remembered by a hash). One caller's
  refused credential therefore never moves another caller onto the agent's
  key. With delegation tokens issued anew per request, every request pays the
  one refused attempt until the scopes are right. The log names a refusal
  once ("Weg 'forwarded' abgelehnt …; für 600 s übersprungen"),
  `GET /debug/dependencies` shows the ways under `refused_ways`. If a way is
  skipped only from memory, the agent answers as for a fresh refusal
  (`TEXT_EXTRACTION_AUTH_REFUSED`, the geocoder's warning).
- Way 2 books text extraction or geocoder on the agent's key instead of the
  caller's. The LLM calls keep carrying the caller's credential only.
- Way 3 always exists for the geocoder. For the text extraction it is
  `METADATA_AGENT_TEXT_EXTRACTION_FALLBACK_URL`, without it the public address
  of the repository's environment: reachable on staging, not on production on
  2026-11-16 (self-signed certificate). In production enter the text
  extraction inside the cluster. If every way fails, the answer names the
  reason of the first.
- Delegation tokens (`bdt_…`) all carry the scopes of the same proxy entry,
  and each request brings a new one. The agent therefore remembers a refusal
  for all delegation tokens; a forwarded key (`forwardApiKey`) still only for
  itself.
- `proxy` has ways 1 and 2 only, `direct` way 3 only.

For `direct`, the agent derives the text extraction from
`METADATA_AGENT_REPOSITORY_URL`:

| Repository host | Text extraction |
|---|---|
| `*.openeduhub.net` with `staging` in the name | `https://text-extraction.staging.openeduhub.net` |
| `redaktion.openeduhub.net` or `*.prod.*` under `openeduhub.net` | `https://text-extraction.prod.openeduhub.net` |
| Any other host | None. Extraction from a URL answers `503` `TEXT_EXTRACTION_NOT_CONFIGURED` until `METADATA_AGENT_TEXT_EXTRACTION_API_URL` is set |

`METADATA_AGENT_TEXT_EXTRACTION_API_URL` names a text extraction of your own,
for example the service inside the cluster. It overrides the choice for the
text extraction only; the geocoder still follows
`METADATA_AGENT_B_API_SERVICES`. Such a text extraction gets the key only if
its address lies under `METADATA_AGENT_B_API_BASE_URL`.

### Rights of the keys in the B-API

Every key the agent is called with needs, in the B-API:

| Right | Needed for | Without it |
|---|---|---|
| The LLM endpoint of the provider (`openai`, `academiccloud` or `router`) | Every extraction | `502` `LLM_AUTH_FORBIDDEN` |
| Proxy service `text-extraction` | Extraction from a URL | With `auto` the own `B_API_KEY`, then the text extraction directly; if both fail, or with `proxy`: `502` `TEXT_EXTRACTION_AUTH_REFUSED` |
| Proxy service `photon_geocoding` | Coordinates of places | With `auto` the own `B_API_KEY`, then Photon directly (log warning); with `proxy`, if `B_API_KEY` is refused too: places stay without coordinates, `processing.warnings` says why |

This applies to the user key and the guest key that edu-sharing uses, to the
`delegationScopes` of the proxy entry (current B-API) and to `B_API_KEY`. The rights are granted in the B-API, not in the agent. To avoid
the two proxy services, set `METADATA_AGENT_B_API_SERVICES=direct`.

---

## Image

The GitLab pipeline (`.gitlab-ci.yml`) builds the image and pushes it to the
internal registry:

```
$DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api
```

| Tag | Built on |
|---|---|
| `main` | Every push to `main` |
| `develop` | Every push to `develop` |
| `v4.0.4` | The Git tag `v4.0.4`. A release tag must have the form `vX.Y.Z` and match the version in `pyproject.toml`; other tags publish nothing |

Before the push, the pipeline starts the image read-only and without keys
(`scripts/smoke-image.sh`): it waits for `/health`, checks `/ready`, the
internal port (`/ready`, `/metrics`) and that `tini` is the init process, and
starts Chromium in it. Then it scans the image with Trivy. A critical
vulnerability with a fix available stops the publish. For a release tag the
pipeline also writes a software bill of materials (CycloneDX, job artifact
`sbom.cdx.json`, kept for one year). Use version tags in production; `main` and
`develop` move.

```bash
docker pull $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v4.0.4
```

The GitLab pipeline is the primary one. The GitHub mirror has its own
workflow (`.github/workflows/docker-publish.yml`; GitLab ignores the `.github`
folder): the same checks, and it publishes to Docker Hub as
`<GitHub owner>/<repo>:<tag>`, for `main` and tags `v*`, if the repository
secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (access token with write
access) are set. If one is missing, it only builds and checks and reports
"Image built but not published".

A published image is checked again on a schedule, because vulnerabilities are
also found after a release:

| Where | Runs | Checks |
|---|---|---|
| GitLab: a pipeline schedule on `main` (to be created once under CI/CD > Schedules) | Only the jobs `scheduled dependency checks` and `scheduled image scan` | `pip-audit` on `requirements.txt`; the age of the bundled Chromium (`scripts/check_browser_age.py` with `REQUIRE_BROWSER_AGE=1`); a Trivy scan of the newest release image (or the tag in the schedule variable `SCAN_TAG`), which fails on a `HIGH` or `CRITICAL` vulnerability with a fix available. The bill of materials is kept as artifact for 30 days |
| GitHub: `.github/workflows/scheduled-checks.yml`, daily at 04:23 UTC and on manual start | Jobs `dependencies` and `image` | The same checks against the newest release image on Docker Hub. The bill of materials is not kept; the job summary names its number of components |

A failing image scan is the sign to release again.

What the image contains:

| | |
|---|---|
| Base | `python:3.13-slim`, pinned by digest |
| User | `appuser` (UID 1000). The code under `/app` belongs to root and is read-only for the process |
| Writable paths needed | `/tmp` and `/home/appuser` (Chromium profile and caches). The application itself writes nothing |
| Chromium | Included for screenshots: the headless shell that Playwright installs (`playwright install --only-shell chromium`). Screenshots are always taken inside the container |
| Process | `tini` as init process (`ENTRYPOINT`), which starts `uvicorn src.main:app` on port 8000, one process. uvicorn listens on `UVICORN_HOST` (`0.0.0.0` in the image) and keeps idle connections open for 75 s, longer than the 60 s a proxy usually keeps them |
| `HEALTHCHECK` | `/health` on port 8000 every 30 s, timeout 10 s, 3 retries |
| Labels | `org.opencontainers.image.version` and `org.opencontainers.image.revision` (the commit) |

To see which commit an image was built from:

```bash
docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' <image>
```

A runtime without this image must provide the same: Chromium for Playwright
(no screenshots without it), the built web component under
`src/static/widget/dist/` shipped with the application, and requests that may
run for up to 300 s.

---

## Docker and Docker Compose

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
  $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v4.0.4
```

| Option | Why |
|---|---|
| `-p 127.0.0.1:8000:8000` | Only this host reaches the port. In mode `auto` anyone who reaches it runs extractions on `B_API_KEY`. Publish it to other hosts (`-p 8000:8000`) only behind an access restriction |
| `--read-only --tmpfs /tmp --tmpfs /home/appuser` | Read-only root file system as in the Helm chart, with the two writable paths Chromium needs |
| `--stop-timeout 320` | A running `/generate` may take up to 300 s. Docker's default of 10 seconds would cut it off at every restart |

More settings go in as further `-e` options, or all of them in a file with
`--env-file <file>`. Common ones in production:

| Setting | Effect |
|---|---|
| `METADATA_AGENT_LOG_FORMAT=json` | One JSON object per log line, for a log collector |
| `METADATA_AGENT_INTERNAL_PORT=9000` | Second port with `/health`, `/ready` and `/metrics`. Do not publish it outside the host or network that scrapes it |
| `FORWARDED_ALLOW_IPS=<proxy IP>` | Whose `X-Forwarded-*` headers uvicorn accepts, see [below](#behind-a-tls-proxy) |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS=<seconds>` | Only with a stop timeout other than 320 s: the start warnings then compare the deadlines with this value |

### Docker Compose

A service for production with the image from the registry:

```yaml
services:
  metadata-agent-api:
    image: ${DOCKER_REGISTRY}/projects/wlo/meta-services/metadata-agent-api:v4.0.4
    ports:
      - "127.0.0.1:8000:8000"   # only this host; other hosts only behind an access restriction
    read_only: true
    tmpfs:
      - /tmp
      - /home/appuser
    stop_grace_period: 320s      # a running /generate takes up to 300 s
    restart: unless-stopped
    environment:
      - B_API_KEY=${B_API_KEY:-}
      - WLO_GUEST_USERNAME=${WLO_GUEST_USERNAME:-}
      - WLO_GUEST_PASSWORD=${WLO_GUEST_PASSWORD:-}
      - METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
      - METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
      - METADATA_AGENT_LOG_FORMAT=json
```

Compose fills `${…}` from the shell or from a `.env` file next to the compose
file. Keep that file out of version control.

The `docker-compose.yml` in the repository follows the same rules but builds
the image from the source tree (`build: .`) and defaults to the staging
addresses. It also passes a whole `.env` file to the container (`env_file`,
optional; this form needs Docker Compose 2.24 or newer). Its healthcheck uses
`/health`, so a container without the repository account stays up although
`/ready` answers `503`. Three settings keep an empty value from `.env` instead
of falling back to their default (`${VAR-default}` in the file):
`METADATA_AGENT_LLM_VERBOSITY`, `METADATA_AGENT_LLM_REASONING_EFFORT` and
`METADATA_AGENT_CORS_ORIGINS`.

### Behind a TLS proxy

uvicorn accepts `X-Forwarded-Proto` and `X-Forwarded-For` only from the
addresses in `FORWARDED_ALLOW_IPS` (a uvicorn variable, default `127.0.0.1`).
No answer of the agent depends on these headers: `/widget/info` returns
relative references instead of absolute URLs, and the agent sends no redirect
(a path with a trailing slash such as `/generate/` answers `404`). The variable
only decides whose headers uvicorn takes over for the scheme and the client
address of a request.

- **Docker:** `-e FORWARDED_ALLOW_IPS=<proxy IP>` (comma-separated for several).
- **Helm:** the chart sets it from `forwardedAllowIps`, default `"*"`. That is
  acceptable only because the NetworkPolicy lets nothing but the B-API and
  Prometheus reach the pod. With `networkPolicy.enabled=false`, enter the
  addresses of the proxies.

---

## Kubernetes with the Helm chart

The chart is in [`helm/metadata-agent/`](helm/metadata-agent/README.md)
(README in German). Its version is the version of the service: chart 4.0.4
deploys image tag `v4.0.4`. The chart has been checked statically so far
(`helm lint`, kubeconform, tests), not in a cluster. The first installation is
the [checklist below](#first-installation-in-a-cluster-checklist).

### Install

```bash
kubectl create secret generic metadata-agent \
  --from-literal=B_API_KEY='<B_API_KEY>' \
  --from-literal=WLO_GUEST_USERNAME='<WLO_GUEST_USERNAME>' \
  --from-literal=WLO_GUEST_PASSWORD='<WLO_GUEST_PASSWORD>'
helm install metadata-agent helm/metadata-agent \
  --set image.repository=$DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api \
  --set secret.existingSecret=metadata-agent
```

`helm install` prints the name of the resources and warnings about the chosen
values. The pods read the Secret only at startup, and a changed Secret does not
restart them. After a change:

```bash
kubectl rollout restart deployment/metadata-agent
```

### Values to set before installing

| Value | Default | What to do |
|---|---|---|
| `image.repository` | empty | **Required.** Rendering fails without it |
| `image.tag` | `v4.0.4` | The image must exist in the registry and be built from the same state as the chart (see [Updates](#updates)) |
| `image.digest` | empty | `sha256:` and 64 hex digits pin the image: it is pulled as `<repository>@<digest>`, and the tag is not used. A tag can be moved to another image, a digest cannot |
| `secret.existingSecret` | empty | Name of the Secret with `B_API_KEY`, `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`. Empty: the chart expects a Secret named like its resources. `B_API_KEY` is not needed in mode `forward` |
| `env.METADATA_AGENT_B_API_BASE_URL`, `env.METADATA_AGENT_REPOSITORY_URL` | Production | Set both for staging. Neither may be empty |
| `env.METADATA_AGENT_WLO_INBOX_ID` | `21144164-30c0-4c01-ae16-264452197063` (the code default) | The folder new nodes are created in. Check that it exists in the target repository and that the account in the Secret may write to it; a wrong id only shows as a failing `/upload` |
| `env.METADATA_AGENT_B_API_AUTH_MODE` | `auto` | `forward` if only forwarded keys may be used |
| `env.METADATA_AGENT_REQUIRE_REPOSITORY` | `"true"` | `"false"` for a deployment that only generates and has no repository account |
| `networkPolicy.ingressNamespaces` | `[b-api]` | Namespace of the B-API pods that call the agent |
| `networkPolicy.prometheusNamespaces` | `[monitoring]` | Namespace of Prometheus |
| `networkPolicy.egressNamespaces`, `networkPolicy.egressPorts` | `[b-api]`, `[80, 443, 8000, 8080]` | Namespaces the agent calls inside the cluster, and the ports of their pods |
| `networkPolicy.dns` | `kube-system`, `k8s-app: kube-dns` | Namespace and pod label of the cluster DNS |
| `serviceMonitor.labels`, `prometheusRule.labels` | `{}` | Labels the selectors of your Prometheus expect |
| `sharedLlmLimit.enabled` | `false` | `true` with AcademicCloud models (see [Scaling](#academiccloud-a-limit-shared-by-all-instances)) |
| `listenHost` | `"0.0.0.0"` | `"::"` in a cluster whose pods have IPv6 addresses (IPv6 only, or dual-stack with IPv6 first) |

Further environment variables go under `env`. The chart sets five variables
itself and refuses them under `env`:

| Variable | Set from |
|---|---|
| `METADATA_AGENT_INTERNAL_PORT` | `service.internalPort` |
| `FORWARDED_ALLOW_IPS` | `forwardedAllowIps` |
| `UVICORN_HOST`, `METADATA_AGENT_INTERNAL_HOST` | `listenHost`: one address for the API port and the internal port |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS` | `terminationGracePeriodSeconds` |

With `listenHost: "::"` probes and Services reach the pod over IPv6; the API
port then listens on IPv6 only.

The chart stops rendering (`helm install`, `helm upgrade`, `helm template`)
instead of installing something that fails in the cluster:

- `image.repository` is empty, or `image.digest` is not `sha256:` and 64 hex
  digits;
- `service.internalPort` is not a port from 1 to 65535;
- `env.METADATA_AGENT_B_API_BASE_URL` or `env.METADATA_AGENT_REPOSITORY_URL`
  is empty (the agent would fall back to the staging address in its code);
- one of the five variables above is set under `env`, or one of the two LLM
  limits while `sharedLlmLimit.enabled` is `true`;
- `sharedLlmLimit.requestsPerMinute` is 0 or less, or rounds to a rate of 0
  per pod;
- with `ingress.enabled`: the annotation `proxy-body-size` does not name the
  size of `env.METADATA_AGENT_MAX_REQUEST_BYTES` (`20m` for 20971520, `40m`
  for 41943040);
- with `secret.create`: `secret.values` holds a key that is not in
  `secret.keys`.

### What the chart sets

| Resource | Content |
|---|---|
| Deployment | 2 replicas, spread over nodes where possible (`topologySpread`, `ScheduleAnyway`). Probes on the internal port. `terminationGracePeriodSeconds: 320` and a `preStop` of 5 s. Runs as UID 1000 without capabilities and without a service account token, root file system read-only; `/tmp` (1Gi) and `/home/appuser` (512Mi) are `emptyDir` volumes with a size limit. The label `app.kubernetes.io/version` of all resources is the version the chart deploys (`appVersion`), not `image.tag` |
| Environment | The values under `env`: production addresses, the inbox folder, JSON logs, service name `metadata-agent`, the timeouts. In addition the five variables the chart sets itself (see above). Every Secret key is bound as optional; `/ready` reports what is missing |
| Service | Two Services. `<name>`: type `service.type` (`ClusterIP`), port 8000 (API) only. `<name>-internal`: always `ClusterIP`, port 9000 (`/health`, `/ready`, `/metrics`), for Prometheus only; `service.type` never publishes it |
| NetworkPolicy | On by default, see below |
| PodDisruptionBudget | With more than one replica: `minAvailable: 1`, so one pod keeps running while a node is drained |
| ServiceMonitor, PrometheusRule | Scraping of `/metrics` and the alert rules, see below |
| Secret | Only with `secret.create` and `--set secret.values.<KEY>=…`; otherwise an existing Secret is used |
| Ingress | **Off.** Not for an agent behind the B-API: the proxy calls the Service inside the cluster. `ingress.enabled` is for standalone operation behind an access restriction; the annotations set 320 s timeouts and a 20 MB body for ingress-nginx, and the body size must match `env.METADATA_AGENT_MAX_REQUEST_BYTES`. With the NetworkPolicy on, add the namespace of the ingress controller to `networkPolicy.ingressNamespaces` |

### NetworkPolicy

| Direction | Allowed |
|---|---|
| In, port 8000 | Only from `networkPolicy.ingressNamespaces` (default `b-api`) |
| In, port 9000 | Only from `networkPolicy.prometheusNamespaces` (default `monitoring`) |
| Out, DNS (port 53) | Only to the cluster DNS (`networkPolicy.dns`) |
| Out, inside the cluster | Only to `networkPolicy.egressNamespaces`, on `networkPolicy.egressPorts` |
| Out, internet | IPv4, all ports, without the private ranges in `networkPolicy.egressExceptCidrs` |

Namespaces are selected by the label `kubernetes.io/metadata.name`, which
Kubernetes sets itself. Adjust namespaces and ports before installing.
Otherwise the B-API does not reach the agent, or the agent does not reach its
services.

### Limits of the NetworkPolicy

- **Ports after address translation:** `networkPolicy.egressPorts` are the
  ports of the target pods (`targetPort`), not of the Service. The policy
  applies after the Service address has been translated.
  `kubectl -n b-api get endpoints -o wide` shows the right ports.
- **Public name, target in the same cluster (LoadBalancer/DNAT):** if the
  repository or the B-API runs behind the ingress or a LoadBalancer Service of
  the same cluster, its name resolves to a public address. kube-proxy rewrites
  it to a pod address in a private range, and `egressExceptCidrs` blocks that.
  Then add the namespace of the ingress controller (or of the service) to
  `egressNamespaces`. With the defaults this case hits LLM, text extraction
  and geocoder at once, because all three use the public address of the B-API.
  Whether it applies to your cluster has to be checked on site (checklist,
  step 2).
- **IPv6:** the policy allows only IPv4 to the outside (`0.0.0.0/0` without
  private ranges). In a dual-stack cluster, connections over IPv6 fail; clients
  fall back to IPv4 as long as the target has an IPv4 address. If you need
  IPv6, add a rule with exceptions of its own (`fc00::/7`, `fe80::/10`,
  `::1/128`) to the policy.
- **NodeLocal DNSCache:** the DNS cache runs on a link-local address of the
  node, not as a pod in `kube-system`. Set `networkPolicy.dns.namespace: ""`;
  port 53 is then open to every address.
- **CNI:** the policy only works with a CNI that enforces NetworkPolicies
  (Calico, Cilium and others).

### Monitoring: ServiceMonitor and PrometheusRule

Both need the CRDs of the Prometheus Operator (for example
kube-prometheus-stack). Without them `helm install` stops with
`no matches for kind "ServiceMonitor"`: install the CRDs first, or switch both
off with `serviceMonitor.enabled=false` and `prometheusRule.enabled=false`.

The **ServiceMonitor** selects the Service `<name>-internal` and scrapes
`/metrics` on the internal port every 30 s (`serviceMonitor.interval`). Metrics
are counted per process:

| Metric | Labels |
|---|---|
| `metadata_agent_http_requests_total` | `method`, `route` (the route template, never an id), `status` |
| `metadata_agent_http_request_duration_seconds` | `method`, `route`. Buckets up to 300 s, in steps of 30 s from 120 s on |
| `metadata_agent_llm_requests_total` | `provider`, `result` (`ok`, `cancelled` or the error code), `auth_source` (`forwarded`, `static`, `openai`, `none`), `config` (`default`, or `override` when the request chose provider or model) |
| `metadata_agent_llm_request_duration_seconds` | `provider`. What the caller of one LLM call waits: for a free slot under the LLM limits of the process (calls in flight, calls per second), for the request, its retries and the pauses between them |
| `metadata_agent_llm_last_failure_timestamp_seconds` | The labels of `metadata_agent_llm_requests_total`. Unix time of the last LLM call that ended in an error (not `ok`, not `cancelled`) |
| `metadata_agent_http_last_internal_error_timestamp_seconds` | `route`. Unix time of the last answer `500` |
| `metadata_agent_llm_tokens_total` | `provider`, `kind`: `prompt` (all input tokens), `cached` (the part read from the prompt cache, a tenth of the input price), `cache_write` (the part written to it, 1.25 times the input price), `completion` |
| `metadata_agent_generations_total` | `status`: how a `/generate` run ended (`success`, `partial`, `failed`), `cancelled` when the caller closed the connection, `refused` for `503` `TOO_MANY_GENERATIONS` |
| `metadata_agent_upstream_requests_total` | `service` (`llm`, `text-extraction`, `repository`, `geocoder`, `other`) |
| `metadata_agent_upstream_responses_total` | `service`, `status` (the HTTP status; for `other` only its class, for example `2xx` or `4xx`) |
| `metadata_agent_upstream_response_duration_seconds` | `service` |

In addition the process and Python runtime metrics of `prometheus_client`.
Requests sent minus responses are the calls that got no answer.

The **PrometheusRule** installs the 13 rules from
[`helm/metadata-agent/files/alerts.yaml`](helm/metadata-agent/files/alerts.yaml),
evaluated per namespace. Critical are only failures with the operator's own key
and the configured model. Failures with a caller's forwarded key or a model
chosen by a request only warn.

| Rule | Severity | Fires on |
|---|---|---|
| `MetadataAgentLlmKeyRefused` | critical | One `LLM_AUTH_*` in 10 min with the own key or no key, and the configured model |
| `MetadataAgentLlmRouteMisconfigured` | critical | One `LLM_ROUTE_*` or `LLM_MODEL_NOT_FOUND` in 10 min with the configured model |
| `MetadataAgentInternalError` | critical | One answer `500` (`INTERNAL_ERROR`) in 10 min |
| `MetadataAgentLlmCallerKeyRefused` | warning | More than 5 `LLM_AUTH_*` in 15 min with a forwarded key |
| `MetadataAgentLlmRequestedModelRejected` | warning | More than 5 failures on route, model or permission in 15 min with a model chosen by the request |
| `MetadataAgentLlmThrottled` | warning | More than 5 `LLM_RATE_LIMITED` in 15 min |
| `MetadataAgentLlmUnreachable` | warning | More than 3 `LLM_UNREACHABLE` in 10 min |
| `MetadataAgentGenerationsFailing` | warning | More than 50 % of the `/generate` runs `failed` in 30 min, with at least 3 runs. Only runs that ended count (`success`, `partial`, `failed`), not `cancelled` or `refused` |
| `MetadataAgentUpstreamUnanswered` | warning | More than 3 calls without an answer in 10 min, for 5 min (per service, without `llm` and `other`) |
| `MetadataAgentUpstreamServerErrors` | warning | More than 5 answers `5xx` in 10 min (per service, without `other`) |
| `MetadataAgentUpstreamKeyRefused` | warning | More than 3 answers `401` or `403` in 10 min from text extraction or geocoder: through the B-API the key lacks the right for the proxy service; called directly, the service itself asks for credentials |
| `MetadataAgentGenerateSlow` | warning | More than 5 % of the `/generate` runs take longer than 270 s, for 15 min |
| `MetadataAgentServerErrors` | warning | More than 5 own answers `5xx` in 10 min (per route, without `/health` and `/ready`, and without the `503` `TOO_MANY_GENERATIONS` of `/generate`) |

The three critical rules fire on the first failure, also on a pod that has just
started: next to the counter they read the two `…_timestamp_seconds` metrics,
which say when a failure last happened, and fire while that is less than
10 minutes ago. A scrape gap, however long, does not make an old failure fire
again. The rules compare the time the pod wrote with the time of Prometheus:
both clocks must agree to well under those 10 minutes. A pod of an older image
has no such metric; for it only the counter decides, which does not see the
first failure of a series that is new. `MetadataAgentGenerateSlow` reads the
share from the 270 s bucket and counts only pods that export it, so pods of an
older image take no part while both run.

A pod that is down or restarting is left to the cluster's own rules
(`TargetDown`, `KubePodCrashLooping`).

---

## Ports and probes

| Port | Content | Reachable for |
|---|---|---|
| `8000` | API, `/docs` (Swagger UI, served by the agent itself), `/openapi.json`, the web component under `/widget/*`. Also `/health` and `/ready`, without details | The B-API proxy or your own proxy |
| `METADATA_AGENT_INTERNAL_PORT` (unset by default; chart: `9000`) | `/health`, `/ready` and `/metrics`, with details | Probes and Prometheus only. Never route it through an ingress. In the chart it has a Service of its own, `<name>-internal` |

- Without `METADATA_AGENT_INTERNAL_PORT` there is no second port and no
  `/metrics`.
- The internal port needs exactly one worker per container. If it cannot be
  bound (taken, or an address the host does not know), the API keeps running,
  the log shows an error, and probes on the internal port fail.
- `METADATA_AGENT_INTERNAL_HOST` is the address the internal port listens on
  (default `0.0.0.0`), `UVICORN_HOST` the address of the API port (a uvicorn
  variable; `0.0.0.0` in the image). In an IPv6 cluster both are `::`; the
  chart sets them together from `listenHost`.
- The agent redirects no path: `/generate/` or `/docs/` with a trailing slash
  answer `404`.
- `/health`, `/ready` and `/debug/*` are not in the OpenAPI spec. `/debug/auth`
  and `/debug/dependencies` answer `404` unless
  `METADATA_AGENT_DEBUG_ENDPOINTS=true`.

| Path | Answer | Meaning |
|---|---|---|
| `/health` | `200` `{"status": "healthy", "version": "4.0.4"}`. On the internal port also `llm_provider` and `b_api_auth_mode` | The process answers. It says nothing about the dependencies |
| `/ready` | `200` `{"status": "ready"}`, or `503` with `status: "not_ready"`, `detail` and `error.code` `NOT_READY`. On the internal port also the reasons (`problems`) | The configuration can work |

`/ready` answers `503` when:

- no schema context could be loaded;
- `B_API_KEY` is missing in mode `static` (with a `b-api-*` provider);
- `OPENAI_API_KEY` is missing with provider `openai`;
- `WLO_GUEST_USERNAME` or `WLO_GUEST_PASSWORD` is missing, unless
  `METADATA_AGENT_REQUIRE_REPOSITORY=false`.

The reasons are written to the log once (`Nicht bereit: …`) and shown on the
internal port, not on the public one. `/ready` deliberately does not call the
dependencies: an outage of the B-API must not take all pods out of the Service
at once. Whether LLM calls succeed only shows in the
[checks after a deployment](#checks-after-a-deployment).

Probes of the Helm chart, all on the internal port:

| Probe | Path | Timing |
|---|---|---|
| Startup | `/health` | Every 2 s, up to 30 failures |
| Liveness | `/health` | Every 15 s, timeout 5 s |
| Readiness | `/ready` | Every 10 s, timeout 5 s |

---

## Timeouts and request size

| What | Setting | Default | When exceeded |
|---|---|---|---|
| One LLM call | `METADATA_AGENT_REQUEST_TIMEOUT` | 90 s | The call is aborted. 90 s leave a buffer below the limit of the whole run |
| A whole `/generate`, `/detect-content-type` or `/extract-field` | `METADATA_AGENT_GENERATE_TIMEOUT` | 300 s | `504` `GENERATION_TIMEOUT`. Field calls of `/generate` that still run 5 s before the limit are stopped; what is done by then comes back as `partial` |
| `/upload`, `/nodes`, `/workflow`, `/screenshot` with `node_id` | `METADATA_AGENT_UPLOAD_TIMEOUT` | 100 s | `504` `UPLOAD_TIMEOUT` |
| One screenshot | fixed | 90 s | The capture fails |
| Text extraction from a URL | fixed | 10 s to connect, 30 s to answer | `504` `TEXT_EXTRACTION_CONNECT_TIMEOUT` or `TEXT_EXTRACTION_TIMEOUT` |
| Request body | `METADATA_AGENT_MAX_REQUEST_BYTES` | 20 MB | `413` `REQUEST_TOO_LARGE` |
| Shutdown: grace period (chart: `terminationGracePeriodSeconds`), `--stop-timeout`, `stop_grace_period` | `METADATA_AGENT_GRACE_PERIOD_SECONDS` tells the agent the value | 320 s | Running requests are cut off |
| Ingress of the chart (`proxy-read-timeout`, `proxy-send-timeout`) | `ingress.annotations` | 320 s | The ingress answers `504` |

The limits of the agent, plus cleanup and `preStop`, must stay below the 320 s
of ingress and grace period. The agent warns at startup if
`METADATA_AGENT_GENERATE_TIMEOUT` + 10 s or `METADATA_AGENT_UPLOAD_TIMEOUT` +
15 s exceeds 320 s. With another grace period, name it in
`METADATA_AGENT_GRACE_PERIOD_SECONDS`: the warnings then compare with that
value. The chart sets the variable from `terminationGracePeriodSeconds`. It
changes nothing else; the platform still ends the process.

What a proxy in front of the agent must allow:

- **At least ~310 s for an answer** (300 s limit plus the answer). This applies
  to the B-API proxy, to edu-sharing and to an ingress of your own; the chart
  cannot set the first two. A shorter timeout cuts long extractions off, and
  the client sees the proxy's `504` instead of the result. ingress-nginx stops
  after 60 s without the annotation `proxy-read-timeout`.
- **Request bodies up to 20 MB.** ingress-nginx allows 1 MB by default
  (`proxy-body-size`); with less than 20 MB an `/upload` with a preview image
  fails at the ingress with `413`.
- **Headers passed through:** `Authorization` (behind the B-API proxy), the
  tracing headers (see [Logging and tracing](#logging-and-tracing)), and
  `X-Forwarded-Proto` / `X-Forwarded-For`.

When a caller closes the connection, for example a proxy after its own timeout,
`/generate`, `/extract-field` and `/detect-content-type` stop their LLM calls.
The log and the metrics show status `499` for it, and
`metadata_agent_generations_total` counts such a `/generate` run as
`cancelled`, not as `failed`.

The 300 s also apply without an ingress (Docker, Compose). For longer runs with
slow models, raise `METADATA_AGENT_GENERATE_TIMEOUT` together with the stop
timeout and the timeouts of the clients in front.

---

## Scaling, throughput and resources

- **Stateless:** no database, no sessions, no files at runtime. Every instance
  can answer every request.
- **One worker per container.** The internal port and the metrics require it.
  Scale with replicas (`replicaCount`, default 2).
- **Limits per process:** they add up with every replica.

| Limit | Default | Setting |
|---|---|---|
| LLM calls in flight | 20 (`b-api-openai`, `b-api-router`, `openai`), 2 (`b-api-academiccloud`) | `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` |
| LLM calls per second | No cap; 2 with `b-api-academiccloud` | `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` (`0` = no cap) |
| `/generate` runs at once | No limit | `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS`; one more run gets `503` `TOO_MANY_GENERATIONS` with `Retry-After` |
| Screenshots at once | 2, further ones wait | fixed |

A value set for the two LLM limits applies to every provider.
`METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` is per second, not per minute:
`2` means 120 calls per minute. If the B-API answers `429` with `b-api-openai`,
lower `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS`.

Durations of one `/generate` with about 50 fields, as measured:

| Provider / model | Duration | Measured |
|---|---:|---|
| `b-api-openai` / `gpt-6-luna` (default), text of 4,400 characters | median 5.2 s; 7.8 s with 10 workers (9.3 s before the changes of that day) | 2026-11-15 |
| `b-api-openai` / `gpt-6-luna`, web pages | 12–22 s | 2026-10-16 |
| `b-api-openai` / `gpt-5.6-luna` | 25.2 s | 2026-08-12 |
| `b-api-academiccloud` / `deepseek-v4-flash` | 32–90 s (queue at the gateway) | 2026-08-12 |

**Resources** (chart defaults): requests 500m CPU and 512Mi memory, limits
2 CPU and 2Gi. These figures are estimated, not measured under load. Memory
peaks come from Chromium: every screenshot starts a browser of its own.

### AcademicCloud: a limit shared by all instances

AcademicCloud models (GWDG; provider `b-api-academiccloud`, or a
`b-api-router` route of AcademicCloud models) have two measured limits. Both
apply to the B-API key, so to all instances together:

- **At most 2 calls in flight** (2026-08-12, staging, `deepseek-v4-flash`). A
  third parallel call is refused with `429` at once.
- **About 10 calls per minute and about 200 per hour** (2026-10-15, router with
  Qwen models). Whether this limit also applies to `deepseek-v4-flash` through
  `b-api-academiccloud` has not been measured; plan with about 10 per minute.

A whole extraction needs about 50 calls. At 10 calls per minute it takes 5 to
6 minutes and ends at `METADATA_AGENT_GENERATE_TIMEOUT` with partial results.
For interactive use, `b-api-openai` remains the default.

The agent throttles per process, and its defaults (2 in flight, 2 per second)
cover only the first limit. Divide the shared limit by the number of
instances:

- **Helm:** `sharedLlmLimit.enabled=true` divides
  `sharedLlmLimit.concurrentRequests` (2) and
  `sharedLlmLimit.requestsPerMinute` (10) by `replicaCount` and sets both
  variables per pod. With 2 replicas each pod gets 1 call in flight and
  0.0833 calls per second. Scaling by hand (`kubectl scale`, an HPA) bypasses
  the division. With more replicas than calls in flight the pods together
  exceed the limit, because each pod gets at least 1. `helm install` warns
  about that, and when AcademicCloud or the router runs with several replicas
  without the division.
- **Without the chart:** set both variables by hand. For one instance:

```
METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL=deepseek-v4-flash
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16   # about 10 per minute
```

Leave `sharedLlmLimit` off for `b-api-openai` and `openai`.

---

## Logging and tracing

Logs go to stdout.

| Setting | Default | Effect |
|---|---|---|
| `METADATA_AGENT_LOG_FORMAT` | `text` (chart: `json`) | `text`: the message alone, one line each. `json`: one object per line |
| `METADATA_AGENT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `METADATA_AGENT_SERVICE_NAME` | `metadata-agent` | The field `service` of every JSON line |

Fields of a JSON line:

| Field | Content |
|---|---|
| `timestamp` | UTC with milliseconds, for example `2026-11-13T09:15:02.417Z` |
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `service` | The service id, `metadata-agent` |
| `logger` | The module, for example `src.api.responses` (error lines), `src.api.routers.*` (endpoints), `src.services.llm_transport` (LLM calls), `metadata_agent.request` (request line) |
| `message` | The text |
| `trace_id`, `span_id` | The B3 ids of the request; `null` outside a request |
| `request_id` | The request id, as in the response header `X-Request-ID`; `null` outside a request |
| `client_trace_id` | Only if the caller sent `x-client-trace-id` |
| `method`, `path`, `status`, `duration_ms` | Only on the request line |
| `exception` | The traceback, only on lines that log an exception |

**Request line.** One line per request replaces the access log of uvicorn, in
both formats: `<method> <path> <status> <duration> ms`, for example
`POST /generate 200 14230 ms`. The path comes without query string and without
headers. `/health` and `/ready` are logged only at `DEBUG`. Requests on the
internal port are not logged.

**Error lines.** Every error response carries the request id as
`error.request_id` and has a log line with the same id:

| Line | Level | For |
|---|---|---|
| `❌ [<request id>] <status> <code>: …` | `ERROR` | Faults of the agent or of a service it depends on (status 5xx) with a code of their own (`LLM_*`, `TEXT_EXTRACTION_*`, `GENERATION_TIMEOUT` …), where available with upstream status and key source |
| `[<request id>] <status> <code>: …` | `INFO`; `WARNING` for `503` `TOO_MANY_GENERATIONS`; `ERROR` for other errors from status 500 | Errors of the caller (4xx) such as `400` `BAD_REQUEST` or `422` `VALIDATION_ERROR`, also those with a code of their own |

To report a failure, quote the request id. Field values, places and addresses
from requests are logged only at `DEBUG`.

**Headers.**

| Header | Direction | Use |
|---|---|---|
| `Authorization: Bearer <key>` | in | From the B-API proxy: the caller's credential: their key (`forwardApiKey`, older B-API) or a delegation token for it (`forwardDelegation`). Passed on unchanged to the B-API |
| `x-client-trace-id`, `x-request-id`, `x-b3-traceid`, `x-b3-spanid`, `x-b3-parentspanid`, `x-b3-sampled`, `x-b3-flags`, `b3` | in, passed on | Tracing (B3). Passed on unchanged to the B-API, the text extraction and the repository. Never to third parties such as Photon called directly or pages being captured |
| `X-Request-ID` | out | The request id, on every response |
| `X-Forwarded-Proto`, `X-Forwarded-For` | in | Accepted only from `FORWARDED_ALLOW_IPS` (see [Behind a TLS proxy](#behind-a-tls-proxy)) |

- The request id is the incoming `x-client-trace-id`, else the incoming
  `x-request-id`, else the B3 trace id. An incoming value is accepted only if
  it consists of letters, digits, `.`, `_` and `-` (at most 64 characters). An
  `x-request-id` that does not fit is not passed on either; the agent's request
  id takes its place.
- Without incoming B3 ids the agent starts a trace of its own and passes it on,
  so that the calls of one request still belong together. An `x-b3-traceid`
  that is not a valid one (16 or 32 hex characters) also starts a new trace. The agent
  reports no spans of its own.

---

## Outgoing requests: public targets only

Every URL the agent opens itself must be a public http(s) target: `source_url`
with `input_source=url`, the pages of screenshots (`/screenshot`, `/generate`,
`/upload`) and preview image URLs of `POST /nodes`.

| Rule | Detail |
|---|---|
| Refused | `localhost`, `*.localhost`, `*.local`, `*.internal`, `*.lan`, `*.home.arpa`, loopback, private ranges, link-local (including `169.254.169.254`), credentials in the URL, other schemes |
| Preview images | Every redirect is checked; at most 5 redirects, at most 10 MB |
| Screenshot browser | Direct requests to internal hosts are blocked. If a redirect or a navigation of the page (also in frames or for images) leads to an internal target, the capture is discarded |
| The agent's own API | Every request of the screenshot browser to the agent itself is refused with `403` |
| Screenshots | One Chromium per capture, at most two at once, at most 90 s each including DNS check and waiting time |

Two limits remain:

- When a redirect leads to an internal target, the redirect request itself
  (a GET) has already been sent. Whoever has to rule that out as well needs
  egress rules for the container. The NetworkPolicy of the Helm chart blocks
  the private ranges.
- The check resolves DNS **inside the container**. A host name that points to a
  private address there, for example with split-horizon DNS in the cluster, is
  refused as well, even if it is public from the outside.

Why a capture failed (network error, Chromium missing) is in the log. The API
answer gives only a neutral reason.

---

## Checks after a deployment

- [ ] `/health` answers `200` with the expected version.
- [ ] `/ready` answers `{"status": "ready"}`.
- [ ] The start log shows the intended addresses (`LLM API Base`,
      `Text Extraction URL`, `Geocoder URL`, `Repository URL`), no
      `Nicht bereit:`, and no unexpected warning (`⚠️`).
- [ ] With the internal port: `/metrics` answers and contains
      `metadata_agent_llm_requests_total`.
- [ ] Tests 1 to 4 below succeed, tests 3 and 4 once logged in and once as a
      guest.
- [ ] Behind a proxy: `/widget/info` answers through it, and the script it
      names (`dist/main.js`, relative to the address of `/widget/info`) can be
      loaded over the same path: `…/widget/dist/main.js`.

`/health` only says that the process runs. Whether the LLM calls go through
shows only in the tests.

### Tests through the B-API proxy

Run the commands in the browser console of an edu-sharing page where you are
logged in. They use the existing session; no API key is needed. The service
name in the path is the proxy entry `metadata-agent-canvas`. The sample texts
are German, like the content the agent usually handles.

**Test 1: one field, one LLM call.** The smallest real test:

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

**Test 2: a whole generation.**

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

| Result | Meaning |
|---|---|
| `200`, `success`, content type `(llm)` | In order. `keySource: forwarded` means the key came through the proxy |
| `502`, `LLM_AUTH_FORBIDDEN` | The B-API refuses the key (`403`). Check the rights of the key |
| `502`, `LLM_AUTH_REJECTED` | The B-API does not accept the key or token (`401`). For a delegation token also: expired, call ended, or the path lies outside `delegationScopes` (`llm` missing). The exact reason is in the B-API's log, not in the answer |
| `502`, `LLM_AUTH_MISSING` | Mode `forward`, but no `Authorization` header arrived. Check `forwardDelegation` or `forwardApiKey` |
| `503`, `LLM_RATE_LIMITED` | The quota or limit of the key is used up |
| `partial` | Some fields failed; `processing.errors` names the causes |
| Content type `(keywords)` | The content type is only guessed from keywords; the LLM gave no usable answer |

**Test 3: extraction from a URL** (text extraction through the B-API). As
test 2, with this body:

```js
    body: JSON.stringify({ input_source: "url", source_url: "https://klexikon.zum.de/wiki/Saturn",
                           schema_file: "auto", language: "de", enable_geocoding: false })
```

| Result | Meaning |
|---|---|
| `200`, `success` | The text arrived. Whether the forwarded credential may use `text-extraction` shows only together with the log or `GET /debug/dependencies`: with `auto`, `refused_ways` contains `text-extraction:forwarded` if the agent had to fall back to its own key or the direct way. Then add the right (or the scope) for the proxy service `text-extraction` |
| `502`, `TEXT_EXTRACTION_AUTH_REFUSED` | Every way failed (with `proxy`: both credentials refused). Add the right for the proxy service `text-extraction` |
| `502`, `TEXT_EXTRACTION_AUTH_MISSING` | No credential and no other way: no `Authorization` arrived (mode `forward`) or `B_API_KEY` is missing, and there is no direct way (`proxy`, or a repository outside staging and production) |
| `503`, `TEXT_EXTRACTION_NOT_CONFIGURED` | Operation without the B-API and a repository that is neither staging nor production: set `METADATA_AGENT_TEXT_EXTRACTION_API_URL` |
| `504`, `TEXT_EXTRACTION_CONNECT_TIMEOUT` | The text extraction service cannot be reached |

**Test 4: places with coordinates** (geocoder through the B-API). As test 2,
with `enable_geocoding: true` and a text with a place, for example "Workshop am
15. März 2025 im Bauhaus-Museum Weimar". Print in addition:
`console.log(data["schema:location"], p.warnings)`.

| Result | Meaning |
|---|---|
| Place with `geo.latitude` and `geo.longitude` | In order: the key may use the proxy service `photon_geocoding` |
| Place without `geo`, and "Orte bleiben ohne Koordinaten …" in `processing.warnings` | The B-API refuses the key for the geocoder or throttles it (with `auto`: and Photon itself does not answer either); the warning names the status. Add the right for `photon_geocoding` |
| Place with `geo`, and "… Photon wird direkt gefragt." in the log | With `auto`: the B-API path to the geocoder failed (refused, error status, no answer or no key; the line names it), the agent asked Photon itself. With a refusal check the right for `photon_geocoding` |

Run tests 3 and 4 once logged in and once as a guest: edu-sharing uses two
different keys for them.

### Tests without the proxy

Standalone, or directly at a container, the same requests go to port 8000. The
agent then uses `B_API_KEY` or `OPENAI_API_KEY`:

```bash
curl -s http://127.0.0.1:8000/extract-field \
  -H "Content-Type: application/json" \
  -d '{"text": "Am 12. November findet in Weimar ein Konzert statt.", "schema_file": "core.json", "field_id": "cclom:title", "language": "de"}'
```

### If a test fails

1. Note the request id (`X-Request-ID`, `error.request_id`) and look up the log
   line with the same id.
2. Set `METADATA_AGENT_DEBUG_ENDPOINTS=true` and call `/debug/auth` and
   `/debug/dependencies` over the same path. `/debug/auth` shows whether a
   forwarded key arrives (presence, scheme and length only).
   `/debug/dependencies` shows whether the B-API, text extraction, geocoder and
   repository can be reached and whether the key is accepted. Details:
   [ENV-PARAMETER.md](ENV-PARAMETER.md).
3. Switch the debug endpoints off again.

---

## First installation in a cluster: checklist

The chart has not been installed in a cluster yet. This list is the first
trial; record the results in the handover protocol. `<ns>` is the target
namespace, `<release>` the release name, and `<name>` the name of the
resources: the release name if it contains "metadata-agent", otherwise
`<release>-metadata-agent` (`helm install` prints it).

1. **Build and push the image.** Set the Git tag of the version (`v4.0.4`) on
   the commit the chart is taken from. The GitLab pipeline builds, runs the
   smoke test (read-only, Chromium), scans with Trivy and pushes only after
   that. Check:
   `docker pull $DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api:v4.0.4`.
   Without this tag in the registry, `helm install` with the defaults ends in
   `ImagePullBackOff`.
2. **Find namespaces and ports:**
   - `kubectl get ns -L kubernetes.io/metadata.name` shows the namespaces of
     the B-API (caller and target) and of Prometheus.
   - `kubectl -n <b-api-ns> get svc,endpoints -o wide` shows the ports of the
     B-API pods for `networkPolicy.egressPorts`. By default the agent calls
     LLM, text extraction and geocoder through `METADATA_AGENT_B_API_BASE_URL`.
     The text extraction inside the cluster needs its ports only if
     `METADATA_AGENT_TEXT_EXTRACTION_API_URL` points to it.
   - `kubectl -n kube-system get pods -l k8s-app=kube-dns` must find the
     cluster DNS. If not, adjust label and namespace in `networkPolicy.dns`;
     with NodeLocal DNSCache set `dns.namespace: ""`.
   - `kubectl get ingress -A | grep -e repository -e redaktion -e b-api`: if
     the repository or the B-API is in the same cluster, add the namespace of
     the ingress controller to `egressNamespaces` (see
     [Limits of the NetworkPolicy](#limits-of-the-networkpolicy)). **Check this
     first:** it affects LLM, text extraction and geocoder at once.
3. **Create the Secret** (`kubectl -n <ns> create secret generic …`, see
   [Install](#install)): `B_API_KEY` (not needed in mode `forward`),
   `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`.
4. **Write a values file**, for example `staging-values.yaml`:
   `image.repository`, `networkPolicy.*` from step 2, and under `env` the
   addresses of the environment (`METADATA_AGENT_B_API_BASE_URL`,
   `METADATA_AGENT_REPOSITORY_URL`), the inbox folder of that repository
   (`METADATA_AGENT_WLO_INBOX_ID`) and `METADATA_AGENT_B_API_AUTH_MODE`. Text
   extraction and geocoder follow the B-API; the keys need the rights for the
   proxy services `text-extraction` and `photon_geocoding`.
5. **Check and install:**
   ```bash
   helm lint helm/metadata-agent --strict -f staging-values.yaml
   helm upgrade --install <release> helm/metadata-agent -n <ns> \
     -f staging-values.yaml --set secret.existingSecret=metadata-agent \
     --wait --timeout 5m
   ```
6. **Pods and probes:**
   - `kubectl -n <ns> get pods -o wide`: all pods `Running` and ready, on
     different nodes where possible.
   - `kubectl -n <ns> describe pod -l app.kubernetes.io/name=metadata-agent`:
     no failed probes.
   - `kubectl -n <ns> logs deploy/<name>`: no `Nicht bereit:`; read the start
     warnings (`⚠️`).
   - The internal port:
     ```bash
     kubectl -n <ns> port-forward deploy/<name> 9000:9000
     curl -s localhost:9000/ready     # {"status":"ready"}
     curl -s localhost:9000/metrics | grep metadata_agent_llm_requests_total
     ```
     The last command shows the series created at startup with the value `0`.
     `kubectl -n <ns> get svc` lists two Services: `<name>` with port 8000 and
     `<name>-internal` with port 9000.
7. **NetworkPolicy:**
   - The agent must answer from the namespace of the B-API and not from
     another one:
     ```bash
     kubectl -n <b-api-ns> run np-test --rm -it --restart=Never \
       --image=curlimages/curl -- curl -s -m 5 http://<name>.<ns>:8000/health
     kubectl -n default run np-test --rm -it --restart=Never \
       --image=curlimages/curl -- curl -s -m 5 http://<name>.<ns>:8000/health
     ```
   - Outgoing from the pod (DNS, B-API, repository):
     ```bash
     kubectl -n <ns> exec deploy/<name> -- python -c \
       "import urllib.request as u; print(u.urlopen('<REPOSITORY_URL>/_about', timeout=10).status)"
     kubectl -n <ns> exec deploy/<name> -- python -c \
       "import urllib.request as u, urllib.error as e
     try: print(u.urlopen('<B_API_BASE_URL>/api/v1/llm/openai/models', timeout=10).status)
     except e.HTTPError as x: print('reachable, HTTP', x.code)"
     ```
     The B-API answers `401` without a key; that means reachable. A timeout
     means: check namespace, port or the DNAT case from step 2. Whether the key
     may use text extraction and geocoder only shows in step 10.
8. **Screenshot in the read-only pod:**
   ```bash
   kubectl -n <ns> port-forward deploy/<name> 8000:8000
   curl -s -o /dev/null -w "%{http_code} %{content_type}\n" -X POST localhost:8000/screenshot \
     -H "Content-Type: application/json" -d '{"url": "https://example.org/"}'
   ```
   Expected: `200 application/json`; the answer holds the image as
   `image_base64`. No `EPERM`, `EROFS` or `ENOSPC` in the log.
9. **Web component through the proxy:** call
   `…/proxy/metadata-agent-canvas/widget/info` through the B-API proxy (browser
   console of an edu-sharing page). The answer names relative references
   (`dist/main.js`), resolved against the address of the call. Then
   `…/proxy/metadata-agent-canvas/widget/dist/main.js` must answer `200`.
10. **End to end through the B-API proxy:** tests 1 to 4 from
    [Checks after a deployment](#checks-after-a-deployment). With
    `forwardDelegation` or `forwardApiKey`, test 2 shows `keySource: forwarded`. Tests 3 and 4 check
    the rights for `text-extraction` and `photon_geocoding`, once logged in and
    once as a guest.
11. **Monitoring:** `kubectl -n <ns> get servicemonitor,prometheusrule`. In
    Prometheus the target (the Service `<name>-internal`) is `up` and the
    rules `metadata-agent` are loaded.
12. **Practice a rollback once** ([Rollback](#rollback)) while nobody uses the
    environment yet.

---

## Updates

For every update:

- [ ] Read the update note of the version in [CHANGELOG.md](CHANGELOG.md)
      (German) and the section below.
- [ ] Use chart and image of the same state.
- [ ] Docker: pull the new tag and recreate the container. Helm:
      `helm upgrade <release> helm/metadata-agent -n <ns> -f <values> --wait`.
- [ ] Run the [checks after a deployment](#checks-after-a-deployment).

The agent is stateless: there is no database and no migration.

### Update from 3.0.x to 4.0.0

The details are in the CHANGELOG under 4.0.0 to 4.0.4. The chart names
`image.tag: v4.0.4` and needs the image of that version: the image
released as 3.0.1 on 2026-10-20 does not work with it (see "Keep chart and
image together" below).

**Before the update:**

- [ ] **Add key rights in the B-API.** Every key the agent is called with
      (user and guest keys from edu-sharing, `B_API_KEY`) needs the proxy
      services `text-extraction` and `photon_geocoding` in addition to the LLM
      endpoints, with the current B-API in the `delegationScopes` of the proxy
      entry. Otherwise the agent with `auto` falls back to its own `B_API_KEY`
      and then to the services directly (log, `refused_ways`); with `proxy`
      extraction from a URL ends with `502` `TEXT_EXTRACTION_AUTH_REFUSED`. To keep the previous way, set
      `METADATA_AGENT_B_API_SERVICES=direct`.
- [ ] **Go through your own values files.** If they contain
      `METADATA_AGENT_TEXT_EXTRACTION_API_URL` (the chart used to set
      `http://bildungs-api-services-full-text.b-api`), the text extraction
      stays at that address and only the geocoder moves to the B-API. Remove
      the line to use the B-API.
- [ ] **Keep chart and image together.** The chart now carries the version of
      the service (before: 0.3.1). It does not work with an image up to the
      released 3.0.1: the chart leaves the address of the text extraction
      empty, and the older image reads that as "no address".
- [ ] **Adapt log evaluations.** In the JSON lines `time` is now `timestamp`.
      `trace_id` is the B3 trace id; the previous value is in `request_id`.
      New are `service` and `span_id`. The access log of uvicorn is gone;
      there is one line per request instead.
- [ ] **Alert rules:** new is `MetadataAgentUpstreamKeyRefused` (the B-API
      refuses the key for text extraction or geocoder).
      `MetadataAgentGenerateSlow` now fires on the share of runs above 270 s
      and needs the buckets of the new image.
- [ ] **Check the values of the settings.** A value a setting does not allow
      now stops the start instead of being accepted: an unknown
      `METADATA_AGENT_LLM_VERBOSITY` or reasoning effort, a temperature
      outside 0–2, `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS`,
      `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS`,
      `METADATA_AGENT_LLM_MAX_TOKENS` or `METADATA_AGENT_REQUEST_TIMEOUT`
      below 1, a negative rate, a screenshot size or delay outside its bounds,
      a `METADATA_AGENT_ALLOWED_LLM_MODELS` that names no model. The bounds
      are in [ENV-PARAMETER.md](ENV-PARAMETER.md).
      `METADATA_AGENT_SCREENSHOT_METHOD` and `METADATA_AGENT_DEFAULT_CONTEXT`
      are gone; remove them.
- [ ] **Internal port of the chart.** Port 9000 moved from the Service
      `<name>` to a Service of its own, `<name>-internal` (always
      `ClusterIP`). The ServiceMonitor of the chart follows; adapt a scrape
      configuration or a port-forward of your own that names the old Service.
- [ ] **Values files of the chart.** `env.METADATA_AGENT_INTERNAL_HOST` is
      refused now: set `listenHost` instead (`"::"` in an IPv6 cluster). An
      ingress annotation `proxy-body-size` that differs from
      `env.METADATA_AGENT_MAX_REQUEST_BYTES`, and an empty B-API or repository
      address, stop the rendering.
- [ ] **Clients and proxies that rely on a redirect.** A path with a trailing
      slash (`/generate/`, `/docs/`) answers `404`; before, the agent
      redirected with `307`. `/widget/info` returns relative references
      (`dist/main.js`) instead of absolute URLs, and its snippets carry the
      placeholder `{AGENT_URL}`.

**After the update:**

- [ ] Run tests 2 to 4 from
      [Checks after a deployment](#checks-after-a-deployment), once logged in
      and once as a guest.

Also new, without anything to do:

- `METADATA_AGENT_SERVICE_NAME` (default `metadata-agent`) and
  `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` (default: no limit).
- An empty environment variable (`VAR=` in a `.env`, `""` in the chart values)
  counts as not set; the default applies. Exceptions:
  `METADATA_AGENT_LLM_VERBOSITY`, `METADATA_AGENT_LLM_REASONING_EFFORT` and
  `METADATA_AGENT_CORS_ORIGINS`, where empty is a value.
- A request id created by the agent is now the B3 trace id: 32 instead of 16
  hex characters.
- Status `499` in the log and the metrics: the caller closed the connection.
  `metadata_agent_generations_total` has the new statuses `cancelled` and
  `refused`.
- `METADATA_AGENT_GRACE_PERIOD_SECONDS` (set by the chart), and the chart
  values `image.digest`, `listenHost` and `env.METADATA_AGENT_WLO_INBOX_ID`.
- The image starts through `tini`, holds only Chromium's headless shell and
  keeps idle connections open for 75 s.

### Update from 2.1.x to 3.0.0

With Docker or Compose it is enough to change the image. With the Helm chart,
adjust the namespaces of the NetworkPolicy first. Clients that branch on status
codes check the table "Breaking" in the CHANGELOG; the shipped web component is
not affected. The web component 3.x needs the agent 3.x.

- [ ] **NetworkPolicy** (new, on by default): adjust
      `networkPolicy.ingressNamespaces`, `prometheusNamespaces`,
      `egressNamespaces`, `egressPorts` and `dns` to the cluster (see
      [NetworkPolicy](#networkpolicy)).
- [ ] **`METADATA_AGENT_INTERNAL_PORT`** now comes from `service.internalPort`.
      Remove a value of your own under `env`.
- [ ] **`/ready` requires the repository account.** Without
      `WLO_GUEST_USERNAME` or `WLO_GUEST_PASSWORD`, for example after a typo in
      `secret.existingSecret`, the pod does not become ready. A deployment that
      only generates sets `env.METADATA_AGENT_REQUIRE_REPOSITORY: "false"`.
- [ ] **Secret changes no longer restart the pods.** The annotation
      `checksum/secret` is gone. After a change:
      `kubectl rollout restart deployment/<name>`.
- [ ] **Timeouts in front of the agent: at least ~310 s** in the B-API proxy,
      in edu-sharing and in an ingress of your own. `/generate` may now run
      300 s (`METADATA_AGENT_GENERATE_TIMEOUT`), also without an ingress;
      2.1.x ran without a limit there. `/upload` and `/nodes` end after 100 s
      (`METADATA_AGENT_UPLOAD_TIMEOUT`).
- [ ] **Log filters:** the loggers are named after the new modules
      (`src.api.responses`, `src.api.routers.*`, `src.services.llm_transport`).
      Adapt filters on `logger="src.main"`.
- [ ] **Behind TLS:** set `forwardedAllowIps` (Helm) or `FORWARDED_ALLOW_IPS`
      (Docker), see [Behind a TLS proxy](#behind-a-tls-proxy).
- [ ] **Docker Compose:** the port is published on `127.0.0.1` only, the
      container runs read-only, and the stop timeout is 320 s. Whoever has to
      reach the agent from other hosts sets `"8000:8000"`, only behind an
      access restriction. An existing `.env` now reaches the container
      completely (Docker Compose 2.24 or newer).

Changed without anything to do: the root file system is read-only (`/tmp` and
`/home/appuser` are `emptyDir` volumes with a size limit), the pod gets no
service account token, the replicas are spread over nodes, and the base images
are pinned by digest. The source text of an extraction is cut to
150,000 characters; `processing.warnings` then names the original length.

### Older versions

Update from 2.0.x to 2.1.x first; the notes are in
[docs/update-2.0-auf-2.1.md](docs/update-2.0-auf-2.1.md) (German).
What still matters for operation is described above: the forwarded
`Authorization` header and mode `static`
([Standalone](#standalone)), the request size limit and the probe on `/ready`.
Two changes of that update are easy to miss: failures come as HTTP errors
(`502`, `503`, `504` with `error.code`) instead of `200` with empty fields, and
an unknown value of `METADATA_AGENT_LLM_PROVIDER` keeps the agent from starting.

---

## Rollback

A rollback only swaps image and manifests. Nodes that `/upload` or `/nodes`
have already written stay in the repository. The web component is part of the
image and goes back with it.

- **Helm:**
  ```bash
  helm history <release> -n <ns>
  helm rollback <release> <REVISION> -n <ns> --wait
  ```
  Helm restores the manifests and values of the chosen revision. If that
  revision had no NetworkPolicy (chart 0.2.x), the policy is removed. A Secret
  from `secret.existingSecret` stays unchanged.
- **Only the image back:**
  ```bash
  helm upgrade <release> helm/metadata-agent -n <ns> \
    --reuse-values --set image.tag=<previous tag> --wait
  ```
  **Not to an image up to the released 3.0.1.** The current chart leaves the
  address of the text extraction empty, and only 4.0.0 and later read
  "empty" as "through the B-API". An older image then calls `/from-url` without
  an address, and extraction from URLs fails. Use `helm rollback` for that (it
  also restores the old values), or add
  `--set env.METADATA_AGENT_TEXT_EXTRACTION_API_URL=<address>`.
- **Docker / Compose:** set the previous image tag and recreate the container.
- **Clients:** whoever adapted clients to the status codes of 3.0.0 checks the
  table "Breaking" in the CHANGELOG before going back to 2.1.x.
- **Back to 2.0.x:** set the image tag back. 2.0.x ignores settings it does not
  know, also in a `.env` file.
