# Abdeckung der Dienstanforderungen von Team 6

Stand: Metadata Agent 3.0.0, 17.10.2026. Abgeglichen mit der Seite
„Dienstanforderungen für den Betrieb durch Team 6“ (Abschnitte 1–7 und
Checkliste Übergabepaket). Einzelheiten stehen in [UEBERGABE.md](../UEBERGABE.md).

✅ erfüllt · 🟡 teilweise · ❌ offen · — entfällt

| Anforderung | Stand | Umsetzung / was fehlt |
|---|:-:|---|
| **1. API und Endpunkte** | | |
| OpenAPI-Spec, Pfad dokumentiert | ✅ | `/openapi.json`, aus dem Code erzeugt (FastAPI) |
| Anzeigename und Beschreibung für Endnutzende | ✅ | `Metadata Agent API`, Text in `info.summary` |
| Spec beschreibt die Nutzung vollständig (wünschenswert) | ✅ | alle 16 Operationen mit Beschreibung, 9 mit Beispiel-Bodies; Swagger UI unter `/docs` ohne Dritt-Server |
| Infrastruktur-Endpunkte nicht in der Spec | ✅ | `/health`, `/ready`, `/metrics`, `/debug/*` fehlen in der Spec |
| Zwei Ports (öffentlich / intern) | ✅ | `METADATA_AGENT_INTERNAL_PORT` für `/health`, `/ready`, `/metrics`; `/metrics` nur dort. `/health` und `/ready` zusätzlich auf Port 8000 für bestehende Probes; `/debug/*` standardmäßig aus |
| Header benannt und begründet | ✅ | ein: `Authorization` (B-API-Proxy), Tracing-Header; aus: `X-Request-ID` |
| Content-Types | 🟡 | API nur `application/json`. Das Widget liefert JS, CSS, `woff2`, HTML und JSON aus – **mit Wolli abstimmen** |
| Erwartete Antwortzeiten | ✅ | `/generate` mit `gpt-6-luna` 12–22 s, mit AcademicCloud bis 90 s, sehr lange Quellen länger (bis 300 s, dann `504`) → Proxy-/Ingress-Timeout mindestens ~310 s (seit 19.10., vorher 120 s); übrige Endpunkte wenige Sekunden |
| Async-Jobs bei mehr als 30 s (Empfehlung) | ❌ | nicht umgesetzt; nächster Schritt, falls ~310 s im Proxy nicht gehen |
| **2. B-API und Authentifizierung** | | |
| Variante A: Auth über die B-API | ✅ | keine eigene Auth-Schicht nötig; Dienst nur über die B-API erreichbar machen |
| Virtueller Key für LLM-Aufrufe | ✅ | weitergereichter `Authorization`-Header wird je Anfrage genutzt (`B_API_AUTH_MODE` `auto` / `forward`), nicht gespeichert oder geloggt |
| Modell-Routing, Berechtigungen des Keys | ✅ | Provider `b-api-router`; Key braucht nur die LLM-Endpunkte, **kein** `LLM_ROUTE_MANAGE`. Default `openai/gpt-6-luna` braucht keine Route |
| Beide Varianten ohne Code-Änderung, abwärtskompatibel | ✅ | Default `auto`: weitergereichter Key, sonst `B_API_KEY` aus der Env als `X-API-KEY` – ohne Header genau das Verhalten von 2.0.x; `static` nutzt nur `B_API_KEY` wie 2.0.x (ENV-PARAMETER „B-API: Authentifizierung“, DEPLOYMENT „Update von 2.0.x auf 2.1.0“) |
| Variante B: LLM-Zugang per Env (Secrets) | ✅ | `B_API_KEY` für die LLM-Endpunkte der B-API (Modus `static`) oder `OPENAI_API_KEY` mit `METADATA_AGENT_OPENAI_API_BASE` für einen OpenAI-kompatiblen Endpunkt |
| Variante B: eigene Auth-Schicht für Aufrufer | ❌ | Der Agent prüft nicht, **wer ihn aufruft** (eingehend). Das ist unabhängig vom Env-Key oben, mit dem er selbst die LLMs aufruft (ausgehend). Ohne B-API muss ein anderer Proxy den Zugang absichern |
| **3. Monitoring und Logging** | | |
| Prometheus-Basis-Metriken, nur intern | ✅ | `prometheus-client`, auf dem internen Port |
| Applikationsmetriken | ✅ | HTTP nach Route, LLM-Aufrufe nach Provider/Ergebnis und Dauer, Erschließungen nach `success`/`partial`/`failed`, Aufrufe an abhängige Dienste (gesendet/beantwortet/Dauer), Tokens |
| Alarmregeln im Helm-Chart | ✅ | zehn Regeln als `PrometheusRule` in [helm/metadata-agent/](../helm/metadata-agent/README.md), mit promtool-Tests (UEBERGABE §4); Empfänger legt T6 im Alertmanager fest |
| Tracing B3, `x-client-trace-id` durchreichen | ✅ | `x-client-trace-id`, `x-request-id`, `x-b3-*`, `b3` gehen unverändert an B-API, Textextraktion und Repository (eigene Spans erzeugt der Dienst nicht) |
| Logs nach stdout, JSON/Text, Level per Env | ✅ | `METADATA_AGENT_LOG_FORMAT`, `METADATA_AGENT_LOG_LEVEL`; JSON mit `trace_id` |
| **4. Repository, Build, Versionierung** | | |
| Repo-Zugang für T6 | 🟡 | URLs in UEBERGABE §1 (scm.edu-sharing.com); Zugriff für T6 noch bestätigen |
| CI baut Images automatisch, Prod nur getaggt | ✅ | GitLab CI: Branch-Images für `main`/`develop`, Versions-Images bei Git-Tags (`v3.0.0`) |
| Schlankes Image | 🟡 | `python:3.13-slim`, Multi-Stage, `.dockerignore`, Nutzer `appuser`, nur Laufzeit-Abhängigkeiten. Chromium für Screenshots (~400 MB) macht das Image trotzdem groß |
| **5. Deployment und Betrieb** | | |
| Zustand und Skalierung beschrieben | ✅ | zustandslos, horizontal skalierbar; Grenzen: ein Worker je Container, gemeinsames GWDG-Limit (~10 Aufrufe/min) |
| Kurze Architekturübersicht | ✅ | [docs/architektur/](architektur/README.md) (PNG, SVG, HTML), UEBERGABE §5 |
| Datenbank (kein SQLite in Produktion) | — | keine Datenbank, kein Redis, keine Volumes |
| Ressourcen-Baseline | 🟡 | Requests 500m / 512Mi, Limits 2 CPU / 2Gi – **geschätzt, nicht unter Last gemessen** |
| Große Daten nicht im Image | ✅ | nur Schemata (8 MB), Widget (2,5 MB), Swagger UI (1,8 MB); keine Modelle oder Korpora |
| Jede Einstellung als Env mit Default | ✅ | [ENV-PARAMETER.md](../ENV-PARAMETER.md); ein Test gleicht die Liste mit dem Code ab |
| Beispiel-Values-YAML, Secrets getrennt | ✅ | Helm-Chart `helm/metadata-agent/values.yaml` (per Test mit dem Code abgeglichen); Secrets: `B_API_KEY`, `OPENAI_API_KEY` (nur Variante B), `WLO_GUEST_USERNAME`, `WLO_GUEST_PASSWORD` |
| Dockerfile und docker-compose als Referenz | ✅ | beide im Repo |
| Abschaltbare Komponenten im Helm-Chart | — | keine DB, kein Redis, keine Worker; nur der interne Port ist optional |
| **6./7. Übergabe und Wartung** | | |
| Kosteninformationen | ✅ | gemessen 16.10.2026 mit `gpt-6-luna`: 0,007 € (kurze Seite) bis 0,017 € (lange Seite) je Erschließung, mit Input-Caching; Einzelheiten in UEBERGABE §7. Abrechnungsweg der B-API (Datenresidenz-Aufschlag) noch klären |
| Ansprechperson T4 | ❌ | in UEBERGABE §8 offen |
| Wer Updates anstößt | ❌ | in UEBERGABE §8 offen; Modellwechsel gehen ohne Code-Änderung über Env oder Route |

**Zusammen:** 25 erfüllt, 4 teilweise, 4 offen, 2 entfallen (35 Punkte).

**Offen vor der Übergabe:**
- **Ausfüllen:** Ansprechperson und Zuständigkeit für Updates; Repo-Zugriff für T6 bestätigen.
- **Messen:** RAM und CPU unter Last, z.B. mit `docker stats` während eines
  `/generate` mit Screenshot.
- **Abstimmen:** die statischen Widget-Dateien mit Wolli (Content-Types).
- **Entscheiden:** ob Async-Jobs für `/generate` nötig sind, falls im Proxy keine
  ~310 s möglich sind.
