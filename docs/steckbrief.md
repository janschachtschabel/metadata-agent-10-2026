# Steckbrief: Metadata Agent

Stand 20.10.2026, Version 3.0.1

| | |
|---|---|
| **Service** | Metadata Agent (metadata-agent inkl. metadata-agent-webcomponent) |
| **Technischer Owner** | @Jan Schachtschabel |
| **Readiness Status** | Handlungsbedarf |
| **Offene Punkte** | 6 |
| **Kritische Offene Punkte** | 2 |
| **Jira** | DAT-108, wg. Anpassungen SDWLO-1564 |

## Stammdaten & API

| Punkt | Text oder Link | Status |
|---|---|---|
| Anzeigename und Beschreibung | `Metadata Agent API`: erzeugt aus Text, Webseite oder Repository-Eintrag Metadaten (Titel, Beschreibung, Schlagworte, Bildungsstufe …) und schreibt sie ins WLO-Repository (`info.summary` der Spec) | erfüllt |
| Repository-Link | GitLab (Produktiv): [metadata-agent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent), [metadata-agent-webcomponent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent-webcomponent). GitHub (Entwicklung): [metadata-agent-10-2026](https://github.com/janschachtschabel/metadata-agent-10-2026), [metadata-agent-webcomponent-10-2026](https://github.com/janschachtschabel/metadata-agent-webcomponent-10-2026). | erfüllt |
| OpenAPI Spec | `/openapi.json` aus dem Code; 16 Operationen beschrieben; Swagger UI `/docs` ohne Dritt-Server | erfüllt |
| Pipeline und Git-Tags | GitLab-CI: Branch-Images und Versions-Image je Tag, dazu Prüfung von Chart (`helm lint`) und Alarmregeln (`promtool`). GitHub Actions grün (2.549 Tests, Chart-Prüfung, Image-Build; Stand 3.0.1). GitLab-Tag `v3.0.1` steht noch aus | erfüllt |
| Endpunkte (öffentliche und Infrastruktur) | Port 8000: API, `/docs`, `/openapi.json`, `/widget/*`. Interner Port (z.B. 9000): `/health`, `/ready`, `/metrics`; `/ready` verlangt die Repository-Zugangsdaten, abschaltbar mit `METADATA_AGENT_REQUIRE_REPOSITORY=false` (nur Generieren). `/debug/*` standardmäßig aus (im Chart ausdrücklich), der interne Port ist nicht im Ingress | erfüllt |
| spezielle Header und Content-Types | ein: `Authorization: Bearer` (B-API, `forwardApiKey`), `x-client-trace-id`, B3; aus: `X-Request-ID`. API nur JSON; Widget-Dateien (JS, CSS, `woff2`, HTML) abstimmen | offen |
| Asynchrone Job-Endpunkte | keine. `/generate` dauert 12–22 s (`gpt-6-luna`), bis 90 s (AcademicCloud), sehr lange Quellen werden auf 150.000 Zeichen gekürzt (Goethe-Artikel, 412.000 Zeichen: 35–42 s statt 126 s); Zeitlimit 300 s → Proxy-Timeout ≥ 310 s empfohlen; Async nur nötig, falls ein Proxy die ≥ 310 s nicht erlaubt | offen |

## Architektur

![Architektur-Map](architektur/metadata-agent-architektur.png)

| Punkt | Text oder Link | Status |
|---|---|---|
| Übersicht | Widget in edu-sharing → B-API-Proxy → Agent → B-API LLM, Textextraktion, edu-sharing, Photon-Geocoder. Grafiken: [Map](architektur/metadata-agent-architektur.png), [Ablauf](architektur/metadata-agent-ablauf.png), [interaktiv](architektur/README.md) | erfüllt |
| zustandslos oder zustandsbehaftet | zustandslos; keine Sitzungen, kein gemeinsamer Zustand zwischen Pods, keine Volumes | erfüllt |
| Horizontale Skalierbarkeit | ja, über Replicas (ein Worker je Container); aktuelles GWDG-Limit (~10 Aufrufe/min, höchstens 2 gleichzeitig) gilt für alle Pods gemeinsam; der Chart teilt es mit `sharedLlmLimit` durch die Replicas | erfüllt |

## Deployment und Infrastruktur

| Punkt | Text oder Link | Status |
|---|---|---|
| Docker-Files | `Dockerfile` (Multi-Stage, `python:3.13-slim`, Nutzer `appuser`, HEALTHCHECK) und `docker-compose.yml` im Repo | erfüllt |
| HELM-Chart und Beispiel-Values | [helm/metadata-agent/](../helm/metadata-agent/README.md): Deployment (Probes intern), Service, PodDisruptionBudget, NetworkPolicy (Namespaces anpassen), Secret, ServiceMonitor, PrometheusRule; Ingress abschaltbar, hinter der B-API aus; `helm lint` und kubeconform grün (beide in der CI), noch nicht im Cluster installiert; Prüfliste für Staging und Rollback in [DEPLOYMENT.md](../DEPLOYMENT.md) | erfüllt |
| Abschaltbare Komponenten | keine; nur der interne Port ist optional | nicht anwendbar |
| Datenbank | keine | nicht anwendbar |
| Quelle und Volume-Größe für große Daten | keine; im Image nur Schemata (8 MB), Widget (2,5 MB), Swagger UI (1,8 MB) | nicht anwendbar |

## Security und Konfiguration

| Punkt | Text oder Link | Status |
|---|---|---|
| Authentifizierungskonzept | mit B-API (Variante A): Proxy reicht den Key per `forwardApiKey` durch, der Dienst nutzt ihn nur je Anfrage (`B_API_AUTH_MODE`: `auto`/`forward`/`static`, gilt für alle B-API-Provider inkl. Router; Kurzinfo: [b-api-kurzinfo.md](b-api-kurzinfo.md)). **Kritisch:** Eintrag in der B-API und Staging-Test fehlen | offen |
| mit B-API: Routing, virtueller Key, Berechtigungen | Standard `gpt-6-luna` (`b-api-openai`), optional `b-api-router` mit Route oder `provider/modell`. Virtueller Key nötig; Rechte: LLM-Endpunkte (`openai`, `router`), kein `LLM_ROUTE_MANAGE`. Modellwahl pro Anfrage begrenzbar mit `METADATA_AGENT_ALLOWED_LLM_MODELS`. Einrichtung: [b-api-kurzinfo.md](b-api-kurzinfo.md) | erfüllt |
| Empfohlenes LLM Routing | openai mit `gpt-6-luna` und `gpt-5.6-luna` (Academiccloud ist mit aktuellen Limits nicht nutzbar) | erfüllt |
| ohne B-API: LLM-Keys und Authentifizierung | `B_API_KEY` (wie 2.0.x) oder `OPENAI_API_KEY`; keine eigene Auth-Schicht, Zugang muss ein Proxy absichern.<br>Steuerung über ENV: `METADATA_AGENT_B_API_AUTH_MODE`<br>Optionen: `auto`, `static` (wie bisher mit API Key), `forward` (neues Verfahren über b-api) | erfüllt |
| Env-Variablen inklusive Defaults | [ENV-PARAMETER.md](../ENV-PARAMETER.md), `.env.template` und Helm-`values.yaml`, alle per Test mit dem Code abgeglichen | erfüllt |
| Benötigte Secrets (ohne Werte) | `B_API_KEY` (entfällt im Modus `forward`), `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` (entfallen, wenn nur generiert wird: `METADATA_AGENT_REQUIRE_REPOSITORY=false`); `OPENAI_API_KEY` nur ohne B-API (dann in `secret.keys` des Charts ergänzen) | erfüllt |

## Monitoring und Betrieb

| Punkt | Text oder Link | Status |
|---|---|---|
| Prometheus-Endpunkt | `/metrics` auf dem internen Port | erfüllt |
| Applikationsmetriken und Alarmregeln | HTTP, LLM-Aufrufe (Ergebnis, Dauer), Erschließungen, Aufrufe an abhängige Dienste, Tokens. Zwölf Alarmregeln als `PrometheusRule` im Chart, per promtool getestet (u. a. 5xx und interne Fehler; langsame Erschließung ab p95 > 270 s); kritisch nur für eigenen Key und konfiguriertes Modell (UEBERGABE §4); Empfänger legt T6 fest | erfüllt |
| Log-Format und x-client-trace-id | stdout, JSON mit `trace_id` oder Text; Trace-Header gehen an B-API, Textextraktion und Repository, nie an Fremddienste | erfüllt |

## Ressourcen und Kosten

| Punkt | Text oder Link | Status |
|---|---|---|
| CPU, RAM und Storage | geschätzt 500m / 512Mi (Requests) bis 2 CPU / 2Gi (Limits), kein Storage; Image groß durch Chromium (~400 MB). Lastmessung ist offen. | offen |
| Erwartete LLM-Kosten | 0,007–0,017 € je Erschließung mit `gpt-6-luna`, Input-Caching 66–93 % (gemessen 16.10.2026, UEBERGABE §7) | erfüllt |
| Kosten externer APIs | Photon-Geocoder kostenlos; Datenresidenz-Aufschlag der B-API klären | offen |

## Übergabe

| Punkt | Text oder Link | Status |
|---|---|---|
| Ansprechperson in T4 | Team 4 (Manuel und Jan) | offen |
| Bekannte Einschränkungen und offene Punkte | siehe unten | erfüllt |
| Links zu Betriebsdokumentation und Nachweisen | [UEBERGABE.md](../UEBERGABE.md), [DEPLOYMENT.md](../DEPLOYMENT.md), [ENV-PARAMETER.md](../ENV-PARAMETER.md), [B-API-Kurzinfo](b-api-kurzinfo.md), [Architektur](architektur/README.md), [Helm-Chart](../helm/metadata-agent/README.md), [team6-anforderungen.md](team6-anforderungen.md), [Audit 15.10.2026](audits/2026-10-15-audit.md), [Audit 17.10.2026](audits/2026-10-17-audit-3.0.0.md), [Audit 18.10.2026 nach Korrektur](audits/2026-10-18-audit-3.0.0-nachkorrektur.md), [Finaler Audit 18.10.2026](audits/2026-10-18-audit-3.0.0-final.md), [CHANGELOG](../CHANGELOG.md) | erfüllt |

## Offene Punkte

**Kritisch**
1. eventuell Anpassungen des Helm-Charts notwendig (Muster liegt bei)
2. Dienst in der B-API mit `forwardApiKey` eintragen und testen ([DEPLOYMENT.md](../DEPLOYMENT.md), „Nach dem Deployment prüfen“); dabei prüfen, ob Prometheus die Metriken und Alarmregeln übernimmt

**Offen**
3. Entscheiden, ob Async-Jobs nötig sind (mit dem Textbudget bleiben auch sehr lange Seiten unter 60 s)
4. CPU und RAM unter Last messen
5. Webkomponente: Tests von Karma auf Vitest (`@angular/build:unit-test`) und den Build auf `@angular/build:application` umstellen, ohne Angular-Update.
   Damit entfallen die 11 `braces`-Befunde der Entwicklungswerkzeuge im CI-Job `audit dev dependencies` (Aufwand ca. 1–2 Tage, 442 Specs).
6. Hinweis an edu-sharing: kurz sichtbares Texteingabefeld beim Laden des Formulars (meist Millisekunden, während einer Erschließung 1–2 s). Betraf 2.1.1 genauso.
   - **Ursache:** edu-sharing setzt `layout`, `readonly` usw. erst nach `await initWebComponent()` (im Formular-Editor zusätzlich in einem `setTimeout`); bis dahin zeigt das Widget sein Standard-Layout mit Eingabefeld.
   - **Lösung:** die Einbindung in edu-sharing anpassen: alle Einstellungen setzen, sobald das Element angelegt ist, und erst danach die Skripte laden (Formular-Editor und Detailansicht). Korrigierter Code: [README der Webkomponente, „Einbettung in edu-sharing“](https://github.com/janschachtschabel/metadata-agent-webcomponent-10-2026#einbettung-in-edu-sharing).
   - **Widget unverändert:** Es kann nicht erkennen, ob der Host noch Einstellungen nachreicht; eine Wartezeit würde jede andere Einbettung verzögern.

**Bekannte Einschränkungen**
- `/generate` braucht meist 12–22 s (`gpt-6-luna`), bis 90 s mit AcademicCloud; sehr lange Quellen werden auf 150.000 Zeichen gekürzt (Rest abgeschnitten, Hinweis in `processing.warnings`) und bleiben so unter 60 s. Nach 300 s bricht der Dienst selbst ab (`504`); Proxy (B-API, edu-sharing) und Ingress brauchen dafür ≥ 310 s Timeout (Chart: 320 s) und 20 MB Body-Größe.
- Die Webkomponente zeigt den Kürzungshinweis noch nicht an; Webkomponente 3.x braucht Agent 3.x.
- Statuscodes in 3.0.0 vereinheitlicht (Breaking für Clients, CHANGELOG): Abruffehler `502`, unbekannter Node oder Kontext `404`, ungültiger Body `422`; `/upload` und `/nodes` ohne geschriebenes Ergebnis `422`/`502` statt `200`, `/workflow` `404`/`504`.
- Das GWDG-Kontingent (AcademicCloud) reicht nicht für Produktivlast; Standard ist daher `gpt-6-luna`.
- `/screenshot`, `/upload` und `/workflow` prüfen keine Rechte selbst; der Schutz liegt beim B-API-Proxy und der NetworkPolicy des Charts. Lesezugriffe laufen mit dem Service-Konto (Entscheidung offen, UEBERGABE §3).
