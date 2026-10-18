# Audit-Korrekturen Phase 3 und 4 (3.0.0)

Quelle: `docs/audits/2026-10-17-audit-3.0.0.md` §9 (Roadmap), Schritte 9–12.
Arbeitsordner: `metadata-agent-3`, `metadata-agent-webcomponent-3`. Keine
Commits. Version bleibt 3.0.0. Neue Dev-Werkzeuge (freigegeben): `pytest-cov`,
`mypy`, `axe-core`.

Regeln: je Befund Test zuerst (rot aus dem richtigen Grund), Umbauten
verhaltenserhaltend (volle Suite + API-Vergleich mit 2.1.1 vorher/nachher),
Review je Phase, Doku im selben Schritt.

## Reihenfolge und Begründung

Toter Code zuerst (weniger zu zerlegen), dann Coverage messen (Netz für die
Umbauten), dann zerlegen. Widget danach, weil es unabhängig ist. Pflege zuletzt.

| # | Befund | Änderung | Nachweis |
|---|---|---|---|
| 1 | M-03 toter Code | `LLMService.normalize_with_llm`, `_parse_normalization_response`, `_build_normalization_prompt` (+ zweiter Prompt-Bauer) entfernen; Widget `app.module.ts`, `InstanceRegistry.isPrimary` prüfen und entfernen | Suche ohne Aufrufer; Suite grün; Bundle baut |
| 2 | DOC-01 | README/Plan „Normalisierung per LLM“ korrigieren, `DEPLOYMENT.md`/`INSTALL.md` auf 3.0.0, Abschnitt Forwarded-Header/TLS (mit OPS-01) | Doku-Diff |
| 3 | T-01 Coverage | `pytest-cov` in dev; Messung als Basis; `fail_under` knapp unter dem Ist-Wert in `pyproject.toml`; CI (GitHub, GitLab) misst; direkte Tests für Kernpfade mit geringer Abdeckung, die M-01 umbaut | Coverage-Bericht vorher/nachher |
| 4 | M-01 Backend | `metadata_service.py` teilen: Validierung → `metadata_validation.py`, Markdown-Export → `metadata_export.py`; `MetadataService.generate_metadata`, `validate_metadata`, Router `generate_metadata`, `LLMService._build_extraction_prompt`, `extract_field` in benannte Schritte zerlegen; Ziel C901 ≤ 15 je Funktion | Charakterisierungstests vor dem Umbau; Suite; API identisch; `ruff --select C901` |
| 5 | M-04 Mixins + mypy | `mypy` in dev, Konfiguration für `src/api`, `src/services/llm_*`, `src/services/repository_*`, `metadata_*`; „Needs:“ als `Protocol`/Attribut-Annotation statt nur Docstring; zustandslose Mixins prüfen und, wo wenige Aufrufer, in Modulfunktionen überführen | `mypy` ohne Fehler auf dem Umfang; CI-Schritt |
| 6 | U-01 Tastatur | Vokabular-Picker: öffnen per Enter/Space/Pfeil, `role="dialog"`/`listbox`, Fokusfalle (CDK `A11yModule`), Escape schließt und gibt Fokus zurück; Gruppenköpfe in allen Layouts als `<button>` mit `aria-expanded`/`aria-controls` | Specs (Tastatur, Fokus), axe |
| 7 | U-05 Dialoge, globale Regeln, Namen | `alert()`/`confirm()` durch Inline-Meldung/Bestätigung im Widget ersetzen; globale Regeln aus `src/styles/main.scss` auf den Host begrenzen; Icon-Buttons mit `aria-label` (i18n) | Specs; Sichtprüfung im Browser (Screenshot) |
| 8 | T-02 + axe | `axe-core` in Karma: Prüfung der Kernkomponenten (Feld, Picker, Layout) ohne Verstöße der Stufen serious/critical; Specs für `canvas.component` (Upload, Doppelklick U-02 nur falls im Weg), Picker, `field-validation.service` | Karma |
| 9 | DEP-01 | `requirements.txt` per `uv export --no-dev --format requirements-txt` mit Hashes; Test: gleich dem Export aus `uv.lock` | Test |
| 10 | DEP-02 | CI: `pip-audit` (uv export), `npm audit --omit=dev` (Widget), Trivy auf das Image (GitHub + GitLab) | CI-Konfiguration geprüft (YAML-Lint), lokal soweit möglich |
| 11 | OPS-01 | uvicorn `--proxy-headers --forwarded-allow-ips` aus Env (`FORWARDED_ALLOW_IPS`, Default `127.0.0.1`), Chart-Wert; `/widget/info` liefert hinter TLS `https://` | Test mit `X-Forwarded-Proto` |
| 12 | OPS-02 | Dockerfile: `COPY --chown` statt `chown -R`, `/app/src` nicht beschreibbar; Chart `readOnlyRootFilesystem: true` mit `emptyDir` für `/tmp` und Chromium-Home; Basis-Images per Digest | Chart-Test, helm lint, kubeconform; Image-Bau falls möglich |
| 13 | OPS-03 | GitHub-Actions per Commit-SHA gepinnt; Image-Smoke-Test (Container starten, `/health`) in GitHub und GitLab; überflüssigen GitLab-Job `uv-install` entfernen | YAML-Prüfung |
| 14 | OPS-04 | `METADATA_AGENT_INTERNAL_PORT` im Template aus `service.internalPort` ableiten, nicht doppelt in `values.yaml` | Chart-Test |

Nicht in diesem Plan (nicht in Phase 3/4 der Roadmap): S-04, S-06, S-08, L-03–L-05,
D-01, D-02, P-02, P-04, U-02, U-04, U-07, U-08, T-03–T-05. Sie bleiben im Audit
offen; Fundstellen in §6.

## Nach jeder Phase

Review (`/better-coding-review`), Befunde beheben, dann Doku: CHANGELOG 3.0.0
beider Repos, Audit §11 „Stand der Behebung“, UEBERGABE/Steckbrief wo Betrieb
betroffen. Abschluss: volle Prüfung (pytest + Coverage, ruff, mypy, helm lint,
kubeconform, promtool, Karma + axe, Widget-Build + Deploy nach `metadata-agent-3`,
Probelauf).
