# Steckbrief: Metadata Agent

Stand 17.11.2026. Beschrieben ist die Version 4.0.4 (Agent und Webkomponente
gemeinsam, auf GitHub als `v4.0.4` und auf GitLab als `0.1.5` getaggt am
17.11.2026). Aufbau nach der Vorlage „Technischer Service-Steckbrief“; die
Erläuterungen je Zeile stehen unter [Details](#details). Die Änderungen
gegenüber 3.0.1 stehen im [CHANGELOG](../CHANGELOG.md).

Status: **vorhanden** / **nicht vorhanden** (etwas, das es geben muss),
**geprüft** (eine Eigenschaft ist festgestellt), **In Klärung**,
**In Nacharbeit**. In Confluence als Status-Makro übernehmen.

## Stammdaten

| | |
|---|---|
| Service | Metadata Agent (Dienst-ID `metadata-agent`) |
| Funktionalität | Erzeugt Metadaten aus Text, Webseite oder Repository-Eintrag, prüft sie und schreibt sie ins WLO-Repository; mit Webkomponente zum Bearbeiten |
| Stabile Version | **vorhanden** (4.0.4) |
| Technischer Owner | Jan Schachtschabel (Vertretung offen) |
| Jira | **vorhanden** (DAT-108, SDWLO-1564) |
| Repository | **vorhanden** ([metadata-agent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent), [metadata-agent-webcomponent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent-webcomponent)) |
| OpenAPI Spec | **vorhanden** (`GET /openapi.json`) |
| Pipeline und Git-Tags | **vorhanden** (Tags `vX.Y.Z` werden benutzt, `.gitlab-ci.yml` im Repo) |
| Endpunkte | **vorhanden** (öffentlich: `/generate`, `/extract-field`, `/upload`, `/nodes` u. a.; Infrastruktur: `/health`, `/ready`, `/metrics`) |
| Endpunkte auf zwei Ports | **vorhanden** (8000 API, intern 9000) |
| Header / Content-Types | **geprüft** (`Authorization`, Trace-Header; JSON, für die Webkomponente JS/CSS/Schriften) |
| Asynchrone Job-Endpunkte | **geprüft** (keine, alles synchron; meist 5–22 s, Frist 300 s) |
| Readiness Status | **In Nacharbeit** |

## Architektur

![Architektur-Map, Betrieb über die B-API](architektur/metadata-agent-architektur-b-api.png)

| | |
|---|---|
| Überblick | FastAPI-Anwendung mit Webkomponente (Angular) |
| Horizontale Skalierbarkeit | **geprüft** (ja) |
| Integriert in B-API | **geprüft** (ja, Proxy-Eintrag `metadata-agent-canvas`) |
| Schreibt Suggestions | **geprüft** (nein) |
| Nutzt Routing | **geprüft** (ja) |
| Nutzt API Keys | **geprüft** (ja, Delegations-Token der B-API) |
| Repo über B-API | **geprüft** (nein, direkt) |
| Benötigte Dienste | **geprüft** (B-API, Textextraktion, Photon-Geocoder, Repository) |

## Deployment und Infrastruktur

| | |
|---|---|
| HELM | **vorhanden** ([helm/metadata-agent](../helm/metadata-agent/README.md); noch nicht im Cluster installiert) |
| Docker-Files | **vorhanden** (`Dockerfile`, `docker-compose.yml`) |
| Abschaltbare Komponenten | **geprüft** (ja: Ingress, NetworkPolicy, Monitoring, Repository-Zugang) |
| Datenbank | **geprüft** (nein) |
| Große Daten | **geprüft** (nein) |

## Security und Konfiguration

| | |
|---|---|
| Authentifizierung | **geprüft** (keine eigene; Schutz durch B-API-Proxy und NetworkPolicy) |
| Network Policy | **vorhanden** (im Chart) |
| ENV Variablen | **vorhanden** ([ENV-PARAMETER.de.md](../ENV-PARAMETER.de.md)) |
| Benötigte Secrets | **geprüft** (`WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`; `B_API_KEY` nur ohne B-API-Proxy) |

## Monitoring und Betrieb

| | |
|---|---|
| Prometheus | **vorhanden** (`/metrics` auf dem internen Port) |
| Alarmregeln | **vorhanden** (13 Regeln als `PrometheusRule`, mit promtool getestet) |
| B3-Tracing | **vorhanden** (B3 und `x-client-trace-id` übernommen und weitergegeben) |

## Ressourcen und Kosten

| | |
|---|---|
| CPU, RAM und Storage | **In Nacharbeit** (geschätzt: Requests 500m / 512Mi, Limits 2 CPU / 2Gi, kein Storage; Messung unter Last fehlt) |
| Kostenschätzung | **vorhanden** (rund 0,007–0,017 € je Erschließung mit `gpt-6-luna`) |

---

## Details

| Punkt | Ergänzung |
|---|---|
| Namen | Dienst-ID `metadata-agent`; Proxy-Eintrag `metadata-agent-canvas` und Image `metadata-agent-api` bleiben aus Kompatibilität |
| Versionen | 4.0.4 (17.11.2026); GitHub-Tags `vX.Y.Z`, GitLab-Tags `0.1.x` (`0.1.5` = 4.0.4); Entwicklung auf [GitHub](https://github.com/janschachtschabel/metadata-agent-10-2026) |
| Pipeline | Tests, Lint, Typen, `helm lint`, kubeconform, promtool, Image-Bau mit Trivy-Scan; Tag muss zur Version passen |
| Endpunkte | Liste und Beispiele: [API.de.md](API.de.md); über den B-API-Proxy nur die Pfade der OpenAPI; `/debug/*` standardmäßig aus |
| Header | Ein: `Authorization` (Delegations-Token), B3, `x-client-trace-id`, `X-Request-ID`; aus: `X-Request-ID`, `Retry-After`; im Proxy-Eintrag freizugeben |
| Laufzeit | `/generate` meist 5–22 s, Frist 300 s mit Teilergebnis kurz davor; Proxy-Timeout ≥ 310 s, Body bis 20 MB |
| B-API | `forwardDelegation: true`, `delegationScopes: llm`, kein `delegationHeader`, `responseTimeout` ≥ 320 000 ms ([b-api-kurzinfo.md](b-api-kurzinfo.md)) |
| Anmeldung | Token je Anfrage unverändert weiter, nie im Log; hinter der B-API `METADATA_AGENT_B_API_AUTH_MODE=forward` ohne `B_API_KEY` |
| Routing | Standard `b-api-openai` mit `gpt-6-luna`; Route per Env (`b-api-router`); AcademicCloud reicht nicht für Produktivlast |
| Repository | direkt per REST mit Dienstkonto (lesen, schreiben, Workflow `200_tocheck`); mit dem Token ginge Schreiben nicht |
| Ausweichen | lehnt die B-API Textextraktion oder Geocoder ab: Photon direkt, Textextraktion über `METADATA_AGENT_TEXT_EXTRACTION_FALLBACK_URL` |
| Skalierung | Replicas ohne gemeinsamen Zustand; GWDG-Limit teilt der Chart durch die Replicas (`sharedLlmLimit`); kein Autoscaling |
| Image | Multi-Stage, `python:3.13-slim` per Digest, kein Root, `tini`, nur lesbares Dateisystem; groß durch Chromium (Größe nicht gemessen) |
| Monitoring | Metriken zu HTTP, LLM (Tokens, Cache, Herkunft der Anmeldung), Erschließungen, abhängigen Diensten; Empfänger der Alarme legt Team 6 fest |
| Kosten | gemessen 16.10. und 15.11.2026; Prompt-Cache spart rund 15 %; Photon kostenlos; Datenresidenz-Aufschlag der B-API offen |

## Offene Punkte

1. **Proxy-Eintrag** `metadata-agent-canvas` auf `forwardDelegation` umstellen (Werte oben), `responseTimeout` von 300 000 auf ≥ 320 000 ms.
2. **Agent dahinter** mit `forward`, ohne `B_API_KEY`, mit `METADATA_AGENT_TEXT_EXTRACTION_FALLBACK_URL` betreiben; ob Textextraktion und Geocoder über die B-API laufen, im Log oder in den Nutzungsdaten der B-API bestätigen.
3. **Mit T6:** direktes Schreiben ins Repository mit eigenem Konto zulassen?
4. **Chart** erstmals im Cluster installieren; bis zum GitLab-Tag `v4.0.4` `image.tag=main` setzen.
5. **edu-sharing:** Einbindung testen und die Attribute aus dem [Hinweis unten](#hinweis-zur-einbindung-in-edu-sharing) übernehmen.
6. **Messen:** CPU und RAM unter Last, P50/P90 je Endpunkt, Image-Größe.
7. **Vertretung** und Zeile in der Kompatibilitätsmatrix.
8. **Webkomponente:** Karma auf Vitest umstellen (eigenes Release).

## Abweichungen vom Entwicklerleitfaden

| Abweichung | Begründung |
|---|---|
| Synchron bis 300 s statt Jobs | Erschließung meist 5–22 s |
| Kein Weiterarbeiten ohne LLM | ohne LLM keine Metadaten; Fehler mit Grund |
| Modellname statt Route als Standard | läuft ohne angelegte Route; per Env umstellbar |
| `/health`, `/ready` auch auf Port 8000 | HEALTHCHECK des Images |
| Repository direkt, nur WLO/OEH-Schemata | B-API deckt Schreiben nicht ab; eigenes Arbeitspaket |
| Chart unter `helm/metadata-agent`, nicht in einer Registry | historisch; kein Autoscaling wegen GWDG-Limit |
| Kein OpenTelemetry | Trace-Header ja, Spans nein |
| `503` auch für Konfigurationsfehler | wie bisher, für Aufrufer stabil |

## Bekannte Einschränkungen

- Webkomponente und Agent gehören zusammen; das Widget trägt die Version des Agenten.
- `/screenshot`, `/upload`, `/workflow` prüfen keine Rechte selbst (Schutz durch Proxy und NetworkPolicy).
- Änderungen für Aufrufer seit 3.0.1: CHANGELOG, „Vor dem Update prüfen“.

## Dokumentation

[UEBERGABE](../UEBERGABE.md) · [DEPLOYMENT](../DEPLOYMENT.de.md) · [ENV-PARAMETER](../ENV-PARAMETER.de.md) · [INSTALL](../INSTALL.de.md) · [API](API.de.md) · [B-API-Kurzinfo](b-api-kurzinfo.md) · [Architektur](architektur/README.md) · [Helm-Chart](../helm/metadata-agent/README.md) · [CHANGELOG](../CHANGELOG.md) · [Audits](audits/)

## Hinweis zur Einbindung in edu-sharing

edu-sharing setzt die Einstellungen des Widgets erst nach dem Laden der Skripte; bis dahin ist kurz der Eingabebereich zu sehen, bei jedem neu angelegten Element. Abhilfe: die Einstellungen als **Attribute ins Template**. Die späteren Zuweisungen im Code können bleiben.

```html
<!-- Formular-Editor, Feld ccm:oeh_extendedData -->
<metadata-agent-canvas #canvas
    [attr.layout]="viewer ? 'detail' : 'clean'"
    [attr.readonly]="viewer ? 'true' : null"
    [attr.columns]="groupId === 'io_render' ? 4 : 1"
    [attr.language]="language"
    show-input-area="false"
    show-core-fields="false"
    show-floating-controls="false"
    highlight-ai="true">
</metadata-agent-canvas>

<!-- Detailansicht (wlo-extended-content-types) -->
<metadata-agent-canvas #canvas
    layout="detail" readonly columns="1" borderless="true"
    background-color="transparent" show-floating-controls="false"
    [attr.language]="language">
</metadata-agent-canvas>
```

`viewer`, `groupId`, `language`: die Werte, die edu-sharing heute erst nach dem Laden setzt. Im Browser geprüft (Widget 4.0.4). Ausführlich: [README der Webkomponente, „Einbettung in edu-sharing“](https://github.com/janschachtschabel/metadata-agent-webcomponent-10-2026/blob/main/README.de.md#einbettung-in-edu-sharing).
