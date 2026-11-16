# Environment variables

**English** · [Deutsch](ENV-PARAMETER.de.md)

The complete reference of every environment variable the Metadata Agent API
reads, for version 4.0.3; the changes since 3.0.1 are listed in
[CHANGELOG.md](CHANGELOG.md) (German). The source is `src/config.py`.
`tests/test_env_parameters.py` compares this document with the settings model:
no variable is missing here, and none is listed that the service does not read.

## Contents

1. [How configuration works](#how-configuration-works)
2. [Keys and credentials](#keys-and-credentials)
3. [LLM provider and model](#llm-provider-and-model)
4. [B-API: authentication](#b-api-authentication)
5. [B-API: routing](#b-api-routing)
6. [Text extraction and geocoder](#text-extraction-and-geocoder)
7. [Repository](#repository)
8. [Throughput](#throughput)
9. [Limits and timeouts](#limits-and-timeouts)
10. [Screenshots](#screenshots)
11. [Logging and tracing](#logging-and-tracing)
12. [Ports, CORS and debug endpoints](#ports-cors-and-debug-endpoints)
13. [Other settings](#other-settings)
14. [Ready-made configurations](#ready-made-configurations)
15. [Helm chart](#helm-chart)
16. [Related documents](#related-documents)

---

## How configuration works

### Names and sources

Every setting is one environment variable. All names carry the prefix
`METADATA_AGENT_`, except the four keys and credentials: `B_API_KEY`,
`OPENAI_API_KEY`, `WLO_GUEST_USERNAME` and `WLO_GUEST_PASSWORD`. The service
also reads these four with the prefix; if both spellings are set, the one
without prefix wins. Use the spelling without prefix, as `.env.template`,
Docker Compose and the Helm chart do.

The service takes a value from the first place that has one:

1. the environment of the process (Docker `-e`, Compose `environment`,
   Kubernetes `env`),
2. a file `.env` in the working directory (UTF-8; copy `.env.template`),
3. the default in the code.

- The Docker image contains no `.env`. In a container only the environment
  counts. `docker-compose.yml` passes a `.env` next to it into the container.
- Settings are read once at start. A change needs a restart.
- Unknown variables are ignored. An old `.env` breaks nothing, but a typo in a
  name goes unnoticed. Check the start log.

### Empty values

A variable that is set but empty counts as not set: the default applies. This
holds for `VAR=` in a `.env` file and for `""` in the chart values. An empty
variable in the environment also hides a value from `.env`.

Three settings are the exception. For them, empty is itself the setting:

| Variable | Empty means |
|---|---|
| `METADATA_AGENT_LLM_VERBOSITY` | The parameter is not sent |
| `METADATA_AGENT_LLM_REASONING_EFFORT` | The parameter is not sent |
| `METADATA_AGENT_CORS_ORIGINS` | No origin is allowed |

### Value formats

- **Choices** (provider, auth mode, log level and so on) ignore upper and lower
  case and surrounding spaces.
- **Switches** take `true` or `false` (also `1`/`0`, `yes`/`no`, `on`/`off`).
- **URLs** are brought into one form: spaces around them and a trailing slash
  are removed, scheme and host are written in lower case, and the default port
  of the scheme (`:80`, `:443`) is dropped. This applies to
  `METADATA_AGENT_B_API_BASE_URL`, the three `…_BASE` variables of the B-API,
  `METADATA_AGENT_OPENAI_API_BASE`, `METADATA_AGENT_REPOSITORY_URL` and
  `METADATA_AGENT_TEXT_EXTRACTION_API_URL`.
- **Invalid values** stop the service at start with a validation error: an
  unknown choice, a number outside its range, text where a number belongs, and
  a `METADATA_AGENT_ALLOWED_LLM_MODELS` that names no model. The tables below
  give the range of each setting. One exception:
  `METADATA_AGENT_DEFAULT_MAX_WORKERS` is clamped to its range.

### Derived values

Some values are computed from others. Set them only to deviate from the rule.

| Value | Rule |
|---|---|
| LLM endpoints of the B-API | `<METADATA_AGENT_B_API_BASE_URL>/api/v1/llm/openai`, `…/academiccloud`, `…/router`, unless the matching `…_BASE` variable is set. `/chat/completions` is appended to each |
| Text extraction address | Chosen by `METADATA_AGENT_B_API_SERVICES`, unless `METADATA_AGENT_TEXT_EXTRACTION_API_URL` is set; see [Text extraction and geocoder](#text-extraction-and-geocoder) |
| Geocoder address | Chosen by `METADATA_AGENT_B_API_SERVICES`; no variable of its own |
| Temperature for provider `openai` | `METADATA_AGENT_OPENAI_TEMPERATURE`, else `METADATA_AGENT_LLM_TEMPERATURE` |
| Throughput limits | Default of the provider, unless set; see [Throughput](#throughput) |
| Workers in effect | The smaller of `METADATA_AGENT_DEFAULT_MAX_WORKERS` and the limit of requests in flight |

### Start log

At start the service prints what applies, including the derived values. It
never prints a key, only whether `B_API_KEY` is set (`gesetzt` or
`nicht gesetzt`). A user name and password inside a URL setting
(`https://user:password@host`) are left out of the address lines.

```text
Starting Metadata Agent API v4.0.3
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

Without a rate limit the throughput line ends with `no rate cap`. The last line
appears only with `METADATA_AGENT_INTERNAL_PORT`.

### Start warnings

Settings that cannot work together are reported at start, between the address
lines and the worker line. The lines begin with `⚠️` and are written in German.
`GET /debug/dependencies` returns the same list as `config_warnings`.

| Situation | What the warning says |
|---|---|
| B-API provider, mode `static`, no `B_API_KEY` | Every LLM call fails |
| B-API provider, mode `forward`, `B_API_KEY` set | The key is never used |
| B-API provider, mode `auto`, no `B_API_KEY` | Only calls through the B-API proxy with a forwarded key succeed |
| Provider `openai`, no `OPENAI_API_KEY` | Every LLM call fails, and `/ready` answers `503` |
| Provider `openai`, `METADATA_AGENT_B_API_SERVICES=proxy`, no `B_API_KEY` | Mode `static`: text extraction and geocoder through the B-API fail. Mode `auto`: they succeed only for calls with a forwarded key |
| `METADATA_AGENT_DEBUG_ENDPOINTS=true` | `/debug/auth` and `/debug/dependencies` answer anyone who reaches the service; switch them off after setup or debugging |
| Provider `b-api-router`, a route name (no `/`) as model, `METADATA_AGENT_LLM_REQUEST_STYLE=auto` | The name does not tell which parameters the models take; the classic request body is sent |
| `WLO_GUEST_USERNAME` or `WLO_GUEST_PASSWORD` missing | `/upload` and `/nodes` answer `503`; `/ready` answers `503` too, unless `METADATA_AGENT_REQUIRE_REPOSITORY=false` |
| `METADATA_AGENT_UPLOAD_TIMEOUT` plus 15 s, or `METADATA_AGENT_GENERATE_TIMEOUT` plus 10 s, is above 320 s (the limit itself above 305 or 310). With `METADATA_AGENT_GRACE_PERIOD_SECONDS` set, that value replaces the 320 s | With the time needed afterwards (cleanup, preStop), the limit exceeds the 320 s of ingress and grace period; the proxy cuts the request first. Measured against `METADATA_AGENT_GRACE_PERIOD_SECONDS`, the warning names that value: the request is cut when the pod is stopped |
| B-API provider whose LLM address is not below `METADATA_AGENT_B_API_BASE_URL`, and `METADATA_AGENT_B_API_SERVICES` is not `direct` | `auto`: text extraction and geocoder are called directly, not through the B-API. `proxy`: they still go to `METADATA_AGENT_B_API_BASE_URL`, with the key of the LLM calls |
| No text extraction address can be derived | Extraction from a URL fails until `METADATA_AGENT_TEXT_EXTRACTION_API_URL` is set |
| The addresses of B-API, repository and text extraction point to different environments (staging and production of OpenEduHub) | Lists which address belongs to which environment |

### Readiness

`/ready` answers `503` while the configuration cannot work at all. It checks
the configuration only and calls no external service.

- No schema context could be loaded.
- B-API provider in mode `static` without `B_API_KEY`.
- Provider `openai` without `OPENAI_API_KEY`.
- `WLO_GUEST_USERNAME` or `WLO_GUEST_PASSWORD` is missing and
  `METADATA_AGENT_REQUIRE_REPOSITORY` is `true`.

The reasons are written to the log and returned by `/ready` on the internal
port; the public port names none. A missing `B_API_KEY` in mode `auto` is no
reason: behind the B-API proxy every call brings its own key.

---

## Keys and credentials

These four variables carry no prefix. Leave them empty until you have the real
value: `/ready` only checks that a value is set, so a placeholder reports the
service as ready and fails on the first call.

| Variable | Default | Meaning and use |
|---|---|---|
| `B_API_KEY` | empty | The service's own B-API key, sent as `X-API-KEY`. Used with the providers `b-api-openai`, `b-api-academiccloud` and `b-api-router` for the LLM calls and for text extraction and geocoder through the B-API. Required in mode `static`, the fallback in mode `auto`, unused in mode `forward`; see [B-API: authentication](#b-api-authentication) |
| `OPENAI_API_KEY` | empty | Key for provider `openai`, sent as `Authorization: Bearer` to `METADATA_AGENT_OPENAI_API_BASE` |
| `WLO_GUEST_USERNAME` | empty | User of the repository account. Needed for `/upload` and `/nodes`; node reads use it too and fall back to anonymous access without it |
| `WLO_GUEST_PASSWORD` | empty | Password of the repository account |

A key used for text extraction and geocoder through the B-API needs the rights
for the proxy services `text-extraction` and `photon_geocoding`. This applies
to `B_API_KEY` and to every forwarded key.

---

## LLM provider and model

### Provider

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | `b-api-openai`, `b-api-academiccloud`, `b-api-router`, `openai` | Which LLM access the service uses; see the table below |
| `METADATA_AGENT_ALLOWED_LLM_MODELS` | empty | Model names, comma-separated | Models a request may name in `llm_model`. Empty: any model. A value that names no model (only commas and spaces) stops the start. Set it wherever calls run on the service's own key |

| Provider | Endpoint | Model variable | Key |
|---|---|---|---|
| `b-api-openai` | OpenAI models through the B-API | `METADATA_AGENT_B_API_OPENAI_MODEL` | B-API key |
| `b-api-academiccloud` | AcademicCloud (GWDG) models through the B-API | `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | B-API key |
| `b-api-router` | A route of the B-API, or one model as `provider/model`; see [B-API: routing](#b-api-routing) | `METADATA_AGENT_B_API_ROUTER_MODEL` | B-API key |
| `openai` | OpenAI or any OpenAI-compatible endpoint, without B-API | `METADATA_AGENT_OPENAI_MODEL` | `OPENAI_API_KEY` |

A request to `/generate`, `/detect-content-type` or `/extract-field` may name
its own `llm_provider` and `llm_model`.

- `llm_provider: "openai"` is refused with `422` unless the service itself runs
  with provider `openai`.
- A model outside `METADATA_AGENT_ALLOWED_LLM_MODELS` is refused with `422`.
  The configured default model of the provider is always allowed.
- A model chosen this way runs on the service's key wherever no caller key is
  forwarded (mode `static`, mode `auto` without header).

### Endpoints and models

| Variable | Default | Meaning and use |
|---|---|---|
| `METADATA_AGENT_B_API_BASE_URL` | `https://b-api.staging.openeduhub.net` | Address of the B-API. The LLM endpoints and the paths for text extraction and geocoder are derived from it. Production: `https://b-api.prod.openeduhub.net` |
| `METADATA_AGENT_B_API_OPENAI_BASE` | empty (derived: `…/api/v1/llm/openai`) | Set only if the path differs from the scheme |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-6-luna` | Model for `b-api-openai` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | empty (derived: `…/api/v1/llm/academiccloud`) | Set only if the path differs from the scheme |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-v4-flash` | Model for `b-api-academiccloud` |
| `METADATA_AGENT_B_API_ROUTER_BASE` | empty (derived: `…/api/v1/llm/router`) | Set only if the path differs from the scheme |
| `METADATA_AGENT_B_API_ROUTER_MODEL` | `openai/gpt-6-luna` | Route name, or `provider/model`, for `b-api-router` |
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` | Endpoint for provider `openai`. Anything that serves `/chat/completions` and takes a Bearer token works: an Azure deployment, a self-hosted vLLM, a gateway |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` | Model for `openai` |

If you set one of the three `…_BASE` variables to an address outside
`METADATA_AGENT_B_API_BASE_URL`, text extraction and geocoder are no longer
called through the B-API in mode `auto`, so the key never reaches a second
B-API. The service warns at start.

### Request parameters

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_LLM_TEMPERATURE` | `0.3` | Number from `0` to `2` | Temperature for all providers. Sent with the classic request body only; reasoning models get none |
| `METADATA_AGENT_OPENAI_TEMPERATURE` | empty (follows `METADATA_AGENT_LLM_TEMPERATURE`) | Number from `0` to `2` | Only if provider `openai` should use a different temperature |
| `METADATA_AGENT_LLM_MAX_TOKENS` | `2000` | Integer, at least `1` | Token limit of one answer. Sent as `max_completion_tokens` to reasoning models, as `max_tokens` to all others |
| `METADATA_AGENT_LLM_VERBOSITY` | `low` | `low`, `medium`, `high`; empty: not sent | `verbosity`, sent to reasoning models only. Any other value stops the start |
| `METADATA_AGENT_LLM_REASONING_EFFORT` | `low` | `none`, `low`, `medium`, `high`; empty: not sent | `reasoning_effort`, sent to reasoning models only. Any other value stops the start, also `minimal`, which the API rejects. `none` was about 40 % faster in a test but lost event dates |
| `METADATA_AGENT_B_API_IGNORE_CACHE` | `false` | `true`, `false` | `true` appends `?ignore-caching=true` to every LLM call to the B-API. The B-API answers word-identical requests from its cache without marking them; with `true` every call is new and costs each time |
| `METADATA_AGENT_LLM_CACHE_BREAKPOINT` | `auto` | `auto`, `on`, `off` | Explicit breakpoint of the OpenAI prompt cache after the page text (`prompt_cache_options`, `prompt_cache_breakpoint`), for `b-api-openai`, `openai` and the `openai/…` models of `b-api-router`. With it the field calls only read the text from the cache; without it each one also writes its field instruction to the cache, at 1.25 times the input price and for no later call to read. `auto`: for `gpt-6…` models only; other models answer `400` to the parameter (measured 2026-11-15: `gpt-4.1-mini`, `gpt-5-mini`, `gpt-5.4-mini`). `on`: for every model of these providers, for a model family the rule does not know yet. `off`: never, the request body of before. The price of the breakpoint: the same text extracted again within the lifetime of the cache costs as much as the first time (measured 0.0065 $ instead of 0.0018 $), because the field instructions are no longer in the cache. Through the B-API a word-identical repetition is answered from the B-API's own cache anyway; for provider `openai` with many repeated extractions of the same text `off` is cheaper |

**Reasoning models** are recognized by the model name: it starts with `gpt-5`,
`gpt-6`, `o1`, `o3` or `o4`, also behind a provider prefix such as `openai/`.
They get `max_completion_tokens`, `verbosity` and `reasoning_effort`, and no
temperature. All other models get `max_tokens` and `temperature` (the classic
body); they answer `400` to the reasoning parameters. This holds for every
provider. Only for `b-api-router` can `METADATA_AGENT_LLM_REQUEST_STYLE` fix
the body.

### Retries

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_LLM_MAX_RETRIES` | `3` | Integer, at least `1` | Attempts per LLM call (`1`: no repetition). Also the number of extra calls an answer outside a closed vocabulary may cost. A required field without a value costs exactly one more call, whatever this setting says, and none with a closed vocabulary |
| `METADATA_AGENT_LLM_RETRY_DELAY` | `1.0` | Seconds, at least `0` | Base of the wait before a repetition: the wait grows linearly with the attempt. After a `429` it is at least the `Retry-After` of the answer, at most 30 s |

- Repeated: `429`, `500`, `502`, `503`, `504`, a `200` without a JSON object,
  and connection errors.
- A call that got no answer within `METADATA_AGENT_REQUEST_TIMEOUT` is sent
  twice at most.
- Never repeated: every other status. A refused key (`401`, `403`) or an
  unknown model (`404`) ends the whole extraction at once.

---

## B-API: authentication

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` | `auto`, `forward`, `static` | Which key the calls to the B-API carry: LLM calls, text extraction and geocoder |

The B-API can call a registered service through `/api/v1/proxy/<service>/…` and
pass on the caller's key as `Authorization: Bearer <key>` (admin option to
forward the caller's API key, `forwardApiKey`). The current B-API forwards a
delegation token with the rights in `delegationScopes` instead
(`forwardDelegation`); for the agent it is the same header. The proxy entry of this service
is called `metadata-agent-canvas`. edu-sharing picks the key, one for logged-in
users and one for guests, and the B-API books usage, cost and limits on it. The
other way is the service's own `B_API_KEY`, sent as `X-API-KEY`.

| Mode | Incoming `Authorization: Bearer …` | No header |
|---|---|---|
| `auto` | Passed on unchanged | `B_API_KEY` as `X-API-KEY` |
| `forward` | Passed on unchanged | `502` with code `LLM_AUTH_MISSING`, no fallback |
| `static` | Ignored | `B_API_KEY` as `X-API-KEY` |

The default `auto` needs no setting: a deployment with `B_API_KEY` works, and
as soon as the proxy forwards a key, that key is used. Set the mode in two
cases:

- `static`, if a proxy in front sends an `Authorization` header that is not a
  B-API key.
- `forward`, if everything is to be billed to the callers' keys. Then set no
  `B_API_KEY`.

What to know:

- Only `Authorization` with scheme `Bearer` is read; an incoming `X-API-KEY` is
  not used. The value goes to the B-API unchanged and as the only auth header.
- The key is valid only for the request that brought it. It is not stored, not
  logged and not shared between concurrent requests.
- The mode applies to all three B-API providers. Provider `openai` uses
  `OPENAI_API_KEY`.
- The answer of `/generate` names the source of the key in
  `processing.auth_source` (`forwarded` or `static`), never the key.
- The service does not check the header itself. It must be reachable **only
  through the B-API** (network rule). Otherwise anyone can send calls with any
  B-API key, and in mode `auto` a call without header runs on `B_API_KEY`.

To check what arrives, use the [debug endpoints](#debug-endpoints).

---

## B-API: routing

With `METADATA_AGENT_LLM_PROVIDER=b-api-router` the LLM calls go to
`/api/v1/llm/router/chat/completions`. The `model` field then names a route.
The B-API picks a model of the route by priority and weight and moves on to the
next one on errors.

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_LLM_REQUEST_STYLE` | `auto` | `auto`, `reasoning`, `classic` | Request body of the router calls. `auto`: by model name; a bare route name counts as classic. `reasoning`: for routes of GPT-5/6 and o-models. `classic`: for all others. Only for `b-api-router` |
| `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT` | empty (not sent) | empty, `none`, `low`, `medium`, `high` | `reasoning_effort` in the classic body of the router calls. Thinking models such as `qwen3.6-35b-a3b` need `none`, or they return no text. Older models such as `gpt-4o` reject the parameter |

The route name or `provider/model` is set with
`METADATA_AGENT_B_API_ROUTER_MODEL`; see
[Endpoints and models](#endpoints-and-models).

### Where routes are defined

**Routes are defined in the B-API, not in the environment.** An administrator
creates a global route there (page "LLM-Routing" or
`POST /administration/llm-routing/groups`). An account with `LLM_ROUTE_MANAGE`
can create a route of its own (`POST /api/v1/llm/router/routes`). The route
sets models, priority and weight. The service only names the route and the
request style of its models. Changing a model in the route therefore needs no
new deployment.

There are two ways to use the router:

- **One model, no route.** Set the model as `provider/model`. The B-API
  resolves it without a route and does not move on to other models.
- **A route with a reserve.** The administrator first creates the route, with
  models of **one** family and `maxAttempts` of at least 2. Then set the route
  name in the service.

The example shows the structure of a route only:

```json
{"logicalModel": "metadata-agent",
 "description": "Metadata Agent: Qwen 3, reserve qwen3.8",
 "deployments": [
   {"id": "qwen36", "providerId": "academiccloud", "upstreamModel": "qwen3.6-35b-a3b", "priorityTier": 0, "weight": 2},
   {"id": "qwen30", "providerId": "academiccloud", "upstreamModel": "qwen3-30b-a3b-instruct-2507", "priorityTier": 0, "weight": 1},
   {"id": "qwen38", "providerId": "academiccloud", "upstreamModel": "qwen3.8-27b", "priorityTier": 1, "weight": 1}
 ],
 "maxAttempts": 2}
```

The variables for both ways are in
[Ready-made configurations](#ready-made-configurations).

### What the service does

- **Switching without downtime.** The default `openai/gpt-6-luna` works without
  a route. Create the route first, then set
  `METADATA_AGENT_B_API_ROUTER_MODEL` to its name. An unknown name fails at
  once with `LLM_ROUTE_NOT_FOUND`.
- **Request style.** The router passes the body unchanged to every model of
  the route, so a route may hold models of one family only. A route name does
  not reveal the family: with `auto` the service sends the classic body and
  warns at start. If the body does not fit, the extraction ends at once with
  `LLM_ROUTE_MISCONFIGURED`.
- **Thinking models.** Without
  `METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none`, `qwen3.6-35b-a3b` spends
  the token budget on thinking and returns no text; the service reports
  `LLM_EMPTY_ANSWER`. Instruct models such as `qwen3-30b-a3b-instruct-2507`
  accept the parameter as well. `qwen3.8-27b` has not been tested in the
  service. Test results: [UEBERGABE.md](UEBERGABE.md) (German).
- **Errors.** Lasting errors are not repeated and end the extraction at once:
  an unknown or disabled route (`LLM_ROUTE_NOT_FOUND`); a model without price,
  a model not eligible for the endpoint, or the wrong request style
  (`LLM_ROUTE_MISCONFIGURED`). The B-API reports some of them as `503`, so the
  service reads the error text. Repeated are throttling by GWDG
  (`503 … RATE_LIMITED`; if it lasts: `LLM_RATE_LIMITED`) and a route without
  a free model (`no deployment left`; if it lasts: `LLM_UNREACHABLE`).
- **Cache.** Every route has its own cache in the B-API, and changing the route
  does not clear it. After changing a model, clear the cache in the B-API.
  `METADATA_AGENT_B_API_IGNORE_CACHE=true` bypasses the cache.
- **Throughput.** The service cannot see which models a route holds. The
  default limits match `openai/gpt-6-luna`; a route of AcademicCloud models
  needs the lower values, see [Throughput](#throughput).
- **Check.** With a route name, `GET /debug/dependencies` also reports
  `route_listed`: whether the B-API lists the route.

---

## Text extraction and geocoder

The service needs two more services: the text extraction for input from a URL,
and the geocoder for coordinates of places. Both exist once per environment.
Through the B-API they are always the ones that belong to that B-API.

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_B_API_SERVICES` | `auto` | `auto`, `proxy`, `direct` | How text extraction and geocoder are reached. `auto`: through the B-API if the provider is a B-API provider and its LLM calls go to `METADATA_AGENT_B_API_BASE_URL`, else directly. `proxy`: always through the B-API. `direct`: never through the B-API |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | empty (chosen by `METADATA_AGENT_B_API_SERVICES`) | URL | Names a text extraction yourself, for example the service inside the cluster. Required if no address can be derived: no B-API and a repository that is neither OpenEduHub staging nor production |

| Way | Text extraction | Geocoder |
|---|---|---|
| Through the B-API | `<B-API>/api/v1/proxy/text-extraction` | `<B-API>/api/v1/proxy/photon_geocoding/api` |
| Directly | By the repository address: `https://text-extraction.staging.openeduhub.net` for OpenEduHub staging, `https://text-extraction.prod.openeduhub.net` for production, none for any other repository | `https://photon.komoot.io/api` (Photon, a third party) |

- **Key.** A call to an address below `METADATA_AGENT_B_API_BASE_URL` first
  carries the same credential as the LLM calls (the forwarded token or the
  caller's key), after a refusal the own `B_API_KEY`. Any other address never
  gets a key. This also holds for an address set in
  `METADATA_AGENT_TEXT_EXTRACTION_API_URL`.
- **Way out with `auto`.** If the B-API refuses both (`401`, `403`, `429`) or
  gives no usable answer, the agent asks the service directly, as before
  4.0.0: Photon, or the text extraction of the repository's environment. A
  refused way is skipped by the process for 10 minutes. Details in
  DEPLOYMENT.md, "How text extraction and geocoder are reached".
- **Rights.** The credential needs the proxy services `text-extraction` and
  `photon_geocoding`. If every way fails, extraction from a URL answers `502`
  `TEXT_EXTRACTION_AUTH_REFUSED`, and places stay without coordinates (noted
  in `processing.warnings`). Without any credential and without a direct way
  (`proxy`, or a repository outside staging and production) the code is
  `TEXT_EXTRACTION_AUTH_MISSING` (`502`).
- **No address.** Without a text extraction address, extraction from a URL
  answers `503` `TEXT_EXTRACTION_NOT_CONFIGURED`. The service warns at start.
- **Environment of the repository.** Only hosts of `openeduhub.net` are
  recognized: a host with `staging` in its name is staging; a host with
  `.prod.` in its name, or `redaktion.openeduhub.net`, is production.

Changed in 4.0.0: `METADATA_AGENT_B_API_SERVICES` is new, and
the default of `METADATA_AGENT_TEXT_EXTRACTION_API_URL` is now empty. Both
services go through the B-API by default, so the keys need the two proxy
rights. `METADATA_AGENT_B_API_SERVICES=direct` or a set address keeps the
earlier way.

---

## Repository

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_REPOSITORY_URL` | `https://repository.staging.openeduhub.net/edu-sharing/rest` | URL | REST address of the edu-sharing repository, for reading nodes **and** for writing. With or without `/rest` at the end; both mean the same. Production: `https://redaktion.openeduhub.net/edu-sharing/rest` |
| `METADATA_AGENT_WLO_INBOX_ID` | `21144164-30c0-4c01-ae16-264452197063` | Node ID | Folder in which new nodes are created |
| `METADATA_AGENT_REQUIRE_REPOSITORY` | `true` | `true`, `false` | Whether `/ready` requires `WLO_GUEST_USERNAME` and `WLO_GUEST_PASSWORD`. `false` for a deployment that only extracts: it becomes ready without them, `/upload` and `/nodes` answer `503`, and the start log warns |

The account is set with `WLO_GUEST_USERNAME` and `WLO_GUEST_PASSWORD`; see
[Keys and credentials](#keys-and-credentials). There is no second switch for
the target repository: the `repository` parameter of a request is ignored.

---

## Throughput

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` | empty (default of the provider) | Integer, at least `1` | LLM requests in flight at once |
| `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` | empty (default of the provider) | Number, at least `0`; `0`: no rate limit | LLM requests per second |
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `20` | 1 to 20; other values are clamped | Parallel field extractions of a `/generate` without `max_workers`. A `max_workers` in the request takes precedence. Measured 2026-11-15 with `b-api-openai` (47 fields, median of six runs): 10 workers 7.8 s, 20 workers 5.2 s |
| `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` | empty (no limit) | Integer, at least `1` | `/generate` runs one instance takes on at once. One more gets `503` `TOO_MANY_GENERATIONS` with `Retry-After` at once, instead of sharing the LLM budget with the running ones until all reach the time limit. The right number comes from a load test |

Defaults of the providers:

| Provider | In flight | Per second |
|---|---:|---:|
| `b-api-openai` | 20 | no limit |
| `b-api-academiccloud` | 2 | 2 |
| `b-api-router` | 20 | no limit |
| `openai` | 20 | no limit |

- Both LLM limits hold for the **whole process**, not per request, and for
  each provider separately. A retry is a request of its own.
- A set value applies to **every** provider. A value above 2 therefore also
  lifts AcademicCloud above its measured limit.
- The workers are capped by the limit of requests in flight. With AcademicCloud
  2 workers run, whatever is set; asking for more only queues them. The start
  log shows the number in effect.
- With several instances each one applies the limits by itself. Divide a limit
  shared by all instances by their number. The Helm chart does this with
  `sharedLlmLimit`, see [Helm chart](#helm-chart).

**AcademicCloud (GWDG)** has two measured limits. Both hold for the B-API key,
not for a process:

- Two requests in flight (measured 2026-08-12). A third parallel request is
  refused with `429` at once and **without `retry-after`**, so a client must
  stay below the limit. More load does not help: at 3 requests per second the
  effective throughput fell below the one at 2.
- About 10 calls per minute and about 200 per hour (measured 2026-10-15 through
  the router with Qwen models). For a whole `/generate` with about 50 calls
  this is the tighter limit. It has not been measured for `deepseek-v4-flash`
  through `b-api-academiccloud`.

The defaults cover the first limit only. For the second, set
`METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16` (about 10 per minute). This
also applies to a `b-api-router` route of AcademicCloud models. At that rate a
`/generate` with about 50 calls takes about 5 minutes and ends at
`METADATA_AGENT_GENERATE_TIMEOUT` with a partial result.

**`b-api-openai`:** a real `/generate` run on 2026-10-06 with `gpt-6-luna` and
20 requests in flight finished without a `429`. On 2026-11-15, 60 and 100 calls
at the same moment were all answered `200` as well, 60 also through
`b-api-router`. The limit stays at 20 all the same: 60 workers took 4.6 s where
20 take 5.2 s, and half a second is not worth tripling the load on a shared
key before it is tried in production. If the gateway answers `429`, lower
`METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS`. With `gpt-6-luna` the extraction
of a web page took 12 to 22 s (measured 2026-10-16). A text of 4,400
characters took 9.3 s before the changes of 2026-11-15 and takes 5.2 s since
(median of six runs): the cache primer now runs alongside the detection of the
content type.

---

## Limits and timeouts

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_REQUEST_TIMEOUT` | `90` | Seconds, integer, at least `1` | Read timeout of one LLM call. Connecting has its own 10 s |
| `METADATA_AGENT_GENERATE_TIMEOUT` | `300` | Seconds, above 0 | Time limit of a whole `/generate`: fetching the source, all field calls, repetitions, geocoding. Also for `/detect-content-type` and `/extract-field` |
| `METADATA_AGENT_UPLOAD_TIMEOUT` | `100` | Seconds, above 0 | Time limit of a write: `/upload`, `/nodes`, `/node`, `/workflow/{node_id}` and `/screenshot` with a node |
| `METADATA_AGENT_MAX_REQUEST_BYTES` | `20971520` (20 MB) | Bytes, at least `1024` | Largest request body. A larger one gets `413` before it is read. The default leaves room for `/upload` with a preview image as data URL |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS` | empty (320 s are assumed) | Seconds, integer, at least `1` | Time the platform gives the process after SIGTERM, if it is not 320 s. Only the start warnings use it: they compare `METADATA_AGENT_GENERATE_TIMEOUT` and `METADATA_AGENT_UPLOAD_TIMEOUT` with it. It changes nothing else. The Helm chart sets it from `terminationGracePeriodSeconds`; with Docker, set it to the value of `--stop-timeout` or `stop_grace_period` |

The time limits build on each other: one LLM call (90 s) stays below a whole
`/generate` (300 s), and that stays below the 320 s of ingress and grace
period.

- **`/generate` at the limit.** Field calls still running 5 s before the limit
  are ended. What has been extracted so far comes back as `partial`; the late
  fields are listed in `processing.errors`. If the limit is reached all the
  same, for example while the source is still being fetched, the answer is
  `504` with code `GENERATION_TIMEOUT` and the request ID, and the running
  calls are cancelled.
- **A proxy in front needs at least about 310 s.** This applies to a B-API
  proxy and to edu-sharing; otherwise they cut long runs first. Above 310 the
  service warns at start: with 10 s for the answer and preStop the limit
  exceeds 320 s.
- **A write at the limit.** The answer is `504` with code `UPLOAD_TIMEOUT` and
  says what happened to the node: `discarded_node` (discarded, in the trash),
  or `node` if it could not be discarded, is already in the workflow, or all
  write steps have run. A new node is also kept, and named as `node`, once
  linking it to collections has begun; `collections` then lists the links
  made. An existing node that was updated may be partly written. The limit
  plus 5 s search, 5 s discard and 5 s preStop stays below 320 s; above 305
  the service warns at start.
- **Another grace period.** Both warnings compare with
  `METADATA_AGENT_GRACE_PERIOD_SECONDS` instead of 320 s if it is set.
- **Source text.** The source text is cut to 150,000 characters;
  `processing.warnings` names the original length. This limit is fixed.
- **`/validate` and `/export/markdown`.** Their request body is limited to
  2 MiB, whatever `METADATA_AGENT_MAX_REQUEST_BYTES` says; a larger one gets
  `413`. This limit is fixed.

Changed in 4.0.0: `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS`
and `METADATA_AGENT_GRACE_PERIOD_SECONDS` are new, a run at the limit returns a
partial result instead of `504`, and a read timeout is repeated once only.
Values outside the ranges in the tables now stop the start.

---

## Screenshots

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_SCREENSHOT_WIDTH` | `1920` | Pixels, `320` to `3840` | Width of the browser window |
| `METADATA_AGENT_SCREENSHOT_HEIGHT` | `1080` | Pixels, `200` to `2160` | Height of the browser window |
| `METADATA_AGENT_SCREENSHOT_FORMAT` | `png` | `png`, `jpeg` | Image format. Any other value (for example `webp`) is delivered as PNG |
| `METADATA_AGENT_SCREENSHOT_FULL_PAGE` | `false` | `true`, `false` | `true`: the whole scrollable page instead of the window |
| `METADATA_AGENT_SCREENSHOT_DELAY` | `2000` | Milliseconds, `0` to `10000` | Wait before the capture |

- **Where they apply.** Width, height, format, full page and delay apply to
  the preview images that `/generate` and `/upload` capture. `/screenshot`
  takes all values from its request; the defaults and ranges of that request
  are the same numbers and are not changed by these variables.
- **Method.** No setting chooses the capture method: a request names it
  (`playwright`, the default, or the legacy alias `pageshot`; both capture
  locally).
- **Chromium.** Screenshots are always taken locally with the Chromium in the
  Docker image. The service uses no external screenshot service.
- **Targets.** Only public http(s) targets are captured. The check resolves DNS
  inside the container: a host name with a private address (for example
  split-horizon DNS in the cluster) is refused.

---

## Logging and tracing

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Threshold of the log |
| `METADATA_AGENT_LOG_FORMAT` | `text` | `text`, `json` | `text`: the message alone, one line each. `json`: one object per line, for a log collector; also used for the server log of uvicorn |
| `METADATA_AGENT_SERVICE_NAME` | `metadata-agent` | Text | Field `service` of every JSON log line: the service ID under which operations runs the service (chart, release, dashboards). The proxy entry of the B-API has a different name, `metadata-agent-canvas` |

Logs always go to stdout.

- **JSON fields.** `timestamp` (UTC), `level`, `service`, `logger`, `message`,
  `trace_id`, `span_id` and `request_id`; also `client_trace_id` if the caller
  sends `x-client-trace-id`.
- **One line per request** with method, path, status and duration; in JSON
  also as fields `method`, `path`, `status` and `duration_ms`. The line has no
  query string and no headers, and the path is cut after 2,000 characters.
  `/health` and `/ready` are logged only with
  `METADATA_AGENT_LOG_LEVEL=DEBUG`. The caller's address is left out on
  purpose: behind the proxy it would be the address of the user. The access
  log of uvicorn is off.
- **Text lines.** In format `text` a message stays on one line, and control
  characters from request values are removed. Python warnings of libraries
  appear as log lines of the logger `py.warnings`.
- **IDs.** `trace_id` and `span_id` are the B3 values. `request_id` is
  `x-client-trace-id`, else `x-request-id`, else the trace ID; an incoming
  value counts only with at most 64 characters of letters, digits, `.`, `_`
  and `-`. The same value is in the response header `X-Request-ID` and in
  error answers (`error.request_id`).
- **Tracing headers.** The service passes incoming `x-client-trace-id`,
  `x-request-id`, the B3 headers `x-b3-*` and `b3` unchanged to its own
  services: the B-API and the repository, including text extraction and
  geocoder called through the B-API. It never passes them to third parties
  such as Photon. A value longer than 128 characters or with other characters
  is dropped. An `x-request-id` that cannot be the request ID (65 to 128
  characters) is not passed on either; the request ID goes out in its place.
  Without a valid B3 trace ID (16 or 32 hex characters, span
  16) it creates one (`x-b3-traceid`, `x-b3-spanid`) and passes it on. A `b3`
  that only carries the sampling decision (`0`, `1`, `d`) goes on as
  `x-b3-sampled` or `x-b3-flags`. The service reports no spans of its own.

Changed in 4.0.0: `METADATA_AGENT_SERVICE_NAME` is new. In the
JSON lines `time` is now called `timestamp`, and `trace_id` is the B3 trace ID;
the earlier value is in `request_id`.

---

## Ports, CORS and debug endpoints

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_INTERNAL_PORT` | empty (no internal port) | 1 to 65535 | Second, internal port with `/health`, `/ready` and `/metrics` (Prometheus). Empty: no second server and no `/metrics`. Do not expose it through the ingress |
| `METADATA_AGENT_INTERNAL_HOST` | `0.0.0.0` | Address | Address the internal port listens on. `::` in a cluster whose pods have IPv6 addresses (probes over IPv6); set `UVICORN_HOST` to the same address, see [below](#proxy-headers-uvicorn). The Helm chart sets both from `listenHost` |
| `METADATA_AGENT_CORS_ORIGINS` | `*` | `*`, origins separated by commas, or empty | Origins that may call the API from a browser. Empty: no origin |
| `METADATA_AGENT_DEBUG_ENDPOINTS` | `false` | `true`, `false` | Enables `GET /debug/auth` and `GET /debug/dependencies`. Off, both answer `404`. Switch on only while setting up or debugging |

- **Public port.** The API listens on port 8000; this is fixed in the image.
  The address it listens on is `UVICORN_HOST`, see
  [below](#proxy-headers-uvicorn). `/health` and `/ready` exist there too,
  without details. On the internal port `/health` also names provider and auth
  mode, and `/ready` the reasons.
- **Internal port.** It needs **one worker per container**. If the port is
  taken, or the host does not know the address in
  `METADATA_AGENT_INTERNAL_HOST`, the service logs the error and keeps running
  without the internal port.
- **CORS.** Credentials are never allowed. The response headers `X-Request-ID`
  and `Retry-After` are readable for the calling page.

### Proxy headers (uvicorn)

These variables are read by uvicorn, not by the service. They do not carry
the service's prefix, and `tests/test_env_parameters.py` does not check them.

| Variable | Default | Meaning and use |
|---|---|---|
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Addresses whose `X-Forwarded-Proto` and `X-Forwarded-For` uvicorn accepts (separated by commas, or `*`). The service builds no address from these headers: `/widget/info` returns relative URLs, and a trailing slash is not redirected. The Helm chart sets it from `forwardedAllowIps`; with Docker use `-e FORWARDED_ALLOW_IPS=<proxy address>`. More in [DEPLOYMENT.md](DEPLOYMENT.md) |
| `UVICORN_HOST` | `0.0.0.0` in the Docker image | Address the API port listens on. `::` in a cluster whose pods have IPv6 addresses, together with `METADATA_AGENT_INTERNAL_HOST`. The Helm chart sets both from `listenHost`; with Docker use `-e UVICORN_HOST=<address>`. Started without the image, uvicorn listens on `127.0.0.1` unless this variable or `--host` says otherwise |

### Debug endpoints

Both exist only with `METADATA_AGENT_DEBUG_ENDPOINTS=true`. Switch them off
again afterwards. Through the B-API proxy their paths must be allowed there.

`GET /debug/auth` shows which key arrives: presence, scheme and length only.

```json
{"mode": "auto", "Authorization": {"present": true, "scheme": "Bearer", "length": 36},
 "X-API-KEY": {"present": false}, "b_api_key_configured": true, "llm_calls_use": "forwarded"}
```

`GET /debug/dependencies` checks whether the dependencies can be reached and
whether the B-API accepts the key. The time limit per check is 5 s.

- **LLM service.** The service asks for the model list
  (`GET <LLM base>/models`) with the key the LLM calls of this request would
  carry. That uses no tokens and is refused just as a generation would be.
  Skipped for provider `openai`.
- **Text extraction and geocoder through the B-API.** Asked with the same key
  (`…/proxy/text-extraction/metrics` and a place search for "Berlin").
  `authorized: false` means: the key lacks the right for the proxy service
  (`401` or `403`). Without a key nothing is sent.
- **Repository and a text extraction called directly.** They count as
  reachable as soon as any HTTP answer comes, whatever the status. Photon
  called directly is not checked (third party).
- **Answer.** It names addresses, status, duration and the kind of error,
  never a key.

```json
{"version": "4.0.3", "llm_provider": "b-api-openai", "b_api_auth_mode": "auto",
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

## Other settings

| Variable | Default | Values | Meaning and use |
|---|---|---|---|
| `METADATA_AGENT_APP_NAME` | `Metadata Agent API` | Text | Name in the start log and title of the OpenAPI document. Normally not set |
| `METADATA_AGENT_APP_VERSION` | `4.0.3` | Text | Version the service reports (`/health`, OpenAPI, `/widget/info`). Do not set it: it changes the reported version only, not the software |

No setting chooses the schema: a request names its own `context`, default
`default` (the value `GET /info/schemata` reports as `default_context`), and
its own `version`, default `latest`. For both contexts `latest` resolves to
**2.0.0**.

---

## Ready-made configurations

Each block lists only what differs from the defaults. Replace the values in
angle brackets. The addresses are those of production; the defaults point to
staging.

### Behind the B-API proxy

edu-sharing → B-API `/api/v1/proxy/metadata-agent-canvas` → service. The keys
of users and guests are forwarded.

```env
METADATA_AGENT_LLM_PROVIDER=b-api-openai
METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
# auto: the forwarded key, else B_API_KEY (direct calls, tests).
# forward: forwarded keys only; a call without header ends with 502 (LLM_AUTH_MISSING).
METADATA_AGENT_B_API_AUTH_MODE=auto
B_API_KEY=<your B-API key>
WLO_GUEST_USERNAME=<repository user>
WLO_GUEST_PASSWORD=<repository password>
```

In the B-API the service must be registered with `forwardDelegation` or `forwardApiKey` set. The
forwarded keys need the LLM endpoints and the proxy services `text-extraction`
and `photon_geocoding`. The service must be reachable only through the B-API,
and the proxy needs a timeout of at least about 310 s.

### Standalone with a B-API key

Without the proxy, every call runs on the service's own key.

```env
METADATA_AGENT_LLM_PROVIDER=b-api-openai
METADATA_AGENT_B_API_BASE_URL=https://b-api.prod.openeduhub.net
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
# static: B_API_KEY only; an incoming Authorization header is ignored.
METADATA_AGENT_B_API_AUTH_MODE=static
B_API_KEY=<your B-API key>
WLO_GUEST_USERNAME=<repository user>
WLO_GUEST_PASSWORD=<repository password>
```

The default `auto` works as well; it would pass an incoming Bearer header on to
the B-API. Anyone who reaches the port runs extractions on `B_API_KEY`, so put
an access restriction in front. A deployment that only extracts and never
writes to the repository leaves out the two `WLO_GUEST_…` lines and sets
`METADATA_AGENT_REQUIRE_REPOSITORY=false`.

### Direct OpenAI

```env
METADATA_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=<your OpenAI key>
METADATA_AGENT_OPENAI_MODEL=gpt-4o-mini
METADATA_AGENT_REPOSITORY_URL=https://redaktion.openeduhub.net/edu-sharing/rest
WLO_GUEST_USERNAME=<repository user>
WLO_GUEST_PASSWORD=<repository password>
```

The defaults of the provider apply: 20 requests in flight, no rate limit.
Without a B-API, text extraction and geocoder are called directly.

For an OpenAI-compatible gateway of your own:

```env
METADATA_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=<key the gateway expects>
METADATA_AGENT_OPENAI_API_BASE=https://llm-gateway.example/v1
METADATA_AGENT_OPENAI_MODEL=<model name>
# What the gateway tolerates.
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=4
```

### AcademicCloud

The variant with the tightest limits; see [Throughput](#throughput).

```env
METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
B_API_KEY=<your B-API key>
METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL=deepseek-v4-flash
# The quota of the key at GWDG is about 10 calls per minute. The rate is per
# second: 0.16 = about 10 calls per minute.
# Both values count per instance: with several, divide them by their number
# (Helm chart: sharedLlmLimit.enabled=true).
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16
```

### B-API router: one model, no route

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=academiccloud/qwen3-30b-a3b-instruct-2507
METADATA_AGENT_LLM_REQUEST_STYLE=classic
# AcademicCloud models share the GWDG quota of the key: limit the throughput.
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16
B_API_KEY=<your B-API key>
```

### B-API router: a route

The route must exist in the B-API first; see
[Where routes are defined](#where-routes-are-defined).

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=<route name>
METADATA_AGENT_LLM_REQUEST_STYLE=classic
# Thinking models in the route need "none".
METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none
# AcademicCloud models share the GWDG quota of the key: limit the throughput.
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16
B_API_KEY=<your B-API key>
```

A route of GPT-5/6 models only needs
`METADATA_AGENT_LLM_REQUEST_STYLE=reasoning` and no throughput limit.

---

## Helm chart

The chart in `helm/metadata-agent/` passes the settings to the pods as
environment variables. More on the chart:
[helm/metadata-agent/README.md](helm/metadata-agent/README.md) (German) and
[DEPLOYMENT.md](DEPLOYMENT.md).

### What the chart sets

From the block `env` in `values.yaml`:

| Variable | Value in the chart | Differs from the code default |
|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | no |
| `METADATA_AGENT_B_API_AUTH_MODE` | `auto` | no |
| `METADATA_AGENT_B_API_BASE_URL` | `https://b-api.prod.openeduhub.net` | **yes**: production instead of staging |
| `METADATA_AGENT_REPOSITORY_URL` | `https://redaktion.openeduhub.net/edu-sharing/rest` | **yes**: production instead of staging |
| `METADATA_AGENT_WLO_INBOX_ID` | `21144164-30c0-4c01-ae16-264452197063` | no; check before the first install that the folder exists in the target repository and that the account may write to it |
| `METADATA_AGENT_B_API_SERVICES` | `auto` | no |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | empty | no |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `90` | no |
| `METADATA_AGENT_GENERATE_TIMEOUT` | `300` | no |
| `METADATA_AGENT_UPLOAD_TIMEOUT` | `100` | no |
| `METADATA_AGENT_MAX_REQUEST_BYTES` | `20971520` | no |
| `METADATA_AGENT_DEBUG_ENDPOINTS` | `false` | no |
| `METADATA_AGENT_REQUIRE_REPOSITORY` | `true` | no |
| `METADATA_AGENT_LOG_FORMAT` | `json` | **yes**: `json` instead of `text` |
| `METADATA_AGENT_LOG_LEVEL` | `INFO` | no |
| `METADATA_AGENT_SERVICE_NAME` | `metadata-agent` | no |

Set by the template `templates/deployment.yaml` from other chart values:

| Variable | Source in the chart | Value by default |
|---|---|---|
| `METADATA_AGENT_INTERNAL_PORT` | `service.internalPort` | `9000`; the probes use this port, and the ServiceMonitor reaches it through the Service `<name>-internal`, which is always `ClusterIP`. The main Service carries the API port only |
| `FORWARDED_ALLOW_IPS` | `forwardedAllowIps` | `*`; acceptable only with the NetworkPolicy of the chart |
| `UVICORN_HOST`, `METADATA_AGENT_INTERNAL_HOST` | `listenHost` | `0.0.0.0`; one address for the API port and the internal port. `"::"` in a cluster whose pods have IPv6 addresses |
| `METADATA_AGENT_GRACE_PERIOD_SECONDS` | `terminationGracePeriodSeconds` | `320` |
| `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` | `sharedLlmLimit`, only if enabled | `concurrentRequests` (2) divided by `replicaCount` (2), at least 1: `1` |
| `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` | `sharedLlmLimit`, only if enabled | `requestsPerMinute` (10) divided by 60 and by `replicaCount`: `0.0833` |
| `B_API_KEY`, `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` | The Secret, one entry per name in `secret.keys` | Every entry is optional: the pod starts without it. Whether the configuration can work is reported by `/ready`, see [Readiness](#readiness) |

Every other variable keeps the default of the code.

### How to override

**Any variable of this reference** goes under `env`, in a values file or with
`--set`. An entry there replaces the chart value; `""` gives the code default
(see [Empty values](#empty-values) for the three exceptions). The two
addresses `METADATA_AGENT_B_API_BASE_URL` and `METADATA_AGENT_REPOSITORY_URL`
must not be empty: the chart stops with an error, because the code default is
staging.

```yaml
# values-staging.yaml
env:
  METADATA_AGENT_B_API_BASE_URL: https://b-api.staging.openeduhub.net
  METADATA_AGENT_REPOSITORY_URL: https://repository.staging.openeduhub.net/edu-sharing/rest
  METADATA_AGENT_LOG_LEVEL: DEBUG
  # A variable the chart does not list:
  METADATA_AGENT_ALLOWED_LLM_MODELS: "gpt-6-luna"
```

```bash
helm upgrade --install metadata-agent helm/metadata-agent \
  --set image.repository=<registry path of the image> \
  --set secret.existingSecret=<name of the Secret> \
  -f values-staging.yaml \
  --set env.METADATA_AGENT_B_API_AUTH_MODE=forward
```

**Not under `env`:** the chart stops with an error for

- `METADATA_AGENT_INTERNAL_PORT` and `FORWARDED_ALLOW_IPS`: set
  `service.internalPort` and `forwardedAllowIps` instead;
- `UVICORN_HOST` and `METADATA_AGENT_INTERNAL_HOST`: set `listenHost` instead;
- `METADATA_AGENT_GRACE_PERIOD_SECONDS`: set `terminationGracePeriodSeconds`
  instead;
- `METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS` and
  `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND` while `sharedLlmLimit.enabled`
  is `true`: change `sharedLlmLimit.concurrentRequests` and
  `sharedLlmLimit.requestsPerMinute`, or disable `sharedLlmLimit`.

**Keys and credentials** never go into `values.yaml`. Name an existing Secret
with `secret.existingSecret`, or let the chart create one with
`secret.create=true` and `--set secret.values.<KEY>=…`. Only the names in
`secret.keys` reach the container. For provider `openai`, add
`OPENAI_API_KEY` to `secret.keys`. Pods read a Secret at start only: after a
change run `kubectl rollout restart`.

**`sharedLlmLimit`** is off by default. Enable it for AcademicCloud, or for a
router route of AcademicCloud models, with more than one pod. Scaling the
Deployment by hand (`kubectl scale`, an HPA) bypasses the division.
`requestsPerMinute` must be above 0, and so must the rate per pod after the
division, rounded to four decimals; otherwise the chart stops with an error.

**Values that belong together.** If you raise
`METADATA_AGENT_GENERATE_TIMEOUT` or `METADATA_AGENT_UPLOAD_TIMEOUT`, also
raise `terminationGracePeriodSeconds` and the two timeout annotations of the
ingress (320 s each). The service gets the grace period as
`METADATA_AGENT_GRACE_PERIOD_SECONDS` and warns at start if a time limit does
not fit into it. If you raise `METADATA_AGENT_MAX_REQUEST_BYTES`, also raise
the annotation `proxy-body-size` of the ingress (`20m`): with the ingress
enabled, the chart stops with an error while the annotation names a different
size.

---

## Related documents

| Document | Content |
|---|---|
| [README.md](README.md) | Overview and quick start |
| [INSTALL.md](INSTALL.md) | Installation |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Deployment with Docker and Kubernetes |
| [docs/API.md](docs/API.md) | Endpoints, requests and error codes |
| [UEBERGABE.md](UEBERGABE.md) (German) | Handover to operations |
| [CHANGELOG.md](CHANGELOG.md) (German) | Changes by version |
| [docs/steckbrief.md](docs/steckbrief.md) (German) | Service profile |
| [docs/b-api-kurzinfo.md](docs/b-api-kurzinfo.md) (German) | Short guide to B-API routing and authentication |
| [helm/metadata-agent/README.md](helm/metadata-agent/README.md) (German) | Helm chart |
