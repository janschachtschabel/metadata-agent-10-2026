# Fixes for the release review 2.1.1 → 3.0.0 (2026-10-18)

The review compared the 2.1.1 working tree (`metadata-agent`, `metadata-agent-webcomponent`)
with 3.0.0 (`-3`) in seven read-only passes. Result: 0 critical, 4 major, 24 minor,
~10 nits, plus pre-existing items. Every item: verify against source first, failing test
first, smallest fix, no commits. Findings that turn out wrong are reported, not "fixed".

## Strands and file ownership (no strand edits another strand's files)

| Strand | Owns | Findings |
|---|---|---|
| A – write path | `src/services/repository_*.py`, `src/models/schemas.py` (ErrorResponse only), `src/models/repository_rules.py`, routers `upload.py`, `save_node.py`, `screenshot.py`, `nodes.py`; tests for these | M2 preview/read-back inside the upload deadline, M3 `_failed` after workflow, #6 lost-node candidate in workflow, #7 cleanup timeouts per phase, #8 `POST /node` without D-01, #9 outage → 404, #10 `/screenshot` without deadline, window check before capture, #21 write-path test gaps + preview bytes, nits (`_set_preview`, user agent 2.0, docstrings, cut-off wording, ErrorResponse extra keys), pre-existing (LRT fallback over node LRT on partial update, partial extended-field success, `vocabulary_fields` cache keeps incomplete set, raw exception texts in upload/node/verify-free paths, unretrieved task exceptions on cancel in upload) |
| B – LLM and values | `src/services/llm_*.py`, `llm_service.py`, `metadata_*.py`, `field_normalizer.py`, `output_normalizer.py`, `geocoding_service.py`, `src/utils/{dates_numbers,vocabulary,text_utils,schema_loader}.py`, routers `generate.py`, `extract.py`, `validation.py`; tests for these | M1 `/validate` 500, #11 geocoding inside the generate deadline, #13 uri+skos labels, #14 empty answers retried, #15 non-strings in closed vocabularies, #16 non-JSON 200, #17 user-origin fields sent to the LLM, nit stale comment, pre-existing (tracing headers to OpenAI, non-string startDate, unlogged content-type errors, URI substring match, schema cache key per raw version, `gather` without cleanup, raw error texts, non-object body message, unretrieved task exception in generate release) |
| C – infra, API edge, docs, CI | `src/config.py`, `src/infra.py`, `src/api/{responses,openapi,input_resolver,deps}.py`, routers `detect.py`, `verify.py`, `schemata.py`, `infra.py`, `widget.py`, `src/services/{input_source_service,screenshot_service}.py`, `helm/**`, CI files, compose, `.env.template`, Dockerfile, all backend docs incl. CHANGELOG, `docs/audits`; tests for these | #5 internal host in text-extraction errors, #12 CHANGELOG row node_url, #18 ingress vs NetworkPolicy, #19 UPLOAD_TIMEOUT in env/compose/upgrade, #20 doc drift, #21 helm tests skipped in GitLab, nits (log sanitising, dead `_ACCEPTED_GAPS` entry, `replicaCount=0`, start warnings for WLO_GUEST and timeout budget, CHANGELOG contradictions, DEPLOYMENT wording, provider check before fetch in detect), pre-existing (blocked URLs logged with query, `.env.template` placeholder keys, compose env passthrough, INSTALL dependency table, WIDGET-REFERENZ table, network test) |
| D – widget | all of `metadata-agent-webcomponent-3`, deploy into `metadata-agent-3/src/static/widget` | M4 `fieldValueCache`, #22 typed time vs timepicker directive, #23 spinner after instance switch, #24 text box after re-run, #25 labels after language switch, #26 licence check order, #27 Roboto OFL text, #28 deploy default target, nits (dead scss, colour token, README tree), pre-existing (upload error display incl. 422 and UPLOAD_TIMEOUT node info, English literals, import without `metadataset`/`_source_text`, `contentTypeGroups` on context switch, subfield origins, unhandled `loadMetadata`, dead canvas styles, README deploy steps, focus on disabled upload button) |

Strands A and B report their CHANGELOG lines; they are merged into the backend CHANGELOG
at the end. The audit report §11 gets one block for this round.

## Verification at the end

Backend: full pytest incl. browser tests (TEMP in `.pwtmp`), coverage, ruff, format, mypy,
`helm lint --strict`, promtool. Widget: both tsc configs, Karma with coverage floor,
production build, deploy, backend browser tests against the new bundle. Then a fresh
read-only review of this round's diff.
