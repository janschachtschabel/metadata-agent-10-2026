# Steckbrief: Metadata Agent

Stand 16.11.2026. Beschrieben ist die Version 4.0.3 (Agent und Webkomponente
gemeinsam, auf GitHub als `v4.0.3` und auf GitLab als `0.1.4` getaggt am
16.11.2026). Die Änderungen gegenüber 3.0.1 stehen im
[CHANGELOG](../CHANGELOG.md).

Status: **vorhanden** / **nicht vorhanden** (etwas, das es geben muss),
**geprüft** (eine Eigenschaft ist festgestellt), **In Klärung**,
**In Nacharbeit**.

## Stammdaten

| Punkt | Status | Angabe |
|---|---|---|
| Service | | Metadata Agent – Dienst-ID `metadata-agent` (mit der Webkomponente `metadata-agent-webcomponent`). Zwei ältere Namen bleiben, weil andere Stellen sie fest verwenden: der Proxy-Eintrag der B-API `metadata-agent-canvas` und das Image `metadata-agent-api` |
| Funktionalität | | Erzeugt aus Text, Webseite oder Repository-Eintrag Metadaten nach den WLO/OEH-Schemata (Titel, Beschreibung, Schlagworte, Bildungsstufe, Lizenz …), prüft sie und schreibt sie ins WLO-Repository; liefert dazu die Webkomponente zum Anzeigen und Bearbeiten |
| Stabile Version | **vorhanden** | 4.0.3 (16.11.2026: Ausweichkette zu Textextraktion und Geocoder; davor 4.0.2 vom selben Tag mit den Korrekturen aus dem ersten Test auf Staging, 4.0.1 und 4.0.0 vom 15.11.2026), davor 3.0.1 (20.10.2026). Aufrufer-sichtbare Änderungen stehen im CHANGELOG unter „Vor dem Update prüfen“ |
| Technischer Owner | | @Jan Schachtschabel (Team 4: Manuel und Jan); Vertretung noch festzulegen |
| Jira | **vorhanden** | DAT-108, wegen der Anpassungen SDWLO-1564 |
| Repository | **vorhanden** | GitLab (Produktiv): [metadata-agent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent), [metadata-agent-webcomponent](https://scm.edu-sharing.com/edu-sharing/projects/wlo/metaservices/metadata-agent-webcomponent). GitHub (Entwicklung): [metadata-agent-10-2026](https://github.com/janschachtschabel/metadata-agent-10-2026), [metadata-agent-webcomponent-10-2026](https://github.com/janschachtschabel/metadata-agent-webcomponent-10-2026) |
| OpenAPI Spec | **vorhanden** | `GET /openapi.json` aus dem Code, 16 Operationen, jede mit Beispielantwort; Swagger UI unter `/docs` ohne Dritt-Server. Referenz: [docs/API.de.md](API.de.md) ([englisch](API.md)) |
| Pipeline und Git-Tags | **vorhanden** | `.gitlab-ci.yml` im Repo: Tests, Lint, Typen, `helm lint`, kubeconform, promtool, Image-Bau mit Trivy-Scan und Smoke-Test. Tags `vX.Y.Z` müssen zur Version in `pyproject.toml` passen und erzeugen das Versions-Image. Geplante Prüfungen (pip-audit, Alter des Chromium, Scan des neuesten Release-Images) als Pipeline-Schedule – in GitLab in der Oberfläche anzulegen. GitHub Actions spiegeln die Prüfungen |
| Endpunkte | **vorhanden** | Öffentlich (Port 8000): `POST /generate`, `/extract-field`, `/detect-content-type`, `/validate`, `/export/markdown`, `/node`, `/nodes`, `/upload`, `/workflow/{node_id}`, `/upload/verify/{node_id}`, `/screenshot`; `GET /info/schemata`, `/info/schemas/…`, `/info/schema/…`, `/widget/info`, `/widget/i18n/{lang}.json`; dazu `/docs`, `/openapi.json`, `/widget/dist/*`, `/widget/examples/*`. Infrastruktur: `/health`, `/ready`, `/metrics`. `/debug/*` nur mit `METADATA_AGENT_DEBUG_ENDPOINTS=true` (Standard und Chart: aus) |
| Endpunkte auf zwei Ports | **vorhanden** | Port 8000: API und Webkomponente. Interner Port (Chart: 9000): `/health`, `/ready`, `/metrics` – nicht im Ingress, im Chart auf einem eigenen Service `<name>-internal`. `/health` und `/ready` antworten auch auf Port 8000 (HEALTHCHECK des Images) |
| Header / Content-Types | **geprüft** | Ein: `Authorization: Bearer` (von der B-API per `forwardDelegation` bzw. `forwardApiKey` durchgereicht), `x-client-trace-id`, `X-Request-ID`, B3 (`x-b3-traceid`, `x-b3-spanid`, `b3`). Aus: `X-Request-ID`, bei Überlast `Retry-After`. API: `application/json` (`/generate` nimmt auch `text/plain`); Webkomponente: JS, CSS, `woff2`, HTML, JSON – im Proxy freizugeben |
| Asynchrone Job-Endpunkte | **geprüft** | keine, alles synchron. `/generate` dauert meist 5–22 s (`gpt-6-luna`), Frist 300 s; kurz davor liefert er ein Teilergebnis (`200`, `status: partial`), `504` nur, wenn bis dahin nichts fertig ist; `/upload` und `/nodes` Frist 100 s. Proxys davor brauchen ≥ 310 s Timeout und 20 MB Body-Größe |
| Readiness Status | **In Nacharbeit** | Version 4.0.3 ist lokal geprüft (4687 Tests, Webkomponente 1284 Specs, [Audit vom 14.11.](audits/2026-11-14-audit-3.0.1.md) mit behobenen Befunden). Es fehlen: Test auf Staging, Eintrag und Schlüsselrechte in der B-API, erste Installation des Charts im Cluster – siehe „Offene Punkte“ |

## Architektur

![Architektur-Map, Betrieb über die B-API](architektur/metadata-agent-architektur-b-api.png)

| Punkt | Status | Angabe |
|---|---|---|
| Überblick | | FastAPI-Anwendung (Python 3.13, ein Worker je Container), zustandslos, mit Chromium (Headless-Shell) für Screenshots. Ablauf: Webkomponente in edu-sharing → B-API-Proxy → Agent → B-API (LLM, Textextraktion, Photon-Geocoder) und edu-sharing-Repository. Grafiken über die B-API: [Map](architektur/metadata-agent-architektur-b-api.png), [Ablauf](architektur/metadata-agent-ablauf-b-api.png); direkter Betrieb: [Map](architektur/metadata-agent-architektur.png), [Ablauf](architektur/metadata-agent-ablauf.png); [interaktiv](architektur/README.md) |
| Horizontale Skalierbarkeit | **geprüft** | ja, über Replicas: keine Sitzungen, kein gemeinsamer Zustand, keine Volumes. Zwei Grenzen: Das GWDG-Limit (AcademicCloud, ~10 Aufrufe/min) gilt für alle Pods gemeinsam – der Chart teilt es mit `sharedLlmLimit` durch die Replicas; die Sperre gegen doppeltes Speichern derselben URL gilt je Prozess (mehrere Pods deckt das zusätzliche Lesen des Eingangsordners ab). Kein Autoscaling |
| Integriert in B-API | **geprüft** | ja: Aufrufe kommen über den Proxy-Eintrag `metadata-agent-canvas` (Port 8000, `forwardDelegation` bzw. `forwardApiKey`, Timeout ≥ 310 s). Der Dienst erkennt den Betrieb selbst (`METADATA_AGENT_B_API_AUTH_MODE=auto`) und läuft auch eigenständig. Einrichtung: [b-api-kurzinfo.md](b-api-kurzinfo.md) |
| Schreibt Suggestions | **geprüft** | nein. Der Dienst schreibt Nodes und Metadaten direkt ins Repository (`/upload`, `/nodes`, `/node`) und startet den Redaktions-Workflow (`200_tocheck`); die Suggestions-API nutzt er nicht |
| Nutzt Routing | **geprüft** | ja, für die LLM-Aufrufe: `b-api-openai` (Standard, Modell `gpt-6-luna`), `b-api-academiccloud` oder `b-api-router` mit Routenname oder `provider/modell`. Empfohlen: OpenAI mit `gpt-6-luna`; AcademicCloud reicht mit den aktuellen Limits nicht für Produktivlast. Modellwahl je Anfrage begrenzbar (`METADATA_AGENT_ALLOWED_LLM_MODELS`) |
| Nutzt API Keys | **geprüft** | ja: je Anfrage der durchgereichte Schlüssel des Aufrufers, sonst der eigene `B_API_KEY` (`auto` / `forward` / `static`). Der Schlüssel braucht die LLM-Endpunkte (`openai`, `router`) und die Proxy-Dienste `text-extraction` und `photon_geocoding`, kein `LLM_ROUTE_MANAGE` |
| Repo über B-API | **geprüft** | nein: Das edu-sharing-Repository ruft der Dienst direkt per REST mit einem Dienstkonto auf (Lesen von Nodes, Schreiben, Vorschau, Workflow). Die B-API deckt davon nur einen Teil ab; der Umbau auf beliebige Repositories ist ein eigenes Arbeitspaket |
| Benötigte Dienste | **geprüft** | B-API (LLM); Textextraktion (`/api/v1/proxy/text-extraction`, für URL-Eingaben) und Photon-Geocoder (`/api/v1/proxy/photon_geocoding`, für Orte) – beide über die B-API der Umgebung (`METADATA_AGENT_B_API_SERVICES`); scheitert der Weg zum Geocoder, fragt der Agent mit `auto` `photon.komoot.io` direkt, ohne Schlüssel; edu-sharing-Repository. Ohne Repository läuft das Erschließen aus Text und URL (`METADATA_AGENT_REQUIRE_REPOSITORY=false`). Keine CronJobs, keine eigenen Daten |

## Deployment und Infrastruktur

| Punkt | Status | Angabe |
|---|---|---|
| HELM | **vorhanden** | [helm/metadata-agent/](../helm/metadata-agent/README.md) im Repo: Deployment (Probes auf dem internen Port), zwei Services, PodDisruptionBudget, NetworkPolicy, Secret, ServiceMonitor, PrometheusRule; Chart-Version = Version des Dienstes; jeder Wert mit `## @param` beschrieben; `helm lint` und kubeconform in der CI. **In Klärung:** noch nie in einem Cluster installiert, Chart-Pfad (`deploy/helm/<dienst-id>`) und Veröffentlichung in einer Registry. Prüfliste: [DEPLOYMENT.de.md](../DEPLOYMENT.de.md) |
| Docker-Files | **vorhanden** | `Dockerfile` (Multi-Stage, `python:3.13-slim` per Digest, Nutzer `appuser`, `tini` als Init-Prozess, HEALTHCHECK) und `docker-compose.yml`; Chart und Compose starten den Container mit nur lesbarem Root-Dateisystem |
| Abschaltbare Komponenten | **geprüft** | Im Chart: Ingress (Standard aus), NetworkPolicy, ServiceMonitor, PrometheusRule, PodDisruptionBudget. Im Dienst: Repository-Zugang (`METADATA_AGENT_REQUIRE_REPOSITORY=false`, nur Generieren), Screenshots und Geokodierung je Anfrage, Debug-Endpunkte (Standard aus) |
| Datenbank | **geprüft** | nein – auch kein Redis, keine Worker, keine Queue |
| Große Daten | **geprüft** | nein. Im Image nur Schemata (8 MB), Webkomponente (2,1 MB) und Swagger UI (1,8 MB); keine Modelle oder Korpora, kein Volume. Das Image ist durch Chromium groß (Headless-Shell; komprimierte Größe noch nicht gemessen). Anfragen bis 20 MB, Quelltexte werden auf 150.000 Zeichen gekürzt |

## Security und Konfiguration

| Punkt | Status | Angabe |
|---|---|---|
| Authentifizierung | **geprüft** | keine eigene: Der Dienst prüft keine Zugangsdaten des Aufrufers. Den Zugang sichert der B-API-Proxy (Variante A: Schlüssel per `forwardDelegation` bzw. `forwardApiKey`) und im Cluster die NetworkPolicy; ohne B-API muss ein anderer Proxy absichern. `/screenshot`, `/upload` und `/workflow` handeln mit dem Dienstkonto (akzeptiert, UEBERGABE §3) |
| Network Policy | **vorhanden** | Im Chart, Standard an: Port 8000 nur aus den Namespaces der B-API, der interne Port nur aus denen von Prometheus; ausgehend nur öffentliche Ziele. Namespaces sind anzupassen; Grenzen (IPv6, LoadBalancer) in [DEPLOYMENT.de.md](../DEPLOYMENT.de.md) |
| ENV Variablen | **vorhanden** | [ENV-PARAMETER.de.md](../ENV-PARAMETER.de.md) ([englisch](../ENV-PARAMETER.md)), `.env.template`, `values.yaml` – per Test mit dem Code abgeglichen. Ohne Einstellung gelten Defaults für Staging; ein ungültiger Wert beendet den Start |
| Benötigte Secrets | **geprüft** | `B_API_KEY` (entfällt im Modus `forward`), `WLO_GUEST_USERNAME` und `WLO_GUEST_PASSWORD` (entfallen, wenn nur generiert wird), `OPENAI_API_KEY` nur ohne B-API. Keine Werte im Repo, im Image oder in den Startzeilen |

## Monitoring und Betrieb

| Punkt | Status | Angabe |
|---|---|---|
| Prometheus | **vorhanden** | `/metrics` auf dem internen Port; ServiceMonitor im Chart. Metriken: HTTP (Dauer, Status), LLM-Aufrufe (Ergebnis, Dauer, Tokens samt gelesenem und geschriebenem Prompt-Cache), Erschließungen nach Ausgang, Aufrufe an abhängige Dienste |
| Alarmregeln | **vorhanden** | 13 Regeln als `PrometheusRule` im Chart, per promtool getestet (abgelehnter Schlüssel, falsche Route, Drosselung, nicht erreichbare Dienste, langsame oder scheiternde Erschließungen, interne Fehler); kritisch nur für eigenen Schlüssel und konfiguriertes Modell. Empfänger legt Team 6 fest (UEBERGABE §4) |
| B3-Tracing | **vorhanden** | Der Dienst übernimmt `x-b3-traceid`, `x-b3-spanid`, `b3` und `x-client-trace-id`, legt ohne sie eine Trace-ID an und gibt die Header an B-API und Repository weiter, nie an Fremddienste. Logs auf stdout als JSON (`service`, `trace_id`, `span_id`, `request_id`; im Chart gesetzt) oder Text, eine Zeile je Anfrage. Kein OpenTelemetry-Export |

## Ressourcen und Kosten

| Punkt | Status | Angabe |
|---|---|---|
| CPU, RAM und Storage | **vorhanden** | Requests 500m / 512Mi, Limits 2 CPU / 2Gi je Pod, kein Storage – geschätzt. **In Nacharbeit:** Messung unter Last, P50/P90 je Endpunkt |
| Kostenschätzung | **vorhanden** | LLM: 0,007–0,017 € je Erschließung mit `gpt-6-luna` (gemessen 16.10.2026, UEBERGABE §7); Kontrolle am 15.11.2026: rund 0,009 $ je Lauf nach Listenpreis, 48 % der Eingabe-Tokens aus dem Prompt-Cache; seit dem Cache-Haltepunkt vom selben Tag rund 15 % weniger (0,0070 $ statt 0,0083 $ für einen Text mit 4.400 Zeichen). Photon-Geocoder kostenlos. **In Klärung:** Datenresidenz-Aufschlag der B-API |

## Offene Punkte

**Kritisch**

1. Dienst in der B-API mit `forwardDelegation` bzw. `forwardApiKey` eintragen und testen ([DEPLOYMENT.de.md](../DEPLOYMENT.de.md), „Nach dem Deployment prüfen“); dabei prüfen, ob Prometheus Metriken und Alarmregeln übernimmt.
2. Schlüsselrechte in der B-API ergänzen: Die Nutzer- und Gast-Schlüssel aus edu-sharing (und `B_API_KEY`) brauchen die Proxy-Dienste `text-extraction` und `photon_geocoding`; danach die Tests 3 und 4 aus DEPLOYMENT.de.md eingeloggt und als Gast ausführen. Mit dem B-API-Team klären, ob der Eintrag `photon_geocoding` Schlüssel und Trace-Header an Photon weiterreicht. Befund vom 16.11. (Staging, 4.0.1): Mit der durchgereichten Anmeldung einer `/generate`-Anfrage antwortet die B-API auf `photon_geocoding` mit `401`, derselbe Schlüssel direkt an die B-API geschickt bekommt `200`. Wahrscheinliche Ursache: Die aktuelle B-API reicht statt des Schlüssels ein Delegations-Token weiter (`forwardDelegation`, geprüft an `/v3/api-docs`), dessen `delegationScopes` `photon_geocoding` nicht enthalten. Mit dem B-API-Team klären: Scopes für `text-extraction` und `photon_geocoding`, `delegationLifetimeSeconds` ≥ 320 s, `responseTimeout` des Eintrags ≥ 310 000 ms (stand am 16.11. auf 300 000), Freigaben für Kopfzeilen und Inhaltstypen (DEPLOYMENT.de.md, „Hinter dem B-API-Proxy“). Bis dahin weicht der Agent aus: eigener `B_API_KEY`, dann der Dienst direkt; abgelehnte Wege zeigt `GET /debug/dependencies` unter `refused_ways`.
3. Chart zum ersten Mal im Cluster installieren; eventuell sind Anpassungen nötig (Muster liegt bei). Das Chart braucht ein Image aus demselben Stand – nicht das veröffentlichte 3.0.1. Es nennt `image.tag: v4.0.3`; dieses Image gibt es erst mit dem GitLab-Tag `v4.0.3` (Punkt 5), bis dahin `image.tag=main` setzen.
4. Einbettung in edu-sharing mit der neuen Webkomponente testen: Der Datensatz enthält Felder ohne Wert nicht mehr, `metadataChange` kommt sofort nach einer Änderung, `contentType` im Datensatz wird gelesen (CHANGELOG der Webkomponente, „Für Einbettende“).

**Offen**

5. Release-Image bauen: Der Stand liegt im GitLab-Repository auf `main` (Tag `0.1.4`, inhaltsgleich mit dem Tag `v4.0.3` des Entwicklungs-Repositorys auf GitHub). Die Pipeline baut daraus das Image `main`; ein Image `v4.0.3` entsteht erst mit einem Tag `v4.0.3` in GitLab. Der Test auf Staging steht noch aus (Punkte 1 bis 4).
6. Entscheiden, ob Async-Jobs nötig sind – nur falls ein Proxy die ≥ 310 s nicht erlaubt.
7. CPU und RAM unter Last messen; P50/P90 je Endpunkt; komprimierte Image-Größe.
8. Hinweis an edu-sharing: kurz sichtbarer Eingabebereich beim Laden des Formulars (auf Staging am 16.11. bestätigt). Ursache ist die späte Konfiguration des Elements; kleinste Korrektur sind Attribute im Template: Formular-Editor `[attr.layout]="viewer ? 'detail' : 'clean'"`, `show-input-area="false"`, `show-core-fields="false"`, `show-floating-controls="false"`, `highlight-ai="true"`; Detailansicht `layout="detail"`, `readonly`, `show-floating-controls="false"`. Im Browser mit einer nachgebauten edu-sharing-Seite geprüft; Code im README der Webkomponente, [„Einbettung in edu-sharing“](https://github.com/janschachtschabel/metadata-agent-webcomponent-10-2026/blob/main/README.de.md#einbettung-in-edu-sharing).
9. Webkomponente: Umstellung von Karma auf Vitest und auf `@angular/build:application` als eigenes Release (Karma und der Webpack-Builder sind abgekündigt). `npm audit` meldet derzeit keine Befunde.
10. Vertretung der Ansprechperson festlegen; Zeile in der gemeinsamen Kompatibilitätsmatrix nachtragen (braucht Textextraktion und Repository; nur gemeinsam mit Webkomponente gleicher Hauptversion).

## Abweichungen vom Entwicklerleitfaden, mit Begründung

- **Synchron bis 300 s statt Jobs ab 30 s:** Eine Erschließung dauert typisch 5–22 s (Text mit 4.400 Zeichen rund 5 s, gemessen 15.11.2026; Webseiten 12–22 s, gemessen 16.10.2026 vor der Beschleunigung). Jobs wären nur nötig, wenn ein Proxy die ≥ 310 s nicht erlaubt.
- **Kein Weiterarbeiten ohne LLM:** Ohne LLM entstehen keine Metadaten. Lehnt die B-API den Schlüssel ab oder drosselt sie, meldet der Dienst den Grund (`502`/`503` mit Fehlercode) statt eines leeren Ergebnisses.
- **Modellname statt Route als Standard:** `gpt-6-luna` über `b-api-openai` läuft, ohne dass eine Route angelegt sein muss. Auf eine Route stellt der Betrieb per Env um (`METADATA_AGENT_LLM_PROVIDER=b-api-router`, `METADATA_AGENT_B_API_ROUTER_MODEL=<route>`), ohne neues Image.
- **Rückfall-Schlüssel als `X-API-KEY`:** `B_API_KEY` geht wie in 2.0.x als `X-API-KEY` an die B-API. Der durchgereichte Schlüssel geht unverändert als `Authorization` weiter.
- **`/health` und `/ready` auch auf Port 8000:** für den HEALTHCHECK des Images und ältere Probes. `/metrics` gibt es nur auf dem internen Port.
- **Repository direkt statt über die B-API, nur WLO/OEH-Schemata:** Der Umbau auf beliebige edu-sharing-Repositories ist ein eigenes Arbeitspaket.
- **Chart unter `helm/metadata-agent`**, nicht `deploy/helm/<dienst-id>`; die CI veröffentlicht das Chart nicht in einer Registry; kein Autoscaling, weil ein HPA das geteilte GWDG-Limit (`sharedLlmLimit`) umgeht.
- **Kein OpenTelemetry:** Der Dienst liest die Trace-Header, gibt sie weiter und legt eine Trace-ID an, meldet aber keine Spans.
- **Betrieb ohne B-API ohne eigene Auth-Schicht:** Den Zugang muss dann ein anderer Proxy absichern.
- **JSON-Logs nur, wenn eingestellt:** Ohne Einstellung schreibt der Dienst Text; das Chart setzt `METADATA_AGENT_LOG_FORMAT=json`, die Compose-Datei nicht.
- **`/debug/auth` und `/debug/dependencies` auf Port 8000:** zum Einrichten des Proxys gedacht und nur mit `METADATA_AGENT_DEBUG_ENDPOINTS=true` vorhanden.
- **Kein eigener Statuscode für fehlende Einrichtung:** `503` meldet auch dauerhafte Konfigurationsfehler (kein Repository-Zugang, keine Textextraktion).

## Bekannte Einschränkungen

- `/generate` braucht meist 5–22 s (`gpt-6-luna`), bis 90 s mit AcademicCloud. Sehr lange Quellen werden auf 150.000 Zeichen gekürzt (Hinweis in `processing.warnings`, den die Webkomponente anzeigt). Kurz vor der Frist von 300 s beendet der Dienst die laufenden Feld-Aufrufe und liefert ein Teilergebnis.
- Agent und Webkomponente gehören zusammen: Die Webkomponente wird vom Agenten ausgeliefert und braucht einen Agenten derselben Hauptversion.
- Änderungen für Aufrufer gegenüber 3.0.1 (CHANGELOG, „Vor dem Update prüfen“): `/validate` und `/export/markdown` antworten bei unbekanntem Schema, Kontext oder unbekannter Version mit `404`; ungültiges JSON ist bei jedem Endpunkt `400`; `/widget/info` liefert relative Verweise; `/nodes` und `/upload` lehnen die Optionen des jeweils anderen ab; ein angehängter Schrägstrich wird nicht weitergeleitet; Einstellungen mit ungültigem Wert beenden den Start; der interne Port liegt im Chart auf einem eigenen Service; Textextraktion und Geocoder laufen über die B-API (Schlüsselrechte, Punkt 2); in den JSON-Logs heißt `time` jetzt `timestamp`, `trace_id` ist die B3-Trace-ID. Prüfliste: [DEPLOYMENT.de.md](../DEPLOYMENT.de.md), „Update von 3.0.x auf 4.0.0“.
- Das GWDG-Kontingent (AcademicCloud) reicht nicht für Produktivlast; Standard ist daher `gpt-6-luna`.
- `/screenshot`, `/upload` und `/workflow` prüfen keine Rechte selbst; der Schutz liegt beim B-API-Proxy und der NetworkPolicy des Charts.

## Betriebsdokumentation und Nachweise

[UEBERGABE.md](../UEBERGABE.md) · [DEPLOYMENT.de.md](../DEPLOYMENT.de.md) · [ENV-PARAMETER.de.md](../ENV-PARAMETER.de.md) · [INSTALL.de.md](../INSTALL.de.md) · [API-Referenz](API.de.md) · [B-API-Kurzinfo](b-api-kurzinfo.md) · [Architektur](architektur/README.md) · [Helm-Chart](../helm/metadata-agent/README.md) · [CHANGELOG](../CHANGELOG.md)

Audits: [15.10.2026](audits/2026-10-15-audit.md) · [17.10.2026](audits/2026-10-17-audit-3.0.0.md) · [18.10.2026 nach Korrektur](audits/2026-10-18-audit-3.0.0-nachkorrektur.md) · [18.10.2026 final](audits/2026-10-18-audit-3.0.0-final.md) · [13.11.2026](audits/2026-11-13-audit-3.0.1.md) · [14.11.2026](audits/2026-11-14-audit-3.0.1.md) mit [Korrekturplan](plans/2026-11-14-audit-und-korrekturen.md). Historisch: [team6-anforderungen.md](team6-anforderungen.md)
