# Kurzinfo: B-API-Routing und B-API-Auth im Metadata Agent 3.0

Für Entwickler. Details: [ENV-PARAMETER.de.md](../ENV-PARAMETER.de.md) – Abschnitte
„B-API: Routing“ und „B-API: Authentifizierung“.

## 1. Routing

Mit `b-api-router` gehen alle KI-Aufrufe an `/api/v1/llm/router/chat/completions`.
Die B-API wählt ein Modell der Route und weicht bei Fehlern auf das nächste aus.
**Routen werden in der B-API gepflegt, nicht im Agenten.** Ein Modelltausch in
der Route braucht deshalb kein Deployment.

**Ein Modell ohne Route** (Default `openai/gpt-6-luna`, funktioniert sofort):

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=openai/gpt-6-luna
```

**Eigene Route mit Reserve** (vorher in der B-API anlegen: Oberfläche
„LLM-Routing“ oder `POST /api/v1/llm/router/routes` mit Recht `LLM_ROUTE_MANAGE`):

```env
METADATA_AGENT_LLM_PROVIDER=b-api-router
METADATA_AGENT_B_API_ROUTER_MODEL=metadata-agent        # Name der Route
METADATA_AGENT_LLM_REQUEST_STYLE=classic                # Qwen/DeepSeek; reasoning für GPT-5/6
METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none       # Pflicht für denkende Modelle (qwen3.6)
METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS=2            # AcademicCloud-Modelle: GWDG-Grenzen
METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=0.16   # ~10 je Minute, das GWDG-Limit des Schlüssels
```

Regeln:
- Eine Route enthält Modelle **einer** Parameterfamilie. GPT-5/6 und o-Modelle
  bilden die Familie `reasoning`, alle anderen `classic`. Kein OpenAI-Modell in
  AcademicCloud-Routen.
- Erst die Route anlegen, dann den Namen setzen. Ein unbekannter Name ergibt sofort
  `LLM_ROUTE_NOT_FOUND`, eine falsche Familie `LLM_ROUTE_MISCONFIGURED`.
- Die B-API cacht wortgleiche Anfragen je Route. Nach einem Modelltausch dort
  „Cache leeren“ wählen.
- Das GWDG-Kontingent je Schlüssel liegt bei ~10 Aufrufen/Minute und ~200/Stunde.
  Ein `/generate` macht ~50 Aufrufe.

## 2. Auth: wessen Schlüssel zahlt

Die B-API ruft den Agenten über ihren Proxy auf (`/api/v1/proxy/<dienst>/…`).
Ist dort `forwardApiKey` gesetzt, reicht sie den Schlüssel des Aufrufers als
`Authorization: Bearer <key>` durch. edu-sharing wählt dabei einen Schlüssel
für Nutzer oder Gäste, und die B-API bucht Kosten und Limits auf ihn.

`METADATA_AGENT_B_API_AUTH_MODE`:

| Modus | Kommt `Authorization: Bearer …` an | Kein Header |
|---|---|---|
| `auto` (Default) | wird an die B-API weitergereicht | eigener `B_API_KEY` |
| `forward` | wird weitergereicht | `502 LLM_AUTH_MISSING` |
| `static` | wird ignoriert | eigener `B_API_KEY` |

- **Meist ist nichts zu setzen:** `auto` nutzt den durchgereichten Schlüssel,
  sonst `B_API_KEY`, etwa für Direktaufrufe und Tests.
- **`forward`** nutzen, wenn nur über den Proxy abgerechnet werden soll; dann
  ohne `B_API_KEY`.
- **`static`** nutzen, wenn davor ein Proxy einen `Authorization`-Header setzt,
  der kein B-API-Schlüssel ist.
- **Gilt für alle B-API-Provider** (`b-api-openai`, `b-api-academiccloud`,
  `b-api-router`). Bei `openai` zählt `OPENAI_API_KEY`.
- **Umgang mit dem Schlüssel:** Er gilt nur für die eine Anfrage. Er wird nicht
  gespeichert und nicht geloggt. `processing.auth_source` in der Antwort sagt,
  woher er kam (`forwarded` oder `static`).
- **Sicherheit:** Der Agent prüft den Schlüssel nicht selbst. Er darf **nur über
  die B-API erreichbar** sein; die NetworkPolicy im Helm-Chart sorgt dafür.

### Derselbe Schlüssel für Textextraktion und Geocoder

Mit einem B-API-Provider ruft der Agent auch die Textextraktion und den
Geocoder über die B-API auf (`/api/v1/proxy/text-extraction`,
`/api/v1/proxy/photon_geocoding`). So gehören sie immer zur B-API der Umgebung:
Staging zu Staging, Produktion zu Produktion.

- **Rechte:** Jeder Schlüssel, mit dem der Dienst aufgerufen wird (Nutzer- und
  Gast-Schlüssel aus edu-sharing, `B_API_KEY`), braucht neben den
  LLM-Endpunkten die Proxy-Dienste `text-extraction` und `photon_geocoding`.
- **Fehlt das Recht:** Das Erschließen aus einer URL endet mit `502`
  `TEXT_EXTRACTION_AUTH_REFUSED`; Orte bleiben ohne Koordinaten, und
  `processing.warnings` sagt warum.
- **`METADATA_AGENT_B_API_SERVICES`:** `auto` (Default) wie beschrieben, aber
  nur, wenn die LLM-Aufrufe an `METADATA_AGENT_B_API_BASE_URL` gehen; `direct`
  ruft beide Dienste direkt und ohne Schlüssel auf; `proxy` erzwingt die B-API.

Lokal ausprobieren (simuliert den Proxy):

```bash
curl -s http://127.0.0.1:8000/generate \
  -H "Authorization: Bearer $MEIN_B_API_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Workshop Robotik am 1. Mai 2026 in Weimar", "schema_file": "event.json"}'
```

## 3. Pro Anfrage überschreiben

`llm_provider` und `llm_model` im Body von `/generate`, `/detect-content-type`
und `/extract-field` überschreiben die Env, zum Beispiel
`{"llm_provider": "b-api-router", "llm_model": "metadata-agent"}`. Ohne
durchgereichten Schlüssel läuft das auf Kosten des Servers.
`METADATA_AGENT_ALLOWED_LLM_MODELS=modell-a,modell-b` begrenzt die erlaubten
Modelle; ein anderes Modell ergibt `422`. Das Default-Modell ist immer erlaubt.

## 4. Prüfen

Mit `METADATA_AGENT_DEBUG_ENDPOINTS=true` (danach wieder ausschalten):
- `GET /debug/auth` zeigt, ob ein Schlüssel ankommt (nur Schema und Länge) und
  welcher genutzt würde.
- `GET /debug/dependencies` prüft:
  - ob die B-API den Schlüssel annimmt (ohne Tokenverbrauch);
  - beim Router, ob die B-API die Route kennt (`route_listed`);
  - ob Textextraktion und Repository erreichbar sind.

Beim Start warnt der Agent vor unpassenden Kombinationen, etwa `static` ohne
`B_API_KEY` oder gemischte Staging- und Prod-Adressen.
