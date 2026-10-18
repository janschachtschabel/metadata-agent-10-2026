# Fixes for the final pre-release audit 3.0.0 (2026-10-18)

Source: `docs/audits/2026-10-18-audit-3.0.0-final.md`. Every finding: verify against
source first (skip with reason if wrong), failing test first, smallest fix, no commits.
Compatibility with 2.1.1 (same parameters, same views) is the leading requirement.

Shared helper added before the strands start: `src/utils/loggable.py` (`loggable_url`),
tested in `tests/test_loggable_url.py` — use it for every URL that goes into a log line.

## Strands and file ownership

| Strand | Owns | Findings |
|---|---|---|
| A – write path | `src/services/repository_*.py`, routers `upload.py`, `save_node.py`, `nodes.py`, `verify.py`, `screenshot.py`, their tests | API-08, API-11 (verify part), API-12, typed write result + one response mapping, `/nodes` success tests, partial-update note for handed-over nodes, S-17 in upload.py, verify 422 shape (API-10 part), aspect PUT after failed GET (verify) |
| B – LLM, values, validation, generate | `src/services/llm_*.py`, `llm_service.py`, `metadata_*.py`, `field_normalizer.py`, `output_normalizer.py`, `geocoding_service.py`, `src/utils/{dates_numbers,vocabulary,text_utils,schema_loader}.py`, `src/models/schemas.py`, `src/api/input_resolver.py`, routers `generate.py`, `extract.py`, `validation.py`, `detect.py`, their tests | P-07, capacity as latitude, vocabulary retry keeps partial answers, closed vocabularies drop caller values (warn / only extracted), FieldNormalizer on unbounded lists, dates/numbers (thousands separator, impossible ISO dates, German date-time with comma/month names), weekdays, `[]` counted, schema helpers log, primer guard, API-06, API-07, API-09, API-10 (generate/validation), correction cap on plain re-extraction, extract core.json fallback, S-17 in generate.py |
| C – security, infra, ops, docs | `src/config.py`, `src/infra.py`, `src/main.py`, `src/metrics.py`, `src/api/{deps,responses,openapi}.py`, routers `infra.py`, `schemata.py`, `widget.py`, `docs.py`, `src/utils/{public_url,body_limit,security_headers,request_id,logging_setup,loggable}.py`, `src/services/{screenshot_service,input_source_service}.py`, helm, CI, Dockerfile, compose, `pyproject.toml`, `requirements.txt`, `uv.lock`, `tests/conftest.py`, `tests/test_layers.py`, all backend docs incl. CHANGELOG, NOTICE | S-14, S-15, S-16, S-17 (own files), S-18, S-19, `/ready` switch and error shape, `llm_max_retries ≥ 1`, Chromium/playwright update + browser-age check, 5xx alert + `for:` on UpstreamUnanswered, Dockerfile layer order, CI (OCI labels, base layer, CRD pin, `v*` tags, dev audit, image promotion), `starlette` declared, coverage floor, network guard, layer test, node_id source tests, all doc findings, CHANGELOG entries for the whole round |
| D – widget | all of `metadata-agent-webcomponent-3`, deploy into `metadata-agent-3/src/static/widget` | F1, F2 (compatibility), W-L1–W-L6, P-1, U-1–U-5, D-1, CHANGELOG headline, restore `FIELD.BOOLEAN.EMPTY`, `.visually-hidden` note, mixed deployment note, standalone `?apiUrl/?dataUrl`, encodeURIComponent, object fields filled, number parsing, upload during extraction, dead code/duplication, budgets, deploy order, T-2, T-3, dependency deprecations (builder migration only if everything stays green) |

Strands A, B and D report their CHANGELOG lines; strand C or the coordinator merges them.

## Verification at the end

Backend full suite incl. browser tests (`REQUIRE_HELM=1`), coverage, ruff, format, mypy,
helm lint, promtool; widget tsc, Karma with floor, production build, deploy; browser
re-check of review-demo.html and the README `metadataInput` flow against 2.1.1; then a
fresh read-only review of this round.
