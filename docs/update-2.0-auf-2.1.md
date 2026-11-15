# Update von 2.0.x auf 2.1.0

> **Historisch.** Stand 2.1.1 (10/2026), unverändert aus `DEPLOYMENT.md` übernommen, als diese
> Datei am 14.11.2026 neu gegliedert wurde. Für ein Update von 3.0.x siehe
> [DEPLOYMENT.de.md](../DEPLOYMENT.de.md) (englisch: [DEPLOYMENT.md](../DEPLOYMENT.md)).

**Kurz: Ein bestehendes Deployment muss nichts ändern.** Das Image auf 2.1.0
zu tauschen genügt. Alle neuen Einstellungen haben Defaults, die sich wie 2.0.x
verhalten, und das mitgelieferte Widget kennt die neuen Antworten bereits.

## Vor dem Update prüfen

| Punkt | Betrifft | Was tun |
|---|---|---|
| **Fehlschläge kommen als HTTP-Fehler** | Programme außer dem mitgelieferten Widget, die `/generate`, `/extract-field` oder `/detect-content-type` aufrufen | Siehe die Liste unter der Tabelle. Solche Programme müssen Antworten außer `200` auswerten und dürfen bei `partial` nicht von einem vollständigen Ergebnis ausgehen. |
| **`METADATA_AGENT_LLM_PROVIDER` wird geprüft** | Deployments mit einem Wert außer `b-api-openai`, `b-api-academiccloud`, `b-api-router` oder `openai` | Bisher landete jeder andere Wert (auch ein Tippfehler) stillschweigend bei OpenAI. Jetzt startet der Dienst nicht. Groß-/Kleinschreibung und Leerzeichen sind egal. |
| **`llm_provider` im Request wird geprüft** | Programme, die `llm_provider` mitschicken | Erlaubt sind die drei `b-api-*`-Provider; `openai` nur, wenn der Dienst selbst mit `openai` läuft. Alles andere: `422`. |
| **`collection_id` muss eine Node-UUID sein** | Programme, die `/upload` oder `/nodes` mit `collection_id` aufrufen | Eine UUID oder ein Sammlungs-Link, der eine enthält. Sonst `422`, statt die Eingabe in eine Repository-URL einzusetzen. |
| **Request-Bodies höchstens 20 MB** | Sehr große `/upload`-Anfragen | Darüber `413`. Einstellbar mit `METADATA_AGENT_MAX_REQUEST_BYTES`. Ein Ingress davor hat oft eine eigene, kleinere Grenze (ingress-nginx: 1 MB, `proxy-body-size`); passend setzen, sonst antwortet schon der Ingress mit `413`. |
| **Ein eingehender `Authorization`-Header wird weitergereicht** | Deployments, vor denen ein Proxy einen `Authorization: Bearer …` mitschickt, der **kein** B-API-Schlüssel ist (etwa ein eigenes Sitzungs-Token) | Im Default `auto` geht dieser Header an die B-API statt `B_API_KEY`, und die Erschließung scheitert mit `502 LLM_AUTH_REJECTED`. Abhilfe: `METADATA_AGENT_B_API_AUTH_MODE=static`; das ist genau das Verhalten von 2.0.x. Ohne einen solchen Header ändert sich nichts. |

Was sich bei Fehlschlägen ändert:

- **Totalausfall von `/generate`** (kein einziges Feld): jetzt `502`, bei
  Drosselung `503`, mit `detail` und `error.code`. 2.0.x antwortete mit `200`,
  `success: true` und leeren Feldern.
- **Fehler der Textextraktion:** jetzt `504`, `502` bzw. `422` mit
  `error.code`. Bisher lieferten `/detect-content-type` und `/extract-field`
  dafür `400`, `/generate` `502`.
- **KI-Fehler in `/extract-field`:** jetzt `502` (Drosselung `503`) mit
  `error.code`, bisher `500`.
- **Teilergebnisse:** weiter `200`, aber mit `processing.status: "partial"`.

## Ändert sich ohne Zutun

- **Inhaltsart:** Liefert die KI keine brauchbare Antwort, kommt nicht mehr
  stillschweigend `learning_material.json`. Die Schlüsselwort-Erkennung ist als
  solche gekennzeichnet (`method: "keywords"`, Warnung).
- **Timeout je KI-Aufruf 90 s** (`METADATA_AGENT_REQUEST_TIMEOUT`): vorher 60 s
  im Code und 300 s in `docker-compose.yml`/`.env.template`. 90 s lassen einen
  Puffer unter dem Ingress-Timeout von 120 s für ein ganzes `/generate`. Wer den
  Wert selbst gesetzt hat, behält ihn.
- **Günstigere Erschließung mit OpenAI-Providern:** Vor den Feld-Aufrufen läuft
  ein kurzer Vorlauf-Aufruf, der den Seitentext in den Prompt-Cache legt; die
  Feld-Aufrufe lesen ihn dann zu einem Zehntel des Preises. Eine Erschließung
  hat damit einen KI-Aufruf mehr und kostet gemessen 59–86 % weniger
  (UEBERGABE §7). Mit AcademicCloud gibt es keinen Vorlauf.
- **Weniger KI-Aufrufe bei Fehlern:** Bei abgelehntem Schlüssel oder
  erschöpftem Kontingent bricht die Erschließung sofort ab. Scheiternde Felder
  werden nicht mehr zusätzlich neu angefragt.
- **Logs:** gehen jetzt vollständig nach stdout und im Format `text` zeilengleich
  wie bisher. Neu sichtbar sind INFO-Meldungen einiger Module, die vorher
  verloren gingen. Warnungen, die vorher nach stderr gingen, stehen jetzt in
  stdout.
- **Header:**
  - Jede Antwort trägt `X-Request-ID`.
  - Aufrufe an B-API, Textextraktion und Repository tragen `x-request-id` und
    eingehende Tracing-Header.
- **OpenAPI-Spec:** `/health` steht nicht mehr darin, ist aber unverändert
  erreichbar. Der Docker-HEALTHCHECK und bestehende Probes funktionieren
  weiter.
- **Image:** Es kommt die Abhängigkeit `prometheus-client` hinzu; `openai`,
  `tenacity`, `aiofiles` und `python-multipart` fallen weg (nie genutzt). Das
  betrifft nur den Image-Bau in der CI. Wer mit pip installiert, nutzt die
  aktualisierte `requirements.txt`.
- **Schreiben ins Repository (`/upload`, `/nodes`):**
  - Lehnt das Repository alle Metadatenfelder ab, wird der neue Node verworfen;
    Sammlungen und Workflow laufen dann nicht mehr (`success: false`,
    `step: "setMetadata"`).
  - Ohne ladbares Schema wird gar nichts geschrieben (`step: "schema"`).
  - Der Workflow hält beim ersten fehlgeschlagenen Schritt an; die übrigen
    stehen mit `skipped: true` in der Antwort.
  - Teilfehler (Sammlung, Workflow-Schritt, erweiterte Daten, Aspekte,
    übersprungene Dublettenprüfung) nennt `error`, auch wenn `success: true`.
  - `/upload` mit `node_id` beachtet das Zwei-Stunden-Fenster wie `/nodes`
    (`403`/`404`).
- **API-Doku ohne Dritt-Server:** `/docs` lädt Swagger UI vom Agenten statt
  von `cdn.jsdelivr.net`, kein Validator-Aufruf an `validator.swagger.io`.
  `/redoc` entfällt (lud zusätzlich Google Fonts); die Spec unter
  `/openapi.json` ist unverändert.
- **Entfernte Einstellungen ohne Wirkung:** `METADATA_AGENT_DEBUG`,
  `…_DEFAULT_VERSION`, `…_NORMALIZATION_ENABLED`, `…_NORMALIZATION_TEMPERATURE`,
  `…_TEXT_EXTRACTION_DEFAULT_METHOD`. Kein Code hat sie gelesen; sind sie noch
  gesetzt, ignoriert der Dienst sie.
- **Widget:** Es lädt seine Texte als `/widget/i18n/<sprache>.json?v=<Widget-Version>`.
  Nach dem Update gibt es daher keine Mischung aus neuem Code und alten,
  gecachten Texten.

## Empfohlen, aber optional

- **Readiness-Probe auf `/ready`:** liefert `503`, wenn die Konfiguration nicht
  arbeiten kann, statt den Pod Anfragen annehmen zu lassen.
- **`METADATA_AGENT_INTERNAL_PORT`** (z.B. `9000`): Probes und `/metrics` laufen
  dann über einen Port ohne Ingress, siehe [UEBERGABE.md](../UEBERGABE.md). Nur
  mit einem Worker je Container.
- **`METADATA_AGENT_LOG_FORMAT=json`**, wenn ein Log-Sammler JSON erwartet.
- **Proxy- und Ingress-Timeout für `/generate`:** mindestens 120 s; das galt
  schon für 2.0.x.
- **B-API-Proxy mit `forwardApiKey`:** Wer darauf umstellt, sorgt dafür, dass
  der Dienst nur über die B-API erreichbar ist. Optional
  `METADATA_AGENT_B_API_AUTH_MODE=forward`, siehe
  [ENV-PARAMETER.de.md](../ENV-PARAMETER.de.md#b-api-authentifizierung).
- **B-API-Routing** (`METADATA_AGENT_LLM_PROVIDER=b-api-router`): erst die
  Route anlegen, dann den Namen und `METADATA_AGENT_LLM_REQUEST_STYLE` setzen,
  siehe [ENV-PARAMETER.de.md](../ENV-PARAMETER.de.md#b-api-routing).

## Zurück auf 2.0.x

Den Image-Tag zurücksetzen. Neu gesetzte Variablen stören 2.0.x nicht; es
ignoriert unbekannte Einstellungen, auch in einer `.env`-Datei.
