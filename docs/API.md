# Metadata Agent API – API reference

**English** · [Deutsch](API.de.md)

Reference for everyone who calls the Metadata Agent API (service id
`metadata-agent`, version 4.0.1). What changed for callers since the previous
release is listed under
[Changes since release 3.0.1](#changes-since-release-301).

The agent serves the same information interactively: `/docs` (Swagger UI) and
`/openapi.json`. Where this reference shortens a field list, `/docs` has every
field.

Related documents: [README](../README.md) (overview),
[INSTALL](../INSTALL.md), [DEPLOYMENT](../DEPLOYMENT.md),
[ENV-PARAMETER](../ENV-PARAMETER.md) (all settings),
[CHANGELOG](../CHANGELOG.md) (German), [steckbrief.md](steckbrief.md) (German,
profile of the service).

## Contents

- [Conventions](#conventions)
  - [Base URL and proxy path](#base-url-and-proxy-path)
  - [Authentication](#authentication)
  - [Request IDs and tracing](#request-ids-and-tracing)
  - [Errors](#errors)
  - [Limits and timeouts](#limits-and-timeouts)
  - [Changes since release 3.0.1](#changes-since-release-301)
- [Shared request parts](#shared-request-parts)
  - [Input source](#input-source)
  - [Public URLs only](#public-urls-only)
  - [Provider and model per request](#provider-and-model-per-request)
  - [Schemas and content types](#schemas-and-content-types)
  - [Metadata body](#metadata-body)
- [Extraction](#extraction)
  - [POST /generate](#post-generate)
  - [POST /extract-field](#post-extract-field)
  - [POST /detect-content-type](#post-detect-content-type)
- [Validation and export](#validation-and-export)
  - [POST /validate](#post-validate)
  - [POST /export/markdown](#post-exportmarkdown)
- [Repository](#repository)
  - [Common rules for writes](#common-rules-for-writes)
  - [POST /node](#post-node)
  - [POST /nodes](#post-nodes)
  - [POST /upload](#post-upload)
  - [POST /workflow/{node_id}](#post-workflownode_id)
  - [POST /upload/verify/{node_id}](#post-uploadverifynode_id)
- [Screenshot](#screenshot)
  - [POST /screenshot](#post-screenshot)
- [Schema information](#schema-information)
- [Widget endpoints](#widget-endpoints)
- [Operations endpoints](#operations-endpoints)
- [Usage examples](#usage-examples)

All operations at a glance:

| Method and path | Purpose | In `/openapi.json` |
|---|---|---|
| `POST /generate` | Extract a full metadata record | yes |
| `POST /extract-field` | Extract one field | yes |
| `POST /detect-content-type` | Detect the content type of a text | yes |
| `POST /validate` | Check a record against its schema | yes |
| `POST /export/markdown` | Render a record as Markdown | yes |
| `POST /node` | Create a repository node without metadata | yes |
| `POST /nodes` | Create a node or update one, return the full node | yes |
| `POST /upload` | Upload a record: duplicate check, preview, workflow | yes |
| `POST /workflow/{node_id}` | Set workflow states on a node | yes |
| `POST /upload/verify/{node_id}` | Compare a node with what was sent | yes |
| `POST /screenshot` | Capture a web page | yes |
| `GET /info/schemata` | Contexts and versions | yes |
| `GET /info/schemas/{context}/{version}` | Schemas of a version | yes |
| `GET /info/schema/{context}/{version}/{schema_file}` | One schema definition | yes |
| `GET /widget/info` | How to embed the web component | yes |
| `GET /widget/i18n/{lang}.json` | UI texts of the web component | yes |
| `GET /widget/dist/…`, `GET /widget/examples/…` | Files of the web component | no (static) |
| `GET /docs`, `GET /docs-assets/…`, `GET /openapi.json` | Interactive documentation | no |
| `GET /health`, `GET /ready` | Liveness and readiness | no |
| `GET /metrics` | Prometheus metrics, internal port only | no |
| `GET /debug/auth`, `GET /debug/dependencies` | Setup checks, off by default | no |

## Conventions

### Base URL and proxy path

| Way | Base URL |
|---|---|
| Direct (local, inside the cluster) | `http://<host>:8000` |
| Through the B-API proxy | `https://<b-api-host>/api/v1/proxy/metadata-agent-canvas` |

The paths in this document follow the base URL unchanged, for example
`https://b-api.staging.openeduhub.net/api/v1/proxy/metadata-agent-canvas/generate`.
The proxy entry of the B-API is called `metadata-agent-canvas`; the service
itself is called `metadata-agent`. The examples use a shell variable:

```bash
API=http://localhost:8000
```

Request bodies are JSON (`Content-Type: application/json`); `/generate` also
accepts `text/plain`. Responses are JSON, compressed with gzip from 500 bytes
on. Other origins may call the API (CORS, `METADATA_AGENT_CORS_ORIGINS`,
default `*`) and may read the response headers `X-Request-ID` and
`Retry-After`. No path ends in a slash, and a trailing slash is not
redirected: `/generate/` is answered with `404`.

`/docs` shows an example answer for every operation. It is the JSON this
reference shows after "Response `200`"; `/generate` and `/upload` have two
named examples each (complete and partial result, written record and
duplicate). The examples leave out optional fields without a value, which the
answer itself returns as `null`, and shorten long lists to a few entries.

### Authentication

The agent checks no credentials of its own callers. It is meant to run behind
the B-API proxy, which checks the caller's key, and must not be reachable from
outside in any other way. See [DEPLOYMENT](../DEPLOYMENT.md) and
[b-api-kurzinfo.md](b-api-kurzinfo.md) (German).

Which key the agent uses for its own calls:

| Call of the agent | Key | Sent as |
|---|---|---|
| LLM, text extraction and geocoder through the B-API | The caller's key if the proxy forwards it (`forwardApiKey`), else the agent's `B_API_KEY`. Decided by `METADATA_AGENT_B_API_AUTH_MODE`, see below | `Authorization: Bearer <key>` (forwarded, unchanged) or `X-API-KEY` (own key) |
| LLM with `llm_provider=openai` | The agent's `OPENAI_API_KEY` | `Authorization: Bearer` |
| Repository (edu-sharing), reading and writing | Service account `WLO_GUEST_USERNAME` / `WLO_GUEST_PASSWORD` | Basic authentication |

`METADATA_AGENT_B_API_AUTH_MODE`:

| Mode | Request with `Authorization: Bearer …` | Request without it |
|---|---|---|
| `auto` (default) | The header is passed on to the B-API | The agent's `B_API_KEY` |
| `forward` | The header is passed on | `502` `LLM_AUTH_MISSING` |
| `static` | The header is ignored, `B_API_KEY` is used | The agent's `B_API_KEY` |

What a caller needs to know:

- To make the agent use your key in a direct call, send
  `Authorization: Bearer <B-API key>`. Other schemes (for example `Basic`) and
  an incoming `X-API-KEY` header are ignored.
- The key needs the rights for the LLM endpoints of the B-API and for the proxy
  services `text-extraction` and `photon_geocoding`. Without the first, the
  extraction fails with `LLM_AUTH_FORBIDDEN`. Without the second, extraction
  from a URL fails with `TEXT_EXTRACTION_AUTH_REFUSED`. Without the third,
  places stay without coordinates and `processing.warnings` says so.
- The agent never answers `401` or `403` for a key that an upstream service
  refused. It answers `502` with an error code, because edu-sharing would read
  a `401` or `403` as an expired user session.
- `processing.auth_source` in the response of `/generate` names where the key
  came from (`forwarded`, `static`, `openai`), never the key.
- Repository: without the service account, `node_id` and `node_url` sources
  are read anonymously (only public nodes are found), and every write
  operation answers `503`. Writes appear in the node history under the service
  account.

### Request IDs and tracing

Every response carries the header `X-Request-ID`. Error bodies repeat the value
as `error.request_id`, and every log line of the request carries it. Quote it
when you report a problem.

| Incoming header | Effect |
|---|---|
| `x-client-trace-id` | Becomes the request ID (first choice) and the log field `client_trace_id` |
| `X-Request-ID` | Becomes the request ID if `x-client-trace-id` is missing |
| `x-b3-traceid`, `x-b3-spanid`, `x-b3-parentspanid`, `x-b3-sampled`, `x-b3-flags`, `b3` | B3 trace context. The trace ID becomes the request ID if neither header above is present |
| none of them | The agent starts a new trace. The request ID is its trace ID (32 hex characters) |

Rules:

- An incoming ID is used only if it is a plain token: letters, digits, `.`,
  `_`, `-`, at most 64 characters. Anything else is ignored.
- A B3 trace ID must have 16 or 32 hex characters, a span ID 16. Other values
  start a new trace.
- The agent passes the tracing headers on to the B-API, the text extraction
  and the repository. An `X-Request-ID` that is no such token is not passed
  on; the request ID of the response takes its place. The agent reports no
  spans of its own.

### Errors

Every error response of every endpoint has the same shape (`ErrorResponse` in
the OpenAPI specification):

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

| Part | Meaning |
|---|---|
| `detail` | Text for people. For `422` on `/extract-field`, `/detect-content-type`, `/node` and `/screenshot`, and for a path parameter that is too long, it is the list of violations, each with `loc`, `msg` and `type` only (the refused input is not repeated); on the other endpoints one string that names each refused field |
| `error.code` | Stable code for programs, see the tables below |
| `error.message` | Short text |
| `error.request_id` | ID of the request, same as the header `X-Request-ID` |
| `error.upstream_status`, `error.auth_source` | Only for `LLM_*` codes: HTTP status of the LLM service and origin of the key |
| further keys beside `error` | Only where an operation says so: `processing` (failed `/generate`), `node`, `discarded_node`, `collections`, `step`, `field_errors` (writes) |

The texts in `detail` and `error.message` are mostly German and may change. Use
`error.code` in programs.

General codes:

| Status | `error.code` | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | Invalid JSON (also `NaN` and `Infinity`) or a body that is not a JSON object, on every endpoint with a body; required input missing; URL not public; path `node_id` is not a UUID |
| `403` | `FORBIDDEN` | The node is outside its editing window |
| `403` | `SCREENSHOT_BROWSER_REFUSED` | The request came from the agent's own screenshot browser |
| `404` | `NOT_FOUND` | Unknown path, context, version, schema, field or node |
| `405` | `METHOD_NOT_ALLOWED` | Wrong HTTP method |
| `413` | `REQUEST_TOO_LARGE` | Body larger than `METADATA_AGENT_MAX_REQUEST_BYTES`; on `/validate` and `/export/markdown` larger than 2 MiB |
| `422` | `VALIDATION_ERROR` | The body does not fit the request model, or a value is longer than its limit, see [Limits and timeouts](#limits-and-timeouts) |
| `422` | `SCHEMA_UNAVAILABLE` | Write: the schema of the record cannot be loaded, because its context, version or `metadataset` is unknown |
| `499` | none | The caller closed the connection, see [Limits and timeouts](#limits-and-timeouts) |
| `500` | `INTERNAL_ERROR` | Unexpected error; the cause is in the log under the request ID |
| `502` | `UPSTREAM_ERROR` | The repository or the source of the text failed |
| `503` | `SERVICE_UNAVAILABLE` | Repository credentials are not configured |
| `503` | `NOT_READY` | `/ready`: the configuration is incomplete |
| `503` | `TOO_MANY_GENERATIONS` | `/generate`: too many runs at once, with `Retry-After` |
| `504` | `GENERATION_TIMEOUT` | Extraction passed `METADATA_AGENT_GENERATE_TIMEOUT` |
| `504` | `UPLOAD_TIMEOUT` | Write passed `METADATA_AGENT_UPLOAD_TIMEOUT` |

Codes of the LLM calls (`/generate`, `/extract-field`, `/detect-content-type`):

| `error.code` | Status | Meaning |
|---|---|---|
| `LLM_AUTH_MISSING` | `502` | No key: none forwarded and none configured |
| `LLM_AUTH_REJECTED` | `502` | The LLM service does not accept the key (its `401`) |
| `LLM_AUTH_FORBIDDEN` | `502` | The key lacks the right for this call (its `403`) |
| `LLM_MODEL_NOT_FOUND` | `502` | Model unknown at the provider |
| `LLM_RATE_LIMITED` | `503` | Still throttled after the retries |
| `LLM_UNREACHABLE` | `502` | No connection to the LLM service |
| `LLM_ROUTE_NOT_FOUND` | `502` | The B-API router knows no active route of this name |
| `LLM_ROUTE_MISCONFIGURED` | `502` | The route cannot serve the request as configured |
| `LLM_UPSTREAM_ERROR` | `502` | Server error of the LLM service that outlasted the retries |
| `LLM_REQUEST_REJECTED` | `502` | The LLM service refused this request (other `4xx`) |
| `LLM_EMPTY_ANSWER` | `502` | Answer without text |
| `LLM_ANSWER_TRUNCATED` | `502` | Answer cut off at the token limit |
| `GENERATION_FAILED` | `502` | The run ended before the field calls, for example the schema could not be loaded |
| `EXTRACTION_FAILED` | `502` | No field got a value and no single cause can be named, for example all field calls ended at the deadline |

Codes of the text extraction (input from a URL):

| `error.code` | Status | Meaning |
|---|---|---|
| `TEXT_EXTRACTION_NOT_CONFIGURED` | `503` | The installation has no text extraction service |
| `TEXT_EXTRACTION_AUTH_MISSING` | `502` | No key for the text extraction through the B-API |
| `TEXT_EXTRACTION_AUTH_REFUSED` | `502` | The B-API refused the key for the proxy service `text-extraction` |
| `TEXT_EXTRACTION_RATE_LIMITED` | `503` | The service is throttled (`429`); try again later |
| `TEXT_EXTRACTION_CONNECT_TIMEOUT` | `504` | No connection within 10 s |
| `TEXT_EXTRACTION_TIMEOUT` | `504` | No answer within 30 s |
| `TEXT_EXTRACTION_UNREACHABLE` | `502` | Connection refused or name not resolvable |
| `TEXT_EXTRACTION_HTTP_ERROR` | `502` | The service answered with an error status |
| `TEXT_EXTRACTION_FAILED` | `502` | The call failed in another way, or the answer was not readable JSON |
| `TEXT_EXTRACTION_EMPTY` | `422` | The service returned no text for the URL |

### Limits and timeouts

| Limit | Default | Setting | What happens |
|---|---|---|---|
| Request body | 20 MiB (20,971,520 bytes) | `METADATA_AGENT_MAX_REQUEST_BYTES` | `413` `REQUEST_TOO_LARGE` |
| Request body of `/validate` and `/export/markdown` | 2 MiB (2,097,152 bytes) | fixed | `413` `REQUEST_TOO_LARGE` |
| Names in a request | `context` 64, `version` 32, `schema_file` 300, `field_id` 200, `language` 8 characters; one entry of `collection_id`, `workflow_steps` or `steps` 200 | fixed | `422`. The same limits hold for `contextName`, `schemaVersion`, `metadataset` and `language` of a record and for the path parameters |
| URLs in a request (`url`, `source_url`, `preview_url`) | 8,192 characters | fixed | `422` |
| Source text | 150,000 characters | fixed | The rest is cut off. The response says so in its warnings (`processing.warnings`; `warnings` of `/detect-content-type`) |
| Correction instruction | 10,000 characters | fixed | With `input_source=text` a longer text counts as a new source; with a URL or node source `400`. See [POST /generate](#post-generate) |
| Findings of `/validate` | 20 per field and check; 500 errors and 500 warnings per response | fixed | A closing entry counts what was left out, see [POST /validate](#post-validate) |
| Extraction (`/generate`, `/extract-field`, `/detect-content-type`), fetching the source included | 300 s | `METADATA_AGENT_GENERATE_TIMEOUT` | `504` `GENERATION_TIMEOUT`; `/generate` returns a partial result first |
| One LLM call | 90 s | `METADATA_AGENT_REQUEST_TIMEOUT` | The field fails; a read timeout is retried once |
| Text extraction | 10 s connect, 30 s answer | fixed | `TEXT_EXTRACTION_CONNECT_TIMEOUT`, `TEXT_EXTRACTION_TIMEOUT` |
| Write (`/upload`, `/nodes`, `/node`, `/workflow`, `/screenshot` with `node_id`) | 100 s | `METADATA_AGENT_UPLOAD_TIMEOUT` | `504` `UPLOAD_TIMEOUT` |
| Screenshot | 90 s, two captures at once | fixed | `502`; further captures wait within the 90 s |
| `/generate` runs at once | no limit | `METADATA_AGENT_MAX_CONCURRENT_GENERATIONS` | `503` `TOO_MANY_GENERATIONS`, `Retry-After: 15` |
| Parallel field calls of one run | 20 | `max_workers`, `METADATA_AGENT_DEFAULT_MAX_WORKERS` | Capped by what the provider allows in flight (AcademicCloud: 2) |

A proxy in front of the agent needs a timeout of at least about 310 s. With a
shorter one the caller gets the proxy's bare `504` instead of the agent's
answer.

**JSON.** Every endpoint with a body reads it the same way, and leniently:
literal line breaks or tabs inside strings are repaired, in bodies of up to
1,000,000 characters. `NaN` and `Infinity`, which are no JSON, invalid UTF-8,
nesting too deep to parse and a body that is not a JSON object are answered
with `400`.

**Status `499`.** When the caller closes the connection, `/generate`,
`/extract-field` and `/detect-content-type` stop their LLM calls. No response
reaches anyone; the request line in the log and the metric
`metadata_agent_http_requests_total` show the status `499`, and a `/generate`
run counts as `cancelled`, not as `failed`. A proxy that gives up after its own
timeout causes exactly this. Write operations are not stopped this way: a
write that has started is finished or cleaned up. A caller that leaves before
its request body is complete gets the same status on every endpoint.

### Changes since release 3.0.1

Release 4.0.0 changed the points below. Callers written against
3.0.1 should check them:

| Area | Before | Now |
|---|---|---|
| `/generate` near the deadline | `504` | `200` with `processing.status: "partial"`; the fields that were too late are named in `processing.errors` |
| Caller disconnects | The run went on | The LLM calls stop; status `499` in log and metrics |
| Too many `/generate` runs | no limit | Optional limit: `503` `TOO_MANY_GENERATIONS` with `Retry-After` |
| Node that the repository does not have or does not show (`node_id`, `node_url`) | `502` | `404` |
| `llm_model` that is not allowed | Refused after the source was fetched | `422` before the source is fetched |
| Text extraction | Called directly | Called through the B-API with the caller's key; new codes `TEXT_EXTRACTION_AUTH_MISSING`, `TEXT_EXTRACTION_AUTH_REFUSED`, `TEXT_EXTRACTION_NOT_CONFIGURED`, `TEXT_EXTRACTION_RATE_LIMITED` |
| `node_url` with a refused or missing key | Fell back to title and description | Ends with the error code |
| `NaN`, `Infinity` in a body | `500` | `400` |
| `_origins`, `_source_text` of the wrong kind | `500` | `422` |
| `metadataset`, `schemaVersion`, `contextName`, `_schema` of the wrong kind in `/upload`, `/nodes`, `/upload/verify` | Node written, then `500` | `422` before anything is written |
| Options of `/validate` and `/export/markdown` in the flat form | Ignored | Applied |
| `/extract-field` | Value in its own shape | Value shaped like `/generate` (coordinates for `schema:location`, day codes for opening hours) |
| `processing.warnings` | few cases | Also: places without coordinates because the B-API refused the geocoder |
| LLM answer cut off at the token limit | Half a value was used | The field fails with `LLM_ANSWER_TRUNCATED` |
| Generated request ID | 16 hex characters | 32 hex characters (the B3 trace ID) |
| `/validate`, `/export/markdown` with an unknown context, version or schema file | `200` (`valid: false`, or `Schema not found` as Markdown) | `404` |
| Invalid JSON or a body that is no object on `/extract-field`, `/detect-content-type`, `/node`, `/screenshot` | `422` | `400`, as on the other endpoints; `NaN` and `Infinity` are refused there too |
| `detail` of a `422` on these four endpoints | Each entry repeated the refused input | Each entry has only `loc`, `msg`, `type` |
| Caller leaves before the request body is complete | `400` or `500` in log and metrics | `499` |
| Path with a trailing slash, for example `/generate/` | `307` redirect | `404` |
| Body of `/validate` and `/export/markdown` | Up to 20 MiB | At most 2 MiB, else `413` |
| Length of `context`, `version`, `schema_file`, `field_id`, `language`, URLs and list entries | No limit | Limited, `422` beyond, see [Limits and timeouts](#limits-and-timeouts) |
| Findings of `/validate` | One per value, values quoted in full | At most 20 per field and check, 500 errors and 500 warnings; quoted values cut to 200 characters |
| `/validate`: required field that holds `[""]` or only blanks | Counted as filled | Error `Required field is empty` |
| `/validate`: numbers and booleans in closed vocabularies | `3` and `true` got a warning, `0` and `false` were not checked | Compared as text (`3` is `"3"`, `true` is `"true"`); `0` and `false` are checked |
| `/validate`: suggestion for a value outside a closed vocabulary | Only for vocabularies with URIs; also by similarity for values under four characters | Also for vocabularies keyed by `value` (`Deutsh` gives `de`); by similarity only from four characters on |
| `/export/markdown` | Title was the file name; core fields could be missing or appear twice; `schema_used` was `auto` | Title is the name of the content type; every field once; fields of undeclared groups in a last section; `schema_used` names the file |
| `label` of `GET /info/schemas/…` | File name | Name of the content type (`core.json`: the file name) |
| `GET /widget/info` | Absolute URLs built from the request | Relative references (`dist/main.js`, `examples/full.html`); the snippets use the placeholder `{AGENT_URL}` |
| `screenshot_method` of `/generate` | Any non-empty text started a capture | `playwright` or `pageshot`; other values `422` |
| Correction instruction over 10,000 characters with a URL or node source | Dropped with a warning | `400` |
| Header keys and `processing` in `existing_metadata` | Replaced the header of the response | Dropped |
| Node without any text (`node_id`, `node_url`) | `502` | `422` |
| Metadata read from a node (`node_id`, `node_url`) | Every property of the node | Only fields a schema of the context declares; never `ccm:oeh_extended*` |
| `/detect-content-type`, `/extract-field` with a text over 150,000 characters | Cut silently | Cut with a note in `warnings` |
| `/nodes` with an option only `/upload` has, or the reverse; unknown key beside a nested `metadata` | Ignored | `422` |
| `metadataset` that names no loadable schema on `/upload`, `/nodes` | Core fields written, `200` | `422` `SCHEMA_UNAVAILABLE`, nothing written |
| Write that reaches the deadline while collections are linked | Node discarded | Node kept: `504` with `node` and `collections` |
| Response of `/upload` and `/nodes` | `fields_skipped` was `0` after every successful write | `fields_skipped` counts the sent fields that reach no property; new `skipped_fields` names them |
| `schemaVersion: null` on `/upload`, `/nodes`, `/upload/verify` | `422` | The default version, as on `/validate` |
| `/screenshot` with `node_id`, no service account | `200` with `preview_uploaded: false` | `503` before the capture |
| `/upload/verify` | Nested `expected_metadata` was not compared; options counted as fields; a license link stored as key and version was `missing_in_repo`; `oeh:new_lrt` was `not_written` where the schema calls the field `ccm:oeh_lrt`; case ignored everywhere | All forms compared alike; options left out; such a license link is a `match`; `oeh:new_lrt` is compared as `ccm:oeh_lrt`; case ignored only for vocabulary values and the license key |
| `ccm:commonlicense_key` spelled `CC BY-SA`, `cc-by-sa` or `CC0` | Written as sent | Written as `CC_BY_SA`, `CC_0` |
| `ccm:custom_license` | A link of any host that ends in a license key became that key; a ported CC link lost its jurisdiction | Only URIs under `w3id.org/openeduhub/vocabs/` become a key; a ported CC link (`/3.0/de/`) stays beside key and version |
| Duplicate check | Search index only; a hit without title did not count | Also the 25 newest nodes of the inbox folder; a hit without title is a duplicate, named by its ID |
| Metric `metadata_agent_generations_total` | `success`, `partial`, `failed` | Also `cancelled` (the caller left) and `refused` (`TOO_MANY_GENERATIONS`) |
| `Retry-After` for a page on another origin | Not readable | Readable (CORS) |
| `/generate` with no field left to extract | `500` | `200`, record unchanged, `fields_extracted: 0` |
| Model answer for a vocabulary field | A label could come back instead of the key (`Deutsch`); answers of one to three characters were matched by similarity (`7` became age rating 0) | Always the key (`de`); no similarity match under four characters |
| Single value on a list field | Stayed a single value | A list of one entry |
| List on a single-value field | First entry, the others dropped silently | First entry, with a note in `processing.warnings` |
| `_origins` of an empty field the model was asked for | `user` | `ai` |
| Field that `core.json` and the content type both declare | Extracted twice and counted twice in `fields_total` (`event.json`: 48) | Once (47) |
| Required field without a value | Up to `METADATA_AGENT_LLM_MAX_RETRIES` more calls | Exactly one more call; none with a closed vocabulary |
| Existing value of a field in its prompt | In full | Cut at 2,000 characters |
| Numbers on boolean fields | From the model every number but `0` was `true`; from the caller no number was a boolean | `1` and `0` are `true` and `false`; any other number is no boolean |
| `1.500` on an integer field | `2` | `1500` |
| Dates `März 15, 2025`, `1. Sept. 2025` | Not read | Read |
| Date `03/15/2025` from the model; non-object entries of `schema:eventSchedule` and `schema:openingHoursSpecification` | Dropped silently | Left out with a note in `processing.warnings` |
| `dayOfWeek` with several days in one string (`Mo, Mi, Fr`) | One day (`MO`) | Every day named; also the English short forms `mon` to `sun` |
| Place with `address` or `geo` written as text | Geocoding switched off for the whole request | The text address is looked up; a fault costs that place only, with a note in `processing.warnings` |
| `language` other than `de`, `en`, `fr` | Places stayed without coordinates | Coordinates as for the other languages |
| LLM calls stopped at the deadline or by a disconnect in `metadata_agent_llm_requests_total` | `result="ok"` | `result="cancelled"` |
| Metrics that say when a failure last happened | none | `metadata_agent_llm_last_failure_timestamp_seconds`, `metadata_agent_http_last_internal_error_timestamp_seconds` |

## Shared request parts

### Input source

`/generate`, `/extract-field` and `/detect-content-type` take their text from
one of four sources:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `input_source` | string | `text` | `text`, `url`, `node_id` or `node_url` |
| `text` | string | none | The text. Required for `text` |
| `source_url` | string | none | URL of the page. Required for `url`, optional for `node_url` |
| `node_id` | string | none | UUID of a repository node. Required for `node_id` and `node_url` |
| `extraction_method` | string | `browser` | How the page is read: `browser` (renders JavaScript) or `simple` (fast) |
| `output_format` | string | `markdown` | Form of the page text: `markdown`, `txt` or `html` |
| `repository` | string | `staging` | Deprecated and ignored. The repository comes from `METADATA_AGENT_REPOSITORY_URL` |

An empty string counts as "not given" for `text`, `source_url` and `node_id`.

| `input_source` | Where the text comes from |
|---|---|
| `text` | The request. Control characters are removed |
| `url` | The text extraction service reads `source_url` |
| `node_id` | Full text and metadata of the node. Without stored full text: title, description and keywords of the node |
| `node_url` | The node's stored full text. Without it, the page at `source_url` or at the node's `ccm:wwwurl`. If the page cannot be read: title, description and keywords, with a note in the warnings |

For node sources the metadata of the node count as `existing_metadata`; values
in the request win. Only the fields that a schema of the context declares are
taken from the node, never the extended fields `ccm:oeh_extended*`. A stored
full text is read up to 150,000 characters; the warnings say when it was cut.
If the source has a URL (`source_url` or the node's `ccm:wwwurl`), the text
starts with a line `Quell-URL / Source URL: <url>`.

Errors of the source, on all three endpoints:

| Status | `error.code` | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | `text`, `source_url` or `node_id` missing for the chosen source; the source delivered no text; `source_url` is not public |
| `404` | `NOT_FOUND` | The repository does not have the node or does not show it to the service account |
| `422` | `VALIDATION_ERROR` | `node_id` is not a UUID; `source_url` is longer than 8,192 characters; the node holds no text: neither full text nor title, description, keywords or a readable URL |
| `422` | `TEXT_EXTRACTION_EMPTY` | The page has no text |
| `502` | `UPSTREAM_ERROR` | The repository could not be read. `detail`: `Failed to fetch input: <error type>` |
| `502`, `503`, `504` | `TEXT_EXTRACTION_*` | See [Errors](#errors). With `node_url` only the codes `…_AUTH_MISSING`, `…_AUTH_REFUSED` and `…_NOT_CONFIGURED` end the request; other failures fall back to title and description |

### Public URLs only

Every URL the agent opens itself must be a public `http` or `https` address:
`source_url`, screenshot URLs and preview image URLs. Refused are
`localhost`, names ending in `.localhost`, `.local`, `.internal`, `.lan` or
`.home.arpa`, loopback, private and link-local addresses (also
`169.254.169.254`), credentials in the URL and other schemes. The host name is
resolved inside the agent's container; a name that points to a private address
there is refused too.

| Where | Answer |
|---|---|
| `source_url` with `input_source=url`, `url` of `/screenshot` | `400`, the reason in `detail` |
| `source_url` with `node_url` | `400`, but only if the node has no stored full text (with full text the URL is not fetched) |
| `ccm:wwwurl` of a node with `node_url` | No error: title and description are used, with a note in the warnings |
| Screenshot or preview next to `/generate` or `/upload` | No error: the response has no preview, or `preview.success: false` |

### Provider and model per request

`/generate`, `/extract-field` and `/detect-content-type` accept two optional
fields that replace the configured LLM for one request:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `llm_provider` | string | `METADATA_AGENT_LLM_PROVIDER` (`b-api-openai`) | `b-api-openai`, `b-api-academiccloud`, `b-api-router` or `openai`. Upper case and blanks are tolerated |
| `llm_model` | string | default model of the provider | Model name. With `b-api-router` a route name or `provider/model`. At most 200 characters from `A–Z a–z 0–9 . _ : / @ -` |

Default models: `gpt-6-luna` (`b-api-openai`), `deepseek-v4-flash`
(`b-api-academiccloud`), `openai/gpt-6-luna` (`b-api-router`), `gpt-4o-mini`
(`openai`).

Both are checked before the source is fetched and answered with `422`:

- `llm_provider: "openai"` is only allowed if the service itself runs with the
  provider `openai`.
- With `METADATA_AGENT_ALLOWED_LLM_MODELS` set, `llm_model` must be on that list
  or be the default model of the provider.
- An unknown provider name or a model name with other characters.

### Schemas and content types

A record belongs to a **context** (`default`, `mds_oeh`), a **version** of the
context and a **content type**, named by its schema file. The core fields
(`core.json`: title, description, keywords, subject, educational level and
more) apply to every content type.

| Name | Meaning |
|---|---|
| `context` | `default` (WLO/OEH standard) or `mds_oeh` |
| `version` | `latest` or a version such as `2.0.0`. `latest` is `2.0.0` today; `1.8.0` and `1.8.1` are still served |
| `schema_file` | Schema file such as `event.json`, the URI of the content type (`http://w3id.org/openeduhub/vocabs/contentTypes/event`), or `auto` where detection is offered |

Content types of version `2.0.0` (both contexts):

| Schema file | Content type | Last part of the URI |
|---|---|---|
| `learning_material.json` | Learning material | `learning_material` |
| `education_offer.json` | Educational offering | `education_offer` |
| `event.json` | Event | `event` |
| `person.json` | Person | `person` |
| `organization.json` | Organization | `organization` |
| `tool_service.json` | Tool or service | `tool_service` |
| `occupation.json` | Occupation | `profession` |
| `didactic_planning_tools.json` | Didactic planning | `didactic_concepts` |

Versions `1.8.0` and `1.8.1` also offer `source.json` and `prompt.json`. The
current lists come from the [schema information](#schema-information)
endpoints. Field IDs differ between versions: in `2.0.0` an event starts at
`ccm:oeh_event_begin`. The fields that reach the repository are listed in
[WLO-REPO-FELDER.md](../WLO-REPO-FELDER.md) (German).

### Metadata body

`/validate`, `/export/markdown`, `/upload` and `/nodes` take a metadata record
in two equal forms. Options of the operation stand beside the record.

Flat, exactly what `/generate` returns (recommended):

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

Wrapped, the fields under `metadata`:

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

Rules:

- `contextName`, `schemaVersion` and `metadataset` may stand inside `metadata`
  or beside it. Inside wins.
- `/upload` and `/nodes` also accept the form the web component sends: the
  fields one level deeper under `metadata.metadata`, the three markers in the
  outer `metadata`.
- **Always send `metadataset`** (file name or content type URI). It decides
  which schema applies beside `core.json`. Without it, `/upload` and `/nodes`
  write only the core fields and drop type-specific fields such as
  `ccm:oeh_event_begin` without an error; `/validate` and `/export/markdown`
  assume `learning_material.json`.
- In the flat form only the option names of the operation are taken out of the
  record. A misspelled option is treated as a metadata field and has no effect.
- `/upload` and `/nodes` refuse an option that only the other one has with
  `422`, in both forms (`repository` is ignored by both). Beside a nested
  `metadata` they also refuse any key that is neither one of their options nor
  part of a `/generate` response.
- `contextName`, `schemaVersion` and `metadataset` must be strings or `null`
  (`null` counts as not given), `_schema` (older form with `context`,
  `version`, `file`) an object. Otherwise `422`.
- The rest of a `/generate` response does no harm: keys that start with `_`
  (`_origins`, `_source_text`) and the keys `metadataset_uri`, `language`,
  `exportedAt`, `processing` and `preview_image_url` are never written or
  validated as fields.

## Extraction

### POST /generate

Extracts a complete metadata record from a text, a web page or a repository
node. The content type is detected or given. Each field is one LLM call; the
calls run in parallel.

**Request.** Body: JSON, or `text/plain` with the text alone (all other values
at their defaults).

| Name | Type | Default | Meaning |
|---|---|---|---|
| input fields | | | See [Input source](#input-source) |
| `context` | string | `default` | Schema context |
| `version` | string | `latest` | Schema version |
| `schema_file` | string | `auto` | Content type. `auto`: the LLM detects it |
| `language` | string | `de` | Language of the extraction: `de` or `en` |
| `max_workers` | integer | `METADATA_AGENT_DEFAULT_MAX_WORKERS` (20) | Parallel LLM calls, 1 to 20 |
| `include_core` | boolean | `true` | Also extract the core fields |
| `enable_geocoding` | boolean | `true` | Add coordinates to the places in `schema:location` |
| `normalize` | boolean | `true` | Normalize dates, booleans, vocabulary values and structure. The shape follows the schema: a single value on a list field becomes a list of one entry; a list on a single-value field gives its first entry, with a note in `processing.warnings`. See "How values are read" below |
| `existing_metadata` | object | none | Values that already exist, see below |
| `regenerate_fields` | string array | none | Only these field IDs are extracted again |
| `regenerate_empty` | boolean | `false` | Also extract the fields that are empty in `existing_metadata` |
| `llm_provider`, `llm_model` | string | configured | See [Provider and model per request](#provider-and-model-per-request) |
| `screenshot_method` | string | none | `playwright` (or the old alias `pageshot`) captures a preview next to the extraction. Not set or empty: no screenshot. Any other value: `422` |
| `preview_url` | string | none | URL for the screenshot. Not set: `source_url`, else `ccm:wwwurl` of the record |

Existing values and repeated runs:

- `existing_metadata` holds field IDs with their values, plus `_origins` and
  `_source_text` from an earlier response. The header keys (`contextName`,
  `schemaVersion`, `metadataset`, `metadataset_uri`, `language`, `exportedAt`),
  `processing` and `preview_image_url` are dropped, so a whole earlier response
  can be sent. Any other key the schema does not know is returned unchanged
  like a field.
- Without `regenerate_fields` and `regenerate_empty` every field is extracted.
  A new value replaces the existing one. An empty answer never replaces an
  existing value.
- With one of the two options only the listed fields, the empty fields
  (`regenerate_empty`) and the fields that `existing_metadata` does not hold
  are extracted.
- If no field is left to extract, for example for a complete record with
  `regenerate_empty`, the answer is `200` with the record unchanged and
  `processing.fields_extracted: 0`. No field call is made.
- The existing value of a field goes into its prompt, cut at 2,000
  characters.
- `existing_metadata._origins` maps field IDs to `ai` or `user`. A field marked
  `user` keeps its value and is not extracted, unless it is listed in
  `regenerate_fields`.
- **Correction round.** Send the fields of the first run as
  `existing_metadata`, including `_source_text`, and put the instruction into
  `text` (for example "shorter title"). The agent then works on the original
  source, the values so far and the instruction. With `input_source=text` a
  `text` longer than 10,000 characters is taken as a new source instead;
  `processing.warnings` says so. With a URL or node source the source is
  fetched and `text` can only be the instruction: longer than 10,000
  characters it is refused with `400`.
- `_origins` must be an object of strings and `_source_text` a string,
  otherwise `422`.

**Response `200`.** The fields stand at the top level, not inside a `metadata`
object:

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

| Part | Meaning |
|---|---|
| `contextName`, `schemaVersion`, `metadataset` | Context, concrete version and schema file that were used |
| `metadataset_uri` | URI of the content type |
| field IDs | Every field with a value. Empty values (`""`, `[]`, `{}`, `null`) are left out |
| `_origins` | Origin of each field of the schema, also of the empty ones the response leaves out (the example lists only the filled ones): `ai` for a value from the model and for an empty field the model was asked for, `user` for a value the caller brought and the model did not replace |
| `_source_text` | The text the values come from. Send it back for a correction round |
| `preview_image_url` | Only with a successful screenshot: data URL (`data:image/png;base64,…`). A screenshot that fails or is late is left out without an error |
| `processing.status` | `success`, `partial` (some fields failed) or `failed` (never in a `200`) |
| `processing.fields_extracted`, `fields_failed`, `fields_total` | Fields with a value from this run, fields that failed for a technical reason, fields of the schema the model can fill. A field that `core.json` and the content type both declare counts once (`event.json`: 47) |
| `processing.auth_source` | Origin of the key: `forwarded`, `static`, `openai` |
| `processing.content_type_detection` | `method`: `llm`, `preselected` (given by `schema_file`) or `keywords`. With `keywords` the LLM gave no usable answer and the type is a guess: `confidence: "low"`, a `reason`, and a note in `warnings` |
| `processing.error` | For `partial` with a named cause, and in the error response of a failed run: `code`, `message`, for LLM errors also `upstream_status` and `auth_source` |
| `processing.errors` | One text per failed field, and the fields that were too late |
| `processing.warnings` | Notes that do not make the run fail, see below |

Typical entries of `processing.warnings`: the source was cut to 150,000
characters; the content type was guessed by keywords; with `node_url` the page
could not be read and only title and description were used; places stay without
coordinates because the B-API refused the geocoder or the time ran out; a long
`text` was taken as a new source.

Further entries, in the wording of the code (`<field>` is the field ID):

| Entry | When |
|---|---|
| `<field>: Feld nimmt einen Wert auf: N weitere Wert(e) nicht übernommen.` | A list arrived for a field that takes one value. The first entry was kept |
| `<field>: '03/15/2025' ist kein Datum in der Lesart Tag/Monat/Jahr und wurde nicht übernommen.` | The model wrote a date that does not exist when read as day/month/year: the month first, or a day such as `30.02.2026`. The value was left out |
| `<field>: N Wert(e) ohne Objektform nicht übernommen.` | Entries of `schema:openingHoursSpecification` or `schema:eventSchedule` that are no objects were left out |
| `Geokodierung: N Ort(e) konnten nicht verarbeitet werden und bleiben unverändert (<error type>).` | A fault while a place was looked up. That place stays as it was; the others are processed |

**How values are read.**

- **Vocabularies.** A vocabulary field holds the key of a concept: its URI,
  else its `value` (`de`), never its label. An answer of the model is matched
  by the key (a `value` in any case), by a label or alternative label in any
  language and, in a closed vocabulary, by the nearest German label. The last
  only for answers of four characters and more: `7` never becomes another age
  rating. If no concept fits, a closed vocabulary gives no value and the model
  is asked again, up to `METADATA_AGENT_LLM_MAX_RETRIES` times; an open
  vocabulary keeps the answer. Values from `existing_metadata` or from the node
  are matched exactly only, by key or label, and a value that matches nothing
  stays as it is. Numbers and booleans count by their text: `3` is `"3"`,
  `true` is `"true"`, and `0` and `false` are values.
- **Required fields.** A required field without a value costs exactly one more
  call. With a closed vocabulary it costs none: `null` there says that no
  concept fits.
- **Numbers.** German notation (`1,5`, `1.500,50`) and German number words
  (`zwölf`). On an integer field `1.500` is 1500, on a number field 1.5.
  `1_000` and `ein` are no numbers.
- **Booleans.** `true`/`false`, `ja`/`nein`, `yes`/`no`, `wahr`/`falsch` and
  `1`/`0`, the last pair as text or as number. Any other number is no boolean.
- **Dates.** Returned as `YYYY-MM-DD`, with a time as `YYYY-MM-DDTHH:MM`. ISO
  forms, German forms (`15.03.2025`) and German month names are read, also
  `März 15, 2025` and `1. Sept. 2025`. The day comes first: `03/15/2025` from
  the model is no date and is left out with a warning, like a day that does
  not exist.
- **Weekdays.** `dayOfWeek` of the opening hours holds the codes `MO` to `SU`.
  A range (`Mo-Fr`) and a list in one string (`Mo, Mi, Fr`, `Sa/So`,
  `Montag und Mittwoch`) are expanded when every part is a day or a range. The
  English short forms `mon` to `sun` count too. An entry of more than 200
  characters stays as written.
- **Places.** An `address` written as text is looked up as written and is not
  replaced. A `geo` that is no object holds no coordinates; latitude `0` is a
  coordinate. The geocoder is asked in `de`, `en` or `fr`; for any other
  `language` it answers in the language of the place. A fault in one place
  leaves that place unchanged, with a warning.

**Partial result at the deadline.** Field calls that still run 5 s before
`METADATA_AGENT_GENERATE_TIMEOUT` are stopped. What is extracted by then comes
back with `200`:

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

Check `processing.status` and `processing.errors` before you apply a result.
The late fields can be requested again with `regenerate_fields` or
[POST /extract-field](#post-extract-field).

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `200` | none | `processing.status` is `success` or `partial` |
| `400` | `BAD_REQUEST` | Invalid JSON, `NaN` or `Infinity`, body is no object, no text, unknown content type URI, correction instruction over 10,000 characters with a URL or node source, see also [Input source](#input-source) |
| `404` | `NOT_FOUND` | Context, version or schema file unknown (checked before the source is fetched); node not found |
| `422` | `VALIDATION_ERROR` | A field does not fit, for example `max_workers` outside 1 to 20, an unknown `screenshot_method`, a value over its length limit, `_origins` or `_source_text` of the wrong kind, provider or model not allowed; a node without text |
| `499` | none | The caller closed the connection; the run was stopped |
| `502` | `LLM_*`, `GENERATION_FAILED`, `EXTRACTION_FAILED` | Not one field was extracted. The body also has `contextName`, `schemaVersion`, `metadataset`, `language` and `processing` |
| `503` | `LLM_RATE_LIMITED` | As `502`, the LLM service is throttled |
| `503` | `TOO_MANY_GENERATIONS` | The limit of runs at once is reached. Header `Retry-After: 15` |
| `504` | `GENERATION_TIMEOUT` | The deadline passed before a result existed, for example while the source was fetched |
| `422`, `502`, `503`, `504` | `TEXT_EXTRACTION_*`, `UPSTREAM_ERROR` | The source could not be read, see [Input source](#input-source) |

A failed run (`schema_file` was given, the B-API refused the key at the first
field call):

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

After a fatal error such as a refused key the agent sends no further field
calls. If the failure already happens while the content type is detected,
`metadataset` is `null` and `processing.fields_total` is `0`.

**Example.**

```bash
curl -s -X POST "$API/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Workshop KI in der Bildung am 15. März 2026 um 9 Uhr in Berlin. Zielgruppe: Lehrkräfte.",
    "schema_file": "event.json"
  }'
```

### POST /extract-field

Extracts one field again, for example to correct it without a full run. The
value comes back in the same shape as from `/generate`.

**Request.**

| Name | Type | Default | Meaning |
|---|---|---|---|
| input fields | | | See [Input source](#input-source) |
| `schema_file` | string | required | Schema file or content type URI. `auto` is not possible here |
| `field_id` | string | required | Field ID, for example `ccm:oeh_event_begin`. A field the content type does not declare is looked up in `core.json` |
| `existing_metadata` | object | none | Current values. The value of `field_id` is given to the LLM as the previous value, cut at 2,000 characters |
| `context` | string | `default` | Schema context |
| `version` | string | `latest` | Schema version |
| `language` | string | `de` | `de` or `en` |
| `normalize` | boolean | `true` | Normalize and shape the value like `/generate`: a single value on a list field becomes a list of one entry, a list on a single-value field gives its first entry with a note in `processing.warnings`. For `schema:location` this includes coordinates |
| `llm_provider`, `llm_model` | string | configured | See [Provider and model per request](#provider-and-model-per-request) |

**Response `200`.**

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

| Part | Meaning |
|---|---|
| `value` | The extracted value, normalized. `null` if the text has no value for the field |
| `raw_value` | The answer of the LLM before normalization. `null` if normalization changed nothing |
| `previous_value` | The value from `existing_metadata` |
| `changed` | `value` differs from `previous_value` |
| `normalized` | Normalization changed the value |
| `field_label` | Label of the field in `language` |
| `processing.warnings` | Notes on the input, on values that were left out (a date the model wrote month-first, further entries of a list, entries without object form) and on geocoding, in the wording of [POST /generate](#post-generate); also that the text was cut to 150,000 characters |

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | Invalid JSON, body is no object, unknown content type URI, no text, see also [Input source](#input-source) |
| `404` | `NOT_FOUND` | Context, version, schema or field unknown (checked before the source is fetched); node not found |
| `422` | `VALIDATION_ERROR` | `schema_file` or `field_id` missing, a field does not fit or is over its length limit, provider or model not allowed; a node without text |
| `499` | none | The caller closed the connection |
| `500` | `INTERNAL_ERROR` | The extraction failed for a reason that is no LLM error |
| `502`, `503` | `LLM_*` | The LLM call failed, see [Errors](#errors) |
| `504` | `GENERATION_TIMEOUT` | Longer than `METADATA_AGENT_GENERATE_TIMEOUT` |
| `422`, `502`, `503`, `504` | `TEXT_EXTRACTION_*`, `UPSTREAM_ERROR` | The source could not be read |

**Example.**

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

Detects the content type of a text with one LLM call and lists the content
types of the context.

**Request.**

| Name | Type | Default | Meaning |
|---|---|---|---|
| input fields | | | See [Input source](#input-source) |
| `context` | string | `default` | Schema context |
| `version` | string | `latest` | Schema version |
| `language` | string | `de` | `de` or `en` |
| `llm_provider`, `llm_model` | string | configured | See [Provider and model per request](#provider-and-model-per-request) |

**Response `200`.** `available` is shortened here to one entry.

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

| Part | Meaning |
|---|---|
| `detected` | The content type found. Pass `schema_file` or `uri` on to `/generate` |
| `available` | All content types of the context and version |
| `method` | `llm`: the model named one of the offered types (`confidence: "high"`). `keywords`: the model gave no usable answer and the keyword detection stood in (`confidence: "low"`, the reason in `warnings`). Check such a result |
| `warnings` | Notes on the input and on the detection; also that the text was cut to 150,000 characters |

A refused key or an unreachable LLM service is an error, never a silent
`learning_material.json`.

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | Invalid JSON, body is no object, no text, see also [Input source](#input-source) |
| `404` | `NOT_FOUND` | Context or version unknown, or they have no content types; node not found |
| `422` | `VALIDATION_ERROR` | A field does not fit or is over its length limit, provider or model not allowed; a node without text |
| `499` | none | The caller closed the connection |
| `502`, `503` | `LLM_*` | The LLM call failed, see [Errors](#errors) |
| `504` | `GENERATION_TIMEOUT` | Longer than `METADATA_AGENT_GENERATE_TIMEOUT` |
| `422`, `502`, `503`, `504` | `TEXT_EXTRACTION_*`, `UPSTREAM_ERROR` | The source could not be read |

**Example.**

```bash
curl -s -X POST "$API/detect-content-type" \
  -H "Content-Type: application/json" \
  -d '{"text": "Workshop KI in der Bildung am 15. März 2026 in Berlin."}'
```

## Validation and export

### POST /validate

Checks a record against its schema and the core fields. Needs no LLM and no
repository.

**Request.** A [metadata body](#metadata-body), flat or wrapped, with these
options:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `metadata` | object | the body itself in the flat form | The record |
| `context` | string | `auto` | `auto`: from `contextName`, else `default` |
| `version` | string | `auto` | `auto`: from `schemaVersion`, else `latest`. A leading `v` is tolerated |
| `schema_file` | string | `auto` | `auto`: from `metadataset`, else `learning_material.json`. File name or content type URI |

What is checked:

| Check | Result |
|---|---|
| Required fields are filled. `[""]`, text of nothing but blanks and `{}` are not a value; `0` and `false` are | error |
| Object fields hold objects in the form of the schema, see [OBJEKTFELDER.md](../OBJEKTFELDER.md) (German) | error |
| Fields whose ID ends in `latitude`, `lat`, `longitude`, `lon` or `lng` are in range | error |
| Numbers, booleans, dates (`YYYY-MM-DD`), date and time (`YYYY-MM-DDTHH:MM`), times (`HH:MM` or `HH:MM:SS`), URLs (`http://` or `https://`). The day and the time must exist | warning, with a suggestion where one can be derived |
| Values of closed vocabularies. A value must be the key of a concept as the vocabulary spells it: the URI, else the `value`. A number or boolean is compared as its text: `3` is the concept `"3"`, `true` is `"true"` | warning; for the first 5 invalid values of a field with a suggestion where one can be derived. The suggestion is the key of the concept the value names by a label, by its `value` in another case or by the end of its URI, else of the concept with the nearest label (values of four characters and more only). `Deutsh` gives `de` |

**Response `200`.**

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

| Part | Meaning |
|---|---|
| `valid` | `true` if `errors` is empty. Warnings do not make a record invalid |
| `schema_used` | The schema file that was applied |
| `errors`, `warnings` | Entries with `field_id`, `message`, `severity` |
| `coverage` | Percentage of the required fields that are filled |

The lists are bounded. Per field and check at most 20 findings are listed; one
more entry then counts the values that were not checked. A response holds at
most 500 errors and 500 warnings; one more entry with `field_id: "_limit"`
counts the rest. A value quoted in a message is cut to 200 characters.

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `200` | none | Result in the body |
| `400` | `BAD_REQUEST` | Invalid JSON, body is no object, unknown content type URI |
| `404` | `NOT_FOUND` | Context, version or schema file unknown, whether named by an option or by `contextName`, `schemaVersion`, `metadataset` of the record |
| `413` | `REQUEST_TOO_LARGE` | Body larger than 2 MiB |
| `422` | `VALIDATION_ERROR` | `contextName`, `schemaVersion`, `metadataset` or an option of the wrong type or over its length limit |

**Example.**

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

Renders a record as a Markdown document with the labels of the schema: every
field of `core.json` and of the content type once, one section per group, the
groups of `core.json` first. Fields of a group that neither schema declares
follow in a last section `Weitere Angaben` (`Further details` in English). The
title is the name of the content type; for `core.json` it is the file name.

**Request.** A [metadata body](#metadata-body), flat or wrapped, with these
options:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `metadata` | object | the body itself in the flat form | The record |
| `context`, `version`, `schema_file` | string | `auto` | As in [POST /validate](#post-validate) |
| `language` | string | `auto` | Language of the labels. `auto`: `language` of the record, else `de`. In the flat form this is the `language` key of the record |
| `include_empty` | boolean | `false` | Also list fields without a value |

**Response `200`.**

```json
{
  "markdown": "# Veranstaltung\n\n## Beschreibung\n\n**Titel:** Workshop KI in der Bildung\n\n## Typisierung\n\n**Schlagwörter:** KI, Bildung\n\n## Termin & Status\n\n**Start (Datum/Zeit):** 2026-03-15T09:00\n",
  "schema_used": "event.json"
}
```

`schema_used` names the schema file the record was rendered with. Groups and
fields without a value are left out unless `include_empty` is set; `0` and
`false` are values, also inside a list or an object.

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `200` | none | The document |
| `400` | `BAD_REQUEST` | Invalid JSON, body is no object, unknown content type URI |
| `404` | `NOT_FOUND` | Context, version or schema file unknown, as in [POST /validate](#post-validate) |
| `413` | `REQUEST_TOO_LARGE` | Body larger than 2 MiB |
| `422` | `VALIDATION_ERROR` | `contextName`, `schemaVersion`, `metadataset`, `language` or an option of the wrong type or over its length limit |

**Example.**

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

Five operations work on the edu-sharing repository named by
`METADATA_AGENT_REPOSITORY_URL`:

| Operation | Use it to |
|---|---|
| [POST /node](#post-node) | Get a node ID before the metadata exist |
| [POST /nodes](#post-nodes) | Create a node or change one, and get the full node back. No duplicate check, no screenshot, no workflow unless asked |
| [POST /upload](#post-upload) | Hand a finished record to the editors: duplicate check, preview screenshot and review workflow by default |
| [POST /workflow/{node_id}](#post-workflownode_id) | Set further workflow states |
| [POST /upload/verify/{node_id}](#post-uploadverifynode_id) | Check what the repository really stored |

### Common rules for writes

**Service account.** All five need `WLO_GUEST_USERNAME` and
`WLO_GUEST_PASSWORD`. Without them the answer is `503` `SERVICE_UNAVAILABLE`.

**Editing window.** An existing node can be written through this API only up
to **2 hours after it was created** (`cm:created`). Editing does not extend the
window. After that the content has usually reached the editors, and later
changes belong into the editorial interface. This holds for `/nodes` and
`/upload` with `node_id` and for `/screenshot` with `node_id`:

| Status | `error.code` | Meaning |
|---|---|---|
| `403` | `FORBIDDEN` | The node is older than 2 hours, or its creation time cannot be read |
| `404` | `NOT_FOUND` | The node does not exist, or the service account has no access |
| `502` | `UPSTREAM_ERROR` | The node could not be read because of a repository error. Try again |

**What is written.** Only fields that the schema marks for the repository:
those of `core.json` plus those of the content type in `metadataset`. Before
writing, the agent converts some values:

| Input | Written as |
|---|---|
| `cm:author: ["Max Müller"]` | `ccm:lifecyclecontributer_author` as vCard |
| Coordinates in `schema:location[].geo` or `schema:geo` | `cm:latitude`, `cm:longitude` |
| `ccm:custom_license` with a Creative Commons link, for example `https://creativecommons.org/licenses/by-sa/4.0/` | `ccm:commonlicense_key: ["CC_BY_SA"]` and `ccm:commonlicense_cc_version: ["4.0"]`. `publicdomain/zero` becomes `CC_0`, `publicdomain/mark` becomes `PDM`. If the link stands inside a sentence, or names the jurisdiction of a ported license (`…/by/3.0/de/`), the text is kept as well |
| `ccm:custom_license` with a URI of the license vocabulary under `http://w3id.org/openeduhub/vocabs/` (or `https://`), for example `…/vocabs/licenses/CC_BY_40` | `ccm:commonlicense_key: ["CC_BY"]` and `ccm:commonlicense_cc_version: ["4.0"]` |
| `ccm:custom_license` with free text or a link that cannot be assigned, also a link of another host that ends in a license key | Kept, with `ccm:commonlicense_key: ["CUSTOM"]` |
| `ccm:commonlicense_key` in another spelling: `CC BY-SA`, `cc-by-sa`, `CC0` | The key edu-sharing resolves: `CC_BY_SA`, `CC_0`. A key edu-sharing does not know is removed. A CC key without a version gets `4.0`, `CC_0` gets `1.0` |
| No license | `ccm:commonlicense_key` stays empty. The agent sets no default; the editors decide |
| Booleans, nested lists, text of nothing but blanks | `"true"` or `"false"`; one flat list; not written |

With `write_extended_data` three more fields are written:
`ccm:oeh_extendedType` (URI of the content type), `ccm:oeh_extendedData` (the
metadata fields of the record as JSON, without the extended fields themselves)
and `ccm:oeh_extendedText` (`extended_text`, the raw source text, at most
150,000 characters; `error` says when it was cut).

**Order of a write.** Duplicate check (only `/upload`), create the node or
check the editing window, metadata, extended data, preview image, collection
references, workflow, read-back. The duplicate check asks the search index and
reads the 25 newest nodes of the inbox folder, because the index lags behind.
Check and create run one after the other per URL within one process of the
agent.

**Collections.** `collection_id` is always a list, also for one collection. A
bare string is refused with `422`. Entries are node IDs or collection URLs
copied from the editorial interface
(`…/components/collections?id=<uuid>`). The node is referenced in the
collection; the original stays in the inbox folder. Collections named in the
record (`virtual:collection_id_primary`, `ccm:collection_id`) are added,
duplicates removed. The response has one entry per collection in `collections`.
A collection that already references the node counts as linked.

**Results.** A write has three kinds of outcome:

| Outcome | Status | Body |
|---|---|---|
| Written | `200` | `success: true`. If a later step failed (a field, a collection, a workflow step, the preview), `error` names it and `field_errors`, `collections`, `workflow` or `preview` show the details. Check `error` also when `success` is `true` |
| Duplicate, or the node exists but the write broke off after it | `200` | `success: false` with `error` and `node`; for a duplicate also `duplicate: true` |
| Nothing written | `422`, `502` | Error shape, see below |

| Status | `error.code` | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | Invalid JSON, body is no object |
| `422` | `VALIDATION_ERROR` | The body does not fit the model, holds an option of the other write operation or an unknown key beside a nested `metadata`, or a field violates the object form of the schema. Nothing was written |
| `422` | `SCHEMA_UNAVAILABLE` | Context or version of the record unknown, or `metadataset` names no schema that can be loaded (a typo, a file the version does not have). Beside `error`: `step: "schema"`. Nothing was written |
| `502` | `UPSTREAM_ERROR` | The repository refused the node or all metadata, or was not reachable. Beside `error`: `discarded_node` (a node this call created and removed again), `node` (a node that could not be removed), `step`, `field_errors` |
| `504` | `UPLOAD_TIMEOUT` | The write passed `METADATA_AGENT_UPLOAD_TIMEOUT`. `discarded_node`: the incomplete node this call created was removed. `node`: a node that stays, because a collection may already reference it, it is already in the workflow, completely written, could not be removed, or was passed in with `node_id`. `collections`: only if the deadline passed while the collections were linked; one entry per collection, with the reason in `error` for those not linked |

A discarded node lies in the recycle bin of the repository and can be restored.

Response models return optional fields without a value as `null`. The examples
below leave them out.

### POST /node

Creates a node in the inbox folder and returns its ID, without writing the
full metadata. For callers that need an ID before the metadata exist, to show
it, link it or pass it on. Hand the ID to `/upload` or `/nodes` as `node_id`
within 2 hours.

**Request.**

| Name | Type | Default | Meaning |
|---|---|---|---|
| `metadata` | object | required | At least `cclom:title`. Also taken: `cclom:general_description`, `cclom:general_keyword`, `ccm:wwwurl`, `cclom:general_language`. Everything else is ignored |
| `check_duplicates` | boolean | `false` | Search for a node with the same `ccm:wwwurl` first |

The node also gets `ccm:linktype: USER_GENERATED`. Its name (`cm:name`) comes
from `cm:name` in the metadata if given, else from the URL, else from the
title. A name from the metadata or the title is cleaned first: the characters
`* " \ > < ? / : |` are replaced, at most 200 characters are kept.

**Response `200`.**

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

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `200` | none | Created (`success: true`, `node`), or a duplicate was found (`success: false`, `duplicate: true`, `node` is the existing node). If the duplicate check could not run, the node is created and `error` says so |
| `400` | `BAD_REQUEST` | No title; invalid JSON, body is no object |
| `422` | `VALIDATION_ERROR` | `metadata` missing or of the wrong type |
| `502` | `UPSTREAM_ERROR` | The repository refused or was not reachable |
| `503` | `SERVICE_UNAVAILABLE` | No service account |
| `504` | `UPLOAD_TIMEOUT` | Deadline passed. A node that was created anyway is named in `node`; it is never removed here |

**Example.**

```bash
curl -s -X POST "$API/node" \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"cclom:title": "Bruchrechnung Klasse 6"}}'
```

### POST /nodes

Writes a record to the repository: creates a node (without `node_id`) or
updates one (with `node_id`). The response always holds the complete node. This
is the intended way to change a node the agent created.

**Request.** A [metadata body](#metadata-body) with these options:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `node_id` | string | none | Node to update, inside its editing window. Not set: a new node in the inbox folder |
| `collection_id` | string array | none | Collections to reference the node in |
| `write_extended_data` | boolean | `false` | Write the three extended fields |
| `extended_text` | string | none | Raw source text. Only with `write_extended_data: true` |
| `preview` | string | none | Preview image: an `http(s)` URL the agent loads, a data URL, or bare Base64. PNG, JPEG, GIF or WebP up to 10 MB; the type is read from the image itself |
| `start_review_workflow` | boolean | `false` | Hand the node over for review (`200_tocheck`) |
| `start_quality_workflow` | boolean | `false` | Confirm the quality (`140_ELEMENT_LEGALLY_APPROVED`), after the handover if both are set |

Behavior:

- **An update writes only what the body holds.** Empty values (`null`, `""`,
  text of nothing but blanks, `[]`) count as not set and delete nothing.
- An option that only `/upload` has (`check_duplicates`, `start_workflow`,
  `workflow_steps`, `preview_url`, `source` and the others) is refused with
  `422`.
- `ccm:oeh_extendedData` is one JSON block and is replaced as a whole. That is
  why `write_extended_data` is off by default here. Switch it on when you send
  the complete record.
- Without `preview` an existing preview image stays. A preview that cannot be
  loaded does not undo the write: `success` stays `true`, the reason is in
  `preview.error` and in `error`.
- Without the two workflow switches the node enters no queue.

**Response `200`.**

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

| Part | Meaning |
|---|---|
| `node_created` | `true` if this call created the node, `false` for an update |
| `schema_used` | The content type schema that decided what may be written. `null`: only `core.json` applied |
| `repo_fields_available` | Number of fields this schema may write. Far fewer than expected means a wrong or missing `metadataset` |
| `fields_written`, `field_errors` | Result of the metadata write: number of properties written, and the fields the repository refused |
| `fields_skipped`, `skipped_fields` | Number of fields that were not written, and the names of those that were sent with a value but reach no repository property, mostly because the schema does not mark them for the repository. Refused fields count too; they are named in `field_errors` |
| `node` | Short form: `nodeId`, `title`, `description`, `wwwurl`, `repositoryUrl` |
| `node_full` | The complete edu-sharing node as the repository returns it (shortened above). `null` only if the read-back failed; the write still counts |
| `preview`, `collections`, `workflow` | Only if requested |

**Status and error codes.** See [Common rules for writes](#common-rules-for-writes):
`400`, `403`, `404`, `422`, `502`, `503`, `504`.

**Example.**

```bash
# Update: only the fields in the body change
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

Uploads a record for the editors. By default it checks for a duplicate,
creates a node, writes the metadata and the extended data, captures the page
as preview image and hands the node over for review (`200_tocheck`).

**Request.** A [metadata body](#metadata-body) with these options:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `check_duplicates` | boolean | `true` | Refuse the upload if a node with the same `ccm:wwwurl` exists |
| `node_id` | string | none | Write into this node instead of creating one, see below |
| `collection_id` | string array | none | Collections to reference the node in |
| `source` | string | none | Publisher. Replaces `ccm:oeh_publisher_combined` |
| `write_extended_data` | boolean | `true` | Write the three extended fields |
| `extended_text` | string | none | Raw source text for `ccm:oeh_extendedText`, for example `_source_text` of the `/generate` response. It is not taken from the record by itself |
| `preview_url` | string | `ccm:wwwurl` of the record | Page to capture as preview image. Without any URL there is no preview |
| `screenshot_method` | string | `playwright` | `playwright` or the old alias `pageshot` |
| `start_workflow` | boolean | `true` | Run the workflow steps |
| `workflow_steps` | string array | `["200_tocheck"]` | States to set in order, see [POST /workflow/{node_id}](#post-workflownode_id). Unknown states: `422` |
| `workflow_comment` | string | `Upload via Metadata Agent API` | Comment of every step |
| `workflow_receiver` | string array | see below | Authority names that are notified at every step |
| `return_full_node` | boolean | `false` | Read the node back and return it as `node_full` |
| `repository` | string | `staging` | Deprecated and ignored |

Empty values: an empty string or an empty list counts as "not given" for
`node_id`, `collection_id`, `source`, `preview_url`, `extended_text`,
`workflow_comment` and `screenshot_method`. Two fields differ:

| Field | An empty list means |
|---|---|
| `workflow_receiver` | Notify nobody. Not set: the group `GROUP_ORG_WLO-Uploadmanager` for `200_tocheck`, nobody for other states |
| `workflow_steps` | Refused with `422`. For an upload without a workflow set `start_workflow: false` |

The options that only `/nodes` has (`preview`, `start_review_workflow`,
`start_quality_workflow`) are refused with `422`.

**Two-step upload with `node_id`.** Create the node with
[POST /node](#post-node), then send the record here with its ID:

| | Without `node_id` | With `node_id` |
|---|---|---|
| Duplicate check | runs | skipped; the node already holds the URL |
| Node after a failure | removed, unless a collection or the workflow may already hold it | stays; it belongs to the caller |
| Editing window | none | 2 hours after creation |
| `node_created` in the response | `true` | `false` |

**Response `200`.**

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

The fields have the meaning described for [POST /nodes](#post-nodes). In
addition:

| Part | Meaning |
|---|---|
| `duplicate` | `true` if the URL already exists. Then `success: false`, `node` is the existing node and `error` names it, by its title or, without one, as `Node <id>` |
| `preview` | Result of the screenshot: `success`, `method`, `capture_time_ms`, `size_bytes`, or `success: false` with `error`. A failed preview does not undo the upload |
| `collections` | One entry per collection: `collectionId`, `success`, `error` |
| `workflow` | One entry per step: `status`, `success`, `error`; `skipped: true` for steps that were not sent because an earlier one failed |
| `node_full` | Only with `return_full_node: true`. Fields in edu-sharing notation: `ref.id`, `properties["cclom:title"][0]` |
| `field_errors` | Fields the repository refused: `field_id`, `error`, `status_code` |

More on the response: [UPLOAD-RESPONSE.md](../UPLOAD-RESPONSE.md) (German).
Collections and workflow for developers:
[ENTWICKLER-UEBERSICHT.md](../ENTWICKLER-UEBERSICHT.md) (German).

A duplicate:

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

**Status and error codes.** See [Common rules for writes](#common-rules-for-writes):
`400`, `422`, `502`, `503`, `504`; with `node_id` also `403` and `404`.

**Example.**

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

Sets workflow states on an existing node, one after the other. edu-sharing
records every step as its own entry in the workflow history of the node, with
state, comment, time and acting user. That is why the states are set step by
step and not in one jump. The acting user is the service account. This
operation has no editing window.

**Request.** `node_id` in the path must be a node UUID.

| Name | Type | Default | Meaning |
|---|---|---|---|
| `steps` | string or string array | required | States to set in order. At least one |
| `comment` | string | `Upload via Metadata Agent API` | Comment of every step. An empty string counts as not given |
| `receiver` | string array | `["GROUP_ORG_WLO-Uploadmanager"]` for `200_tocheck`, else nobody | Authority names that are notified. An empty list: nobody |

States of the WLO editorial workflow:

| State | Meaning |
|---|---|
| `100_unchecked` | Unchecked |
| `110_METADATA_RECORD_REQUESTED` | Metadata record requested |
| `120_METADATA_QUALITY_CONFIRMED` | Metadata quality confirmed |
| `125_METADATA_QUALITY_FOR_BUFFET` | Qualified for the editorial buffet |
| `130_ELEMENT_REJECTED` | Element rejected |
| `140_ELEMENT_LEGALLY_APPROVED` | Quality confirmed, element approved |
| `150_PUBLISH_IN_SEARCH` | Published in the search |
| `160_REMOVE_FROM_SEARCH` | Removed from the search |
| `200_tocheck` | Handed over for review (default after `/upload`) |
| `TASK_CREATE_TREE`, `TASK_CHECK_COLLECTION_PROPOSAL`, `TASK_CHECK_QUALITY` | Task states |

**Response `200`.**

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

| Part | Meaning |
|---|---|
| `steps` | Result per step. After a failed step the remaining ones are not sent and are listed with `skipped: true` |
| `current_status` | `ccm:wf_status` of the node, read back after the steps |
| `history` | Workflow history of the node, passed on as the repository returns it. Besides state, comment and time each entry names the acting user |
| `error` | Set if a step failed or the repository was not reachable |

If one of the two read operations fails, its field is missing. The steps that
ran still count.

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `200` | none | Result in the body. `success: false` if a step failed or the repository was not reachable |
| `400` | `BAD_REQUEST` | `node_id` is no UUID, body missing, invalid JSON or no object |
| `404` | `NOT_FOUND` | Node unknown or not visible. No state is set |
| `422` | `VALIDATION_ERROR` | Unknown state, `steps` empty or missing |
| `503` | `SERVICE_UNAVAILABLE` | No service account |
| `504` | `UPLOAD_TIMEOUT` | Deadline passed. `node` names the node; single steps may already be set |

**Example.**

```bash
curl -s -X POST "$API/workflow/3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9" \
  -H "Content-Type: application/json" \
  -d '{"steps": ["140_ELEMENT_LEGALLY_APPROVED"], "comment": "Redaktionell geprüft"}'
```

### POST /upload/verify/{node_id}

Reads a node from the repository and reports which of its values the
repository does not resolve. With `expected_metadata` it also compares the node
field by field with what was sent. A `200` from a write does not prove that
every value arrived as a usable value; this operation shows it.

**Request.** `node_id` in the path must be a node UUID. The body is optional.

| Name | Type | Default | Meaning |
|---|---|---|---|
| `expected_metadata` | object | none | The record that was sent, for example the response of `/generate`, in any form `/upload` takes: flat, under `metadata` or under `metadata.metadata`. Options of `/upload` and `/nodes` beside the fields are not compared. Not set: no comparison |
| `repository` | string | `staging` | Deprecated and ignored |

A body that is present but broken is refused. It never falls back to a plain
read, because that would report a comparison that did not take place.

**Response `200`.**

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

| Part | Meaning |
|---|---|
| `actual_metadata` | The node's fields as flat metadata |
| `unresolved` | Values the repository stores but does not read as values. A vocabulary field needs a filled `<field>_DISPLAYNAME`, `ccm:commonlicense_key` needs `virtual:licenseurl`. Reported with and without `expected_metadata`. An empty list: everything resolves |
| `unresolved[].known` | `true` for fields that have no value space in the repository (`ccm:oeh_quality_correctness`, the three `ccm:commonlicense_ai_*`). They are stored correctly and appear on every node. Real findings come first |
| `diff`, `summary` | Only with `expected_metadata`. Problems first |
| `diff[].resolution` | `resolved`, `unresolved` or `null` (nothing to resolve). Independent of `status`: a field can be a `match` and still not resolve |
| `summary` | The number of fields per `status`. `unresolved` counts, beside them, the compared fields whose value the repository does not resolve; such a field can be a `match` at the same time |

| `status` | Meaning |
|---|---|
| `match` | Expected and stored values agree. The comparison is tolerant: a single value equals a list of one, the order of a list does not count, a date may be stored as milliseconds. Upper and lower case is ignored for vocabulary values and the license key, not in free text. A field the agent writes in another form is compared in that form: `oeh:new_lrt` as `ccm:oeh_lrt`, a license link in `ccm:custom_license` as key and version, `cm:author` by the names |
| `mismatch` | The value was written but differs |
| `missing_in_repo` | Expected, but not on the node |
| `extra_in_repo` | On the node, but not expected. Properties the agent derives from a sent field (license key and version, the author's vCard) do not count |
| `not_written` | The field is never written. It is no repository field of the schema, neither under its own name nor under the name it is written as (`oeh:new_lrt` counts as `ccm:oeh_lrt`), or it is a `virtual:` field, which the repository computes itself. If the repository fields of the schema cannot be read, this holds for every field |

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | `node_id` is no UUID, invalid JSON, body is no object |
| `404` | `NOT_FOUND` | Node unknown, or the service account has no access |
| `422` | `VALIDATION_ERROR` | The body does not fit, for example `schemaVersion` is a number |
| `502` | `UPSTREAM_ERROR` | The repository could not deliver the node |
| `503` | `SERVICE_UNAVAILABLE` | No service account |

**Example.**

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

Captures a web page with a local Chromium browser and returns the image. With
`node_id` the image is also stored as preview image of that node. The capture
runs inside the agent; no external screenshot service is involved.

**Request.**

| Name | Type | Default | Meaning |
|---|---|---|---|
| `url` | string | required | Page to capture. [Public URLs only](#public-urls-only) |
| `method` | string | `playwright` | `playwright` or the old alias `pageshot`. Both capture locally; the response says `playwright` |
| `width` | integer | `1920` | Viewport width, 320 to 3840 |
| `height` | integer | `1080` | Viewport height, 200 to 2160 |
| `format` | string | `png` | `png` or `jpeg`. Other values are delivered and labeled as PNG |
| `full_page` | boolean | `false` | Capture the whole scrollable page |
| `delay` | integer | `2000` | Wait before the capture in ms, 0 to 10000 |
| `node_id` | string | none | Store the image as preview of this node. Must be a UUID |
| `repository` | string | `staging` | Deprecated and ignored |

The page is captured when its HTML is loaded and the network is quiet. The
agent waits at most 10 s for a quiet network and captures anyway after that,
then waits `delay`. If the page, a redirect, a frame or an image leads to an
internal address, the capture is discarded.

**Response `200`.**

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

`success` says that the capture succeeded. Without `node_id` in the request,
`preview_uploaded`, `node_id` and `error` are `null`; the example leaves them
out. With `node_id`, `preview_uploaded` says whether the image was stored and
`node_id` repeats the node. If the repository refuses the image, the response
is still `200` with the image, `preview_uploaded: false` and the reason in
`error`. Without the service account a request with `node_id` is answered with
`503` before the capture.

**Status and error codes.**

| Status | `error.code` | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | The URL, a redirect or a frame is not public; invalid JSON, body is no object |
| `403`, `404` | `FORBIDDEN`, `NOT_FOUND` | With `node_id`: outside the editing window, or node unknown. Checked before the capture |
| `422` | `VALIDATION_ERROR` | A field does not fit, for example `width` out of range or a `url` over 8,192 characters |
| `500` | `INTERNAL_ERROR` | The capture failed unexpectedly |
| `502` | `UPSTREAM_ERROR` | The page could not be loaded or captured within 90 s. With `node_id` also: the repository could not be read |
| `503` | `SERVICE_UNAVAILABLE` | With `node_id`: no service account. Checked before the capture |
| `504` | `UPLOAD_TIMEOUT` | With `node_id`: capture and storing passed `METADATA_AGENT_UPLOAD_TIMEOUT`. `node` names the node |

**Example.**

```bash
curl -s -X POST "$API/screenshot" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "format": "jpeg", "width": 1280, "height": 800}'
```

## Schema information

Three read-only operations describe the schemas the agent knows. They need no
LLM and no repository. An unknown context, version or schema file is answered
with `404` `NOT_FOUND`, a path parameter over its length limit (see
[Limits and timeouts](#limits-and-timeouts)) with `422`.

### GET /info/schemata

Lists the contexts and their versions.

**Response `200`.**

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

`default_version` is what `latest` means.

```bash
curl -s "$API/info/schemata"
```

### GET /info/schemas/{context}/{version}

Lists the schema files of a context and version. `version` may be `latest`.

**Response `200`.** Shortened here to two schema files.

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

`label` is the name of the content type, as in the `available` list of
[POST /detect-content-type](#post-detect-content-type). For `core.json`, which
is no content type, it is the file name.

```bash
curl -s "$API/info/schemas/default/latest"
```

### GET /info/schema/{context}/{version}/{schema_file}

Returns one schema definition as JSON: `profileId`, `version`, `@context`,
`groups`, `fields` and `output_template`. Each field has `id`, `group`,
`label`, `description`, `prompt` and `system` (data type, `required`,
`multiple`, `repo_field`, vocabulary).

**Response `200`.** `event.json`, shortened to one prefix, one group, one
field and three keys of the template.

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

## Widget endpoints

The agent delivers the web component `<metadata-agent-canvas>` itself.
Attributes, layouts and events are described in
[WIDGET-REFERENZ.md](../WIDGET-REFERENZ.md) (German).

| Path | Content |
|---|---|
| `GET /widget/info` | How to embed the component, as JSON |
| `GET /widget/i18n/{lang}.json` | UI texts. `lang` is `de` or `en`, otherwise `404` (`422` for more than 8 characters). `Cache-Control: public, max-age=3600` |
| `GET /widget/dist/…` | The built component: `runtime.js`, `polyfills.js`, `main.js`, `styles.css`, fonts |
| `GET /widget/examples/…` | Example pages, for example `full.html`, `detail.html`, `minimal.html` |

`GET /widget/info` returns these keys:

| Key | Content |
|---|---|
| `name`, `version`, `description` | `metadata-agent-canvas`, the version of the agent |
| `dist_base_url`, `scripts` | Base of the files (`dist`) and the lists `required` (`dist/runtime.js`, `dist/polyfills.js`, `dist/main.js`), `styles`, `fonts` |
| `variants` | `full`, `detail`, `minimal`, each with `description`, `example_url` and an HTML `snippet` |
| `layouts`, `layout_aliases` | Layout names and their aliases |
| `attributes`, `events`, `incoming_events` | Attributes and events with a short description |
| `examples`, `example_data` | URLs of the example pages (`examples/full.html`) and of an example record |

The URLs in the response are relative references. Resolve them against the
address under which you called `/widget/info`: `dist/main.js` becomes
`…/widget/dist/main.js`, through the proxy as well. In the HTML snippets the
placeholder `{AGENT_URL}` stands for the base URL of the agent; replace it
before you use a snippet.

**Response `200` of `GET /widget/info`.** Shortened to one variant, one
layout, two attributes, one event and three example pages.

```json
{
  "name": "metadata-agent-canvas",
  "version": "4.0.1",
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

**Response `200` of `GET /widget/i18n/{lang}.json`.** `de.json`, shortened to
two of its groups.

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

## Operations endpoints

These endpoints are not part of `/openapi.json`.

| Endpoint | Port 8000 | Internal port (`METADATA_AGENT_INTERNAL_PORT`) |
|---|---|---|
| `GET /health` | yes, without configuration details | yes, with `llm_provider` and `b_api_auth_mode` |
| `GET /ready` | yes, without reasons | yes, with `problems` |
| `GET /metrics` | no (`404`) | yes |
| `GET /debug/auth`, `GET /debug/dependencies` | only with `METADATA_AGENT_DEBUG_ENDPOINTS=true`, otherwise `404` | no |

The internal port is a second server in the same process that the ingress
does not expose. Without `METADATA_AGENT_INTERNAL_PORT` it does not exist, and
there is no `/metrics`. See [DEPLOYMENT](../DEPLOYMENT.md) and
[UEBERGABE.md](../UEBERGABE.md) (German).

### GET /health

Liveness: the process answers. It says nothing about the dependencies.

```json
{ "status": "healthy", "version": "4.0.1" }
```

```bash
curl -s "$API/health"
```

### GET /ready

Readiness: the configuration allows the agent to work. It calls no other
service.

```json
{ "status": "ready" }
```

`503` `NOT_READY` if the configuration is incomplete:

| Reason | Condition |
|---|---|
| No schema contexts loaded | always checked |
| `B_API_KEY` missing | B-API provider with `METADATA_AGENT_B_API_AUTH_MODE=static` |
| `OPENAI_API_KEY` missing | provider `openai` |
| `WLO_GUEST_USERNAME` or `WLO_GUEST_PASSWORD` missing | unless `METADATA_AGENT_REQUIRE_REPOSITORY=false` (a deployment that only extracts) |

The `503` has the error shape plus `"status": "not_ready"`. The reasons are in
the log and, as `problems`, on the internal port.

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$API/ready"
```

### GET /metrics

Prometheus metrics, on the internal port only. Besides the process metrics:

| Metric | Labels |
|---|---|
| `metadata_agent_http_requests_total`, `…_duration_seconds` | `method`, `route` (the route template, never an ID), `status` (also `499`) |
| `metadata_agent_generations_total` | `status`: `success`, `partial`, `failed`, `cancelled` (the caller left), `refused` (`TOO_MANY_GENERATIONS`) |
| `metadata_agent_llm_requests_total`, `…_duration_seconds` | The counter by provider, result (`ok`, `cancelled` or the error code), `auth_source` and `config`; the histogram by provider only. It measures what the caller of one LLM call waits: for a free slot under the LLM limits of the process, for the request, its retries and the pauses between them |
| `metadata_agent_llm_last_failure_timestamp_seconds` | The labels of the LLM counter; Unix time of the last LLM call that ended in an error |
| `metadata_agent_http_last_internal_error_timestamp_seconds` | `route`; Unix time of the last answer `500` |
| `metadata_agent_llm_tokens_total` | provider and kind of token: `prompt`, `cached` (read from the prompt cache), `cache_write` (written to it), `completion` |
| `metadata_agent_upstream_requests_total`, `…_responses_total`, `…_response_duration_seconds` | `service`: `llm`, `text-extraction`, `repository`, `geocoder`, `other`. The responses also by `status`; for `other` by status class (`2xx`, `4xx`) |

The histogram of the request duration has buckets up to 300 s, from 120 s on
every 30 s (150 to 270 s), so the share of slow `/generate` runs can be read
from it.

```bash
# The Helm chart sets the internal port to 9000
curl -s "http://localhost:9000/metrics"
```

### GET /debug/auth and GET /debug/dependencies

Help while a deployment is set up. They exist only with
`METADATA_AGENT_DEBUG_ENDPOINTS=true` and answer `404` otherwise. Switch them
off again afterwards.

`GET /debug/auth` shows whether a forwarded key arrives. It never shows a key,
only presence, scheme and length:

```json
{
  "mode": "auto",
  "Authorization": { "present": true, "scheme": "Bearer", "length": 36 },
  "X-API-KEY": { "present": false },
  "b_api_key_configured": true,
  "llm_calls_use": "forwarded"
}
```

`llm_calls_use` is `forwarded`, `static` or `null` (no key available).

`GET /debug/dependencies` calls the B-API, the text extraction, the geocoder
and the repository once, with the key of the request, and reports per service
`url`, `reachable`, `status`, `ms` and, where a key is involved, `auth_source`
and `authorized`. It also returns `version`, `llm_provider`,
`b_api_auth_mode` and `config_warnings`. The call uses no LLM tokens.

```bash
curl -s "$API/debug/auth" -H "Authorization: Bearer $B_API_KEY"
curl -s "$API/debug/dependencies" -H "Authorization: Bearer $B_API_KEY"
```

## Usage examples

### From a web page to a reviewed node

The usual flow: extract, validate, upload, verify.

```bash
API=http://localhost:8000

# 1. Extract from a page. The response is the record.
curl -s -X POST "$API/generate" \
  -H "Content-Type: application/json" \
  -d '{"input_source": "url", "source_url": "https://example.com/workshop"}' \
  > record.json

# 2. Validate: the record goes in unchanged.
curl -s -X POST "$API/validate" \
  -H "Content-Type: application/json" -d @record.json

# 3. Upload: the record unchanged. Options would stand beside the fields.
curl -s -X POST "$API/upload" \
  -H "Content-Type: application/json" -d @record.json \
  > upload.json

# 4. Verify with the nodeId from upload.json.
curl -s -X POST "$API/upload/verify/<nodeId>" \
  -H "Content-Type: application/json" \
  -d "{\"expected_metadata\": $(cat record.json)}"
```

Before step 3, check `processing.status` in `record.json`. With `partial`,
`processing.errors` names the fields that are missing.

### Multi-line text without JSON

```bash
curl -s -X POST "$API/generate" \
  -H "Content-Type: text/plain" \
  --data-binary 'Workshop KI in der Bildung
Am 15. März 2026 in Berlin.
Zielgruppe: Lehrkräfte aller Schulformen.
Kosten: 49 Euro'
```

### Correction round

Send the fields of the first result back as `existing_metadata` and put the
instruction into `text`. `_source_text` among them makes the request a
correction.

```bash
# Fields, _origins and _source_text of the first result, without header and processing
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

### Node ID first, metadata later

```bash
# 1. Create the node and keep its ID.
curl -s -X POST "$API/node" \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"cclom:title": "Bruchrechnung Klasse 6"}}'

# 2. Within 2 hours: write the record into that node.
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

### Python: handling partial results and errors

```python
import httpx

API = "http://localhost:8000"
# Longer than METADATA_AGENT_GENERATE_TIMEOUT (300 s)
client = httpx.Client(base_url=API, timeout=320)

response = client.post("/generate", json={
    "text": "Fortbildung: Digitale Werkzeuge im Unterricht, am 20. April 2026 "
            "in der Stadthalle Hamburg, Dammtorwall 10, 20355 Hamburg.",
    "schema_file": "event.json",
})

if response.status_code == 503 and "Retry-After" in response.headers:
    raise SystemExit(f"Busy, retry in {response.headers['Retry-After']} s")
if response.is_error:
    error = response.json()["error"]
    raise SystemExit(f"{error['code']} (request {error['request_id']})")

record = response.json()
processing = record["processing"]
if processing["status"] == "partial":
    print("Missing fields:", processing["errors"])
for warning in processing["warnings"]:
    print("Note:", warning)

validation = client.post("/validate", json=record).json()
if validation["valid"]:
    upload = client.post("/upload", json={**record, "check_duplicates": True}).json()
    if upload["success"]:
        print("Uploaded:", upload["node"]["repositoryUrl"])
    if upload.get("error"):
        print("Note:", upload["error"])
```
