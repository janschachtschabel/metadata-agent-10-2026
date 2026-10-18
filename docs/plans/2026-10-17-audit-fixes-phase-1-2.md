# Plan: Audit-Befunde Phase 1 + 2 (3.0.0)

Stand 17.10.2026. Grundlage: [Audit 3.0.0](../audits/2026-10-17-audit-3.0.0.md),
Fahrplan §9, Phasen 1 und 2. Arbeitskopien `metadata-agent-3` und
`metadata-agent-webcomponent-3`, keine Commits. Version bleibt 3.0.0 (noch nicht
veröffentlicht); Verhaltensänderungen stehen im CHANGELOG, Statuscode-Änderungen
unter „Breaking“.

Regeln: je Befund zuerst ein Test, der rot ist; dann die kleinste Änderung;
volle Suite nach jedem Schritt; Review je Phase.

## Phase 1 – Bedingungen

| # | Befund | Änderung | Test |
|---|---|---|---|
| 1 | S-03 SVG als Vorschau | `_sniff_image_mimetype` erkennt SVG weiter, `resolve_preview` lehnt es mit `PreviewError` ab | Data-URL und URL mit SVG → `PreviewError` |
| 2 | S-07 Tracing an fremde Hosts | Vorschau-Abruf mit eigenem Client (`THIRD_PARTY_HOOKS`) | Abruf trägt keine `x-client-trace-id` |
| 3 | L-06 Client-Leck bei Abbruch | `try/finally` um den Rest von `/generate` | Abbruch schließt den Override-Client |
| 4 | U-03 `localStorage` | Lesen/Schreiben in try/catch | Spec: werfender Speicher → Sprache aus Default, kein Fehler |
| 5 | S-02 + D-03 Vorschau-Upload | `RepositoryService.upload_preview(node_id, image, mimetype)` mit `_reject_if_too_old`; genutzt von `/screenshot`, `/upload`, `save_node`; in `/upload` vor dem Workflow | zu alter Node → 403 bei `/screenshot`; Reihenfolge Vorschau vor Workflow |
| 6 | S-01 NetworkPolicy | `templates/networkpolicy.yaml` (Ingress aus B-API-Namespace + Prometheus, Egress DNS/B-API/Internet ohne private Netze), `automountServiceAccountToken: false` | helm template + kubeconform; pytest prüft Werte |
| 7 | S-05 Lesezugriffe Service-Konto | Doku in UEBERGABE als offene Entscheidung | – |

## Phase 2 – Grundlagen

| # | Befund | Änderung | Test |
|---|---|---|---|
| 8 | API-02 500 ohne Request-ID/CORS | innerste Middleware fängt Ausnahmen, antwortet `failure_response(500, INTERNAL_ERROR)` | Endpunkt wirft → 500 mit `X-Request-ID`, `error.request_id`, CORS-Header |
| 9 | API-01 Statuscodes | Abruffehler überall 502; „Node nicht gefunden“ bei `/upload/verify` 404; ungültiger Body bei `/upload/verify` 422; Repository-Fehler bei `POST /node` 502. `/upload`/`/workflow` mit `success:false` bleiben 200 (Teilergebnis im Body, Widget wertet aus) | je Fall ein Test |
| 10 | A-01 + L-02 Eingangsquelle, Korrekturanweisung | `InputResolver` (Text, Quell-URL, Hinweise, Kürzung) für detect/extract/generate; Anweisung nach dem Holen anhängen, beim Kürzen reserviert; `_source_text` ohne Anweisung | URL-Quelle + Anweisung → Anweisung im Text; langer Text → Anweisung bleibt; `_source_text` ohne Anweisung |
| 11 | P-01 Gesamt-Zeitlimit | `METADATA_AGENT_GENERATE_TIMEOUT` (110 s), `asyncio.timeout` um die Generierung → 504 `GENERATION_TIMEOUT`, Feld-Tasks abgebrochen | langsamer Fake → 504 |
| 12 | P-03 Geocoding | höchstens 10 Orte je Anfrage geocodiert, Rest unverändert durchgereicht | 15 Orte → 10 Aufrufe |
| 13 | L-01 + U-06 Widget-Races | Stale-Prüfung nach jedem await in `applyGeneratedMetadata`/`regenerateWithContentType`; `selectContentType`, `loadMetadata`, `importJsonData` erhöhen den Zähler; Regenerate schickt die Kernwerte als `existing_metadata` | Specs: Reset während `prepareSpecialFields` → kein Überschreiben; Typwechsel während Extraktion → Lauf verworfen; Regenerate-Request enthält Kernwerte |

Nach Phase 2: Review, Doku (CHANGELOG, ENV-PARAMETER, UEBERGABE, Steckbrief,
Audit §10 „Stand der Behebung“), volle Prüfung (pytest, ruff, helm, promtool,
kubeconform, Karma, Build, Probelauf).
