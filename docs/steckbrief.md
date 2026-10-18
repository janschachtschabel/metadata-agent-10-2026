# Steckbrief: Metadata Agent API

Entstanden aus der ausführlichen Beschreibung unter [UEBERGABE.md](../UEBERGABE.md)
(Stand 17.10.2026, Version 3.0.0).

| | |
|---|---|
| **Service** | Metadata Agent API (inkl. Webkomponente `metadata-agent-canvas`) |
| **Technischer Owner** | **[eintragen]** |
| **Readiness Status** | Handlungsbedarf |
| **Offene Punkte** | 9 |
| **Kritische Offene Punkte** | 2 |
| **Jira** | **[eintragen, z.B. GEN-357]** |

## Stammdaten & API

| Punkt | Text oder Link | Status |
|---|---|---|
| Anzeigename und Beschreibung | `Metadata Agent API`: erzeugt aus Text, Webseite oder Repository-Eintrag Metadaten (Titel, Beschreibung, Schlagworte, Bildungsstufe …) und schreibt sie ins WLO-Repository (`info.summary` der Spec) | erfüllt |
| Repository-Link | GitLab: [metadata-agent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent), [metadata-agent-webcomponent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent-webcomponent). GitHub (privat, 2.1.1): [metadata-agent-10-2026](https://github.com/janschachtschabel/metadata-agent-10-2026), [metadata-agent-webcomponent-10-2026](https://github.com/janschachtschabel/metadata-agent-webcomponent-10-2026). Zugriff für T6 offen | offen |
| OpenAPI Spec | `/openapi.json` aus dem Code; 16 Operationen beschrieben; Swagger UI `/docs` ohne Dritt-Server | erfüllt |
| Pipeline und Git-Tags | GitLab-CI: Branch-Images und Versions-Image je Tag (Variablen: UEBERGABE §1), dazu Prüfung von Chart (`helm lint`) und Alarmregeln (`promtool`). GitHub Actions mit Tests, Chart-Prüfung und Image-Bau. **Kritisch:** 3.0.0 im GitLab-Repo nicht committet, Tag `v3.0.0` fehlt | offen |
| Endpunkte (öffentliche und Infrastruktur) | Port 8000: API, `/docs`, `/openapi.json`, `/widget/*`. Interner Port (z.B. 9000): `/health`, `/ready`, `/metrics`. `/debug/*` standardmäßig aus (im Chart ausdrücklich), der interne Port ist nicht im Ingress | erfüllt |
| spezielle Header und Content-Types | ein: `Authorization: Bearer` (B-API, `forwardApiKey`), `x-client-trace-id`, B3; aus: `X-Request-ID`. API nur JSON; Widget-Dateien (JS, CSS, `woff2`, HTML) mit Wolli abstimmen | offen |
| Asynchrone Job-Endpunkte | keine. `/generate` dauert 12–22 s (`gpt-6-luna`), bis 90 s (AcademicCloud) → Timeout ≥ 120 s; Async nur nötig, falls der Proxy das nicht erlaubt | offen |

## Architektur

| Punkt | Text oder Link | Status |
|---|---|---|
| Übersicht | Widget in edu-sharing → B-API-Proxy → Agent → B-API LLM, Textextraktion, edu-sharing, Photon-Geocoder. Grafiken: [Map](architektur/metadata-agent-architektur.png), [Ablauf](architektur/metadata-agent-ablauf.png), [interaktiv](architektur/README.md) | erfüllt |
| zustandslos oder zustandsbehaftet | zustandslos; keine Sitzungen, kein gemeinsamer Zustand zwischen Pods, keine Volumes | erfüllt |
| Horizontale Skalierbarkeit | ja, über Replicas (ein Worker je Container); GWDG-Limit (~10 Aufrufe/min, höchstens 2 gleichzeitig) gilt für alle Pods gemeinsam; der Chart teilt es mit `sharedLlmLimit` durch die Replicas | erfüllt |

## Deployment und Infrastruktur

| Punkt | Text oder Link | Status |
|---|---|---|
| Docker-Files | `Dockerfile` (Multi-Stage, `python:3.13-slim`, Nutzer `appuser`, HEALTHCHECK) und `docker-compose.yml` im Repo | erfüllt |
| HELM-Chart und Beispiel-Values | [helm/metadata-agent/](../helm/metadata-agent/README.md): Deployment (Probes intern), Service, PodDisruptionBudget, NetworkPolicy (Namespaces anpassen), Secret, ServiceMonitor, PrometheusRule; Ingress abschaltbar, hinter der B-API aus; `helm lint` und kubeconform grün (beide in der CI), noch nicht im Cluster installiert; Prüfliste für Staging und Rollback in DEPLOYMENT.md | erfüllt |
| Abschaltbare Komponenten | keine; nur der interne Port ist optional | nicht anwendbar |
| Datenbank | keine | nicht anwendbar |
| Quelle und Volume-Größe für große Daten | keine; im Image nur Schemata (8 MB), Widget (2,5 MB), Swagger UI (1,8 MB) | nicht anwendbar |

## Security und Konfiguration

| Punkt | Text oder Link | Status |
|---|---|---|
| Authentifizierungskonzept | mit B-API (Variante A): Proxy reicht den Key per `forwardApiKey` durch, der Dienst nutzt ihn nur je Anfrage (`B_API_AUTH_MODE`). **Kritisch:** Eintrag in der B-API und Staging-Test fehlen | offen |
| mit B-API: Routing, virtueller Key, Berechtigungen | Standard `gpt-6-luna` (`b-api-openai`), optional `b-api-router` mit Route oder `provider/modell`. Virtueller Key nötig; Rechte: LLM-Endpunkte (`openai`, `router`), kein `LLM_ROUTE_MANAGE` | erfüllt |
| ohne B-API: LLM-Keys und Authentifizierung | `B_API_KEY` (wie 2.0.x) oder `OPENAI_API_KEY`; keine eigene Auth-Schicht, Zugang muss ein Proxy absichern | erfüllt |
| Env-Variablen inklusive Defaults | [ENV-PARAMETER.md](../ENV-PARAMETER.md), `.env.template` und Helm-`values.yaml`, alle per Test mit dem Code abgeglichen | erfüllt |
| Benötigte Secrets (ohne Werte) | `B_API_KEY` (entfällt im Modus `forward`), `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD`; `OPENAI_API_KEY` nur ohne B-API (dann in `secret.keys` des Charts ergänzen) | erfüllt |

## Monitoring und Betrieb

| Punkt | Text oder Link | Status |
|---|---|---|
| Prometheus-Endpunkt | `/metrics` auf dem internen Port | erfüllt |
| Applikationsmetriken und Alarmregeln | HTTP, LLM-Aufrufe (Ergebnis, Dauer), Erschließungen, Aufrufe an abhängige Dienste, Tokens. Zehn Alarmregeln als `PrometheusRule` im Chart, per promtool getestet; kritisch nur für eigenen Key und konfiguriertes Modell (UEBERGABE §4); Empfänger legt T6 fest | erfüllt |
| Log-Format und x-client-trace-id | stdout, JSON mit `trace_id` oder Text; Trace-Header gehen an B-API, Textextraktion und Repository, nie an Fremddienste | erfüllt |

## Ressourcen und Kosten

| Punkt | Text oder Link | Status |
|---|---|---|
| CPU, RAM und Storage | geschätzt 500m / 512Mi (Requests) bis 2 CPU / 2Gi (Limits), kein Storage; Image groß durch Chromium (~400 MB). Unter Last messen | offen |
| Erwartete LLM-Kosten | 0,007–0,017 € je Erschließung mit `gpt-6-luna`, Input-Caching 66–93 % (gemessen 16.10.2026, UEBERGABE §7); gebucht auf den Key des Aufrufers | erfüllt |
| Kosten externer APIs | Photon-Geocoder kostenlos; Datenresidenz-Aufschlag der B-API (OpenAI 10 %, Azure EU 20 %) klären | offen |

## Übergabe

| Punkt | Text oder Link | Status |
|---|---|---|
| Ansprechperson in T4 | **[Name, Kontakt]**; wer Updates anstößt, ist offen | offen |
| Bekannte Einschränkungen und offene Punkte | siehe unten | erfüllt |
| Links zu Betriebsdokumentation und Nachweisen | [UEBERGABE.md](../UEBERGABE.md), [DEPLOYMENT.md](../DEPLOYMENT.md), [ENV-PARAMETER.md](../ENV-PARAMETER.md), [Helm-Chart](../helm/metadata-agent/README.md), [team6-anforderungen.md](team6-anforderungen.md), [Audit 15.10.2026](audits/2026-10-15-audit.md), [Audit 17.10.2026](audits/2026-10-17-audit-3.0.0.md), [CHANGELOG](../CHANGELOG.md) | erfüllt |

## Offene Punkte

**Kritisch**
1. 3.0.0 im GitLab-Repo committen und als `v3.0.0` taggen (API und Webkomponente).
2. Mit dem Helm-Chart auf Staging deployen, Dienst in der B-API mit `forwardApiKey` eintragen und testen (DEPLOYMENT.md, „Nach dem Deployment prüfen“); dabei prüfen, ob Prometheus die Metriken und Alarmregeln übernimmt.

**Offen**
3. Technischer Owner, Ansprechperson T4 und Jira-Ticket eintragen.
4. Festlegen, wer Updates anstößt (Abhängigkeiten, Patches, Modellwechsel).
5. Repo-Zugriff für T6 bestätigen.
6. Content-Types der Widget-Dateien mit Wolli abstimmen.
7. Entscheiden, ob Async-Jobs nötig sind.
8. CPU und RAM unter Last messen.
9. Abrechnungsweg der B-API klären (Datenresidenz-Aufschlag).

**Bekannte Einschränkungen**
- `/generate` braucht bis 90 s und bricht nach 110 s selbst ab (`504`); Proxy und Ingress brauchen ≥ 120 s Timeout und 20 MB Body-Größe.
- Statuscodes in 3.0.0 vereinheitlicht (Breaking für Clients, CHANGELOG): Abruffehler `502`, unbekannter Node `404`, ungültiger Body `422`.
- Das GWDG-Kontingent (AcademicCloud) reicht nicht für Produktivlast; Standard ist daher `gpt-6-luna`.
- `/screenshot`, `/upload` und `/workflow` prüfen keine Rechte selbst; der Schutz liegt beim B-API-Proxy und der NetworkPolicy des Charts. Lesezugriffe laufen mit dem Service-Konto (Entscheidung offen, UEBERGABE §3).
