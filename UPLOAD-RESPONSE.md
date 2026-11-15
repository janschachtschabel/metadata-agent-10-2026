# `POST /upload` — Antwortformat

Stand nach Einführung von `node_full`. **Bestehende Felder sind unverändert** — wer
heute gegen `/upload` programmiert, muss nichts anpassen.

## Request-Schalter

```jsonc
{
  "metadata": { "...": "..." },   // Output von /generate
  "return_full_node": true,       // Default: false
  "collection_id": ["3039bdb2-…"], // NEU, optional — immer eine Liste von
                                   // IDs oder Sammlungs-URLs
  "workflow_steps": [             // NEU, Default: ["200_tocheck"]
    "200_tocheck",
    "140_ELEMENT_LEGALLY_APPROVED"
  ]
}
```

Optionen, die es nur bei `POST /nodes` gibt (`preview`, `start_review_workflow`,
`start_quality_workflow`), lehnt `/upload` mit `422` ab – und umgekehrt `/nodes`
die Optionen von `/upload` (z. B. `check_duplicates`, `workflow_steps`,
`return_full_node`). Dasselbe gilt für einen unbekannten Schlüssel neben einem
verschachtelten `metadata`; erlaubt bleiben dort die Optionen, der Umschlag einer
`/generate`-Antwort, `repository` und Schlüssel mit führendem `_`.

## Antwort

| Feld | Typ | Immer da? | Bedeutung |
|---|---|---|---|
| `success` | bool | ja | Upload erfolgreich |
| `duplicate` | bool \| null | – | `true` wenn `ccm:wwwurl` bereits existiert |
| `node` | object \| null | bei Erfolg + bei Duplikat | Kurzinfo (unverändert) |
| `node_full` | object \| null | **nur bei `return_full_node: true`** | Kompletter edu-sharing Node |
| `error` | string \| null | – | Fehlermeldung |
| `step` | string \| null | – | Schritt, an dem es scheiterte (z. B. `setMetadata`) |
| `fields_written` | int \| null | bei Erfolg | Geschriebene Felder |
| `schema_used` | string \| null | bei Erfolg | **NEU** — Typschema, gegen das geschrieben wurde. `null` = nur `core.json` |
| `repo_fields_available` | int \| null | bei Erfolg | **NEU** — wie viele Felder dieses Schema überhaupt schreiben darf (core 26, +`event.json` 33, +`learning_material.json` 40) |
| `fields_skipped` | int \| null | bei Erfolg | Anzahl der gesendeten Felder mit Wert, die auf keine Repository-Property geschrieben wurden (meist nicht repo-fähig) |
| `skipped_fields` | array \| null | bei Erfolg | **NEU** — die Namen dieser Felder; `[]`, wenn keines ausgelassen wurde |
| `field_errors` | array \| null | – | `[{field_id, error, status_code}]` |
| `preview` | object \| null | – | Status des Vorschaubild-Uploads. `method` ist immer `playwright` (lokal, auch bei `screenshot_method: "pageshot"`). Eine nicht öffentliche Vorschau-URL ergibt `success: false` mit `error` — der Upload selbst bleibt erfolgreich |
| `collections` | array \| null | **nur wenn Sammlungen angegeben waren** | `[{collectionId, success, error}]`; steht auch in der `504`-Antwort, wenn das Zeitlimit während der Verknüpfung endete (siehe unten) |
| `workflow` | array \| null | **nur bei `start_workflow: true`** | `[{status, success, error, skipped}]` in angeforderter Reihenfolge; `skipped: true` bei einem Schritt, der nicht gesendet wurde, weil ein früherer scheiterte |
| `node_created` | bool \| null | bei Erfolg | **NEU** — `true`, wenn dieser Aufruf den Node angelegt hat; `false`, wenn er über `node_id` übergeben wurde |
| `discarded_node` | string \| null | nur nach Abbruch | ID des unvollständigen Nodes, der zurückgenommen wurde. **Nie gesetzt, wenn `node_id` übergeben wurde** — ein fremder Node wird nicht verworfen |

### `node` — die bisherige Kurzinfo

```jsonc
{
  "nodeId": "5ab4b434-4832-45ca-b4b4-34483265ca5d",
  "title": "Workshop KI in der Bildung",
  "description": "Ein Workshop über…",       // auf 200 Zeichen gekürzt
  "wwwurl": "https://example.org/workshop",
  "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/5ab4b434-…"
}
```

Die Werte stammen aus den **gesendeten** Metadaten, nicht aus dem Repository.
`repositoryUrl` ist ein Konstrukt des Agenten und existiert in edu-sharing nicht.

### `node_full` — der Node aus dem Repository

Nur vorhanden, wenn `return_full_node: true` gesetzt war. Enthält den Node **so, wie
ihn edu-sharing liefert** — identische Struktur zum `node`-Objekt aus
`GET /node/v1/nodes/-home-/{id}/metadata` und aus der Antwort von `createChild`.

```jsonc
{
  "ref":        { "repo": "…", "id": "5ab4b434-…" },
  "parent":     { "repo": "…", "id": "21144164-…" },   // Inbox-Ordner
  "type":       "ccm:io",
  "aspects":    [ "…" ],
  "properties": {
    "cclom:title":               [ "Workshop KI in der Bildung" ],
    "cclom:general_description": [ "Ein Workshop über…" ],
    "ccm:wwwurl":                [ "https://example.org/workshop" ]
  }
}
```

Der Ausschnitt zeigt nur die Felder, auf die es hier ankommt — maßgeblich ist die
Node-Definition der edu-sharing REST-API, nicht diese Datei.

**Feldzuordnung zwischen beiden Objekten:**

| `node` | `node_full` |
|---|---|
| `nodeId` | `ref.id` |
| `title` | `properties["cclom:title"][0]` |
| `wwwurl` | `properties["ccm:wwwurl"][0]` |
| `description` (gekürzt) | `properties["cclom:general_description"][0]` (ungekürzt) |
| `repositoryUrl` | *kein Gegenstück* |

Beachten: In `node_full` sind Property-Werte **immer Arrays**, auch bei einem einzigen
Wert.

## Wann `node_full` leer bleibt

- `return_full_node` war nicht gesetzt
- Das Zurücklesen ist fehlgeschlagen — der Upload gilt trotzdem als erfolgreich
  (`success: true`), weil der Node bereits geschrieben wurde. Ein Lesefehler danach
  darf einen erfolgreichen Schreibvorgang nicht als Fehlschlag melden.

## Beispiele

**Erfolg mit vollständigem Node**

```jsonc
{
  "success": true,
  "duplicate": null,
  "node":      { "nodeId": "5ab4b434-…", "title": "Workshop KI in der Bildung", "…": "…" },
  "node_full": { "ref": { "id": "5ab4b434-…" }, "properties": { "…": "…" } },
  "fields_written": 23,
  "fields_skipped": 4,
  "skipped_fields": ["schema:eventStatus", "schema:duration", "schema:doorTime", "oeh:format"],
  "preview": { "success": true, "method": "playwright" }
}
```

**Duplikat** — `node`/`node_full` beschreiben den **bereits vorhandenen** Node.
Geprüft wird über die Suche und zusätzlich über die 25 neuesten Nodes des
Eingangsordners (der Suchindex kennt einen eben angelegten Node noch nicht);
Prüfung und Anlegen laufen je URL nacheinander (innerhalb eines Prozesses), sodass
ein Doppelklick keinen zweiten Node erzeugt. Hat der gefundene Node keinen Titel, lautet der Text
`URL existiert bereits: Node <id>`:

```jsonc
{
  "success": false,
  "duplicate": true,
  "node":      { "nodeId": "abc-…", "title": "Schon vorhanden", "…": "…" },
  "node_full": { "ref": { "id": "abc-…" }, "…": "…" },
  "error": "URL existiert bereits: \"Schon vorhanden\""
}
```

### `collections` und `workflow`

```jsonc
{
  "collections": [
    { "collectionId": "3039bdb2-…", "success": true,  "error": null },
    { "collectionId": "unbekannt",  "success": false, "error": "HTTP 404: …" }
  ],
  "workflow": [
    { "status": "200_tocheck",                 "success": true, "error": null },
    { "status": "140_ELEMENT_LEGALLY_APPROVED", "success": true, "error": null }
  ]
}
```

Beide Schritte laufen **nach** dem Schreiben der Metadaten. Ein Fehlschlag dort
setzt `success` des Uploads nicht zurück — der Node existiert dann bereits mit
allen Metadaten, nur die Sammlungs-Referenz bzw. der Workflow-Schritt fehlt. Das
steht im jeweiligen Eintrag.

Jeder Workflow-Schritt ist ein eigener Aufruf und damit ein eigener Eintrag in
der Workflow-Historie des Nodes — inklusive ausführendem Nutzer. Scheitert ein
Schritt, werden die folgenden nicht mehr gesendet (ein späterer Status setzt
die früheren voraus, z.B. keine Veröffentlichung nach gescheiterter Freigabe);
sie stehen mit `"success": false, "skipped": true` in der Liste. Für bereits
hochgeladene Nodes gibt es denselben Ablauf als `POST /workflow/{node_id}`.

## Abbruch nach dem Anlegen

Der Upload legt den Node zuerst an und füllt ihn danach. Bricht die Verbindung
dazwischen ab — Timeout, Netzfehler —, bliebe sonst ein Node im Eingangsordner
zurück, der nur seinen Titel trägt: für den Aufrufer unsichtbar, der `success:
false` sieht und es erneut versucht, wobei jedes Mal einer mehr liegen bleibt.

Deshalb nimmt die API den angelegten Node in diesem Fall zurück (Papierkorb,
`recycle=true`, also wiederherstellbar) und meldet ihn als `discarded_node`.
Weil danach nichts angelegt bleibt, ist das seit dem finalen Audit (18.10.)
eine Fehlerantwort (`502`, `UPSTREAM_ERROR`) statt `200` mit `success: false`;
`detail` ist der bisherige `error`-Text:

```jsonc
// HTTP 502
{
  "detail": "Timeout bei der Verbindung zum Repository (ReadTimeout) (unvollständiger Node wurde verworfen)",
  "error": { "code": "UPSTREAM_ERROR", "message": "…", "request_id": "3f9c2a7d1b4e5f6091c4a7d2e8b05f13" },
  "discarded_node": "244bf208-de5e-4543-8bf2-08de5ed543fe"
}
```

Dasselbe gilt für jedes Schreiben ohne Ergebnis (nichts angelegt, nichts
geändert): `422` `VALIDATION_ERROR` (Objektform eines Felds verletzt), `422`
`SCHEMA_UNAVAILABLE` (Kontext, Version oder `metadataset` unbekannt: das Schema
ließ sich nicht laden, `step: "schema"`, vor der ersten Anfrage ans Repository –
ein nicht ladbares `metadataset` fiel früher still auf `core.json` zurück) oder
`502` `UPSTREAM_ERROR`
(Repository lehnte ab oder war nicht erreichbar), jeweils mit `discarded_node`,
`node`, `step`, `field_errors`, wenn gesetzt. `200` mit `success: false` gibt es
nur noch für Dubletten und Teilergebnisse (der Node existiert).

Scheitert auch das Verwerfen, bleibt der Node bestehen und wird als `node.nodeId`
mit einem entsprechenden Hinweis zurückgegeben — dann ist ein manueller
Eingriff nötig. Wird ein bestehender Node (`node_id`) unterbrochen, nachdem die
Anfrage das Repository erreicht haben kann, nennt die Antwort den Node mit dem
Hinweis „kann teilweise geschrieben sein“.

Ein Fehler **vor** dem Anlegen verwirft nichts; ein erfolgreicher Upload ebenso
wenig. Verworfen wird auch nie ein Node, der schon im Workflow ist (die Redaktion
kann benachrichtigt sein), einer, dessen Schreibschritte alle gelaufen sind, oder
einer, für den die erste Sammlungsanfrage hinausgegangen ist (eine Sammlung kann
dann schon auf ihn verweisen) – er wird als `node` gemeldet, mit dem Hinweis
„nicht verworfen“ in `error`. Endet das Zeitlimit während der Verknüpfung, nennt
die `504`-Antwort zusätzlich `collections` – je Sammlung verknüpft,
„Verknüpfung nicht bestätigt“ (Anfrage gesendet, Antwort offen) oder „Verknüpfung
nicht versucht“ – und `error` fasst dasselbe zusammen; ein angeforderter Workflow
wurde dann nicht gestartet.

Geht die Antwort auf das Anlegen verloren, sucht der Dienst den Node unter den
neuesten des Eingangsordners. Als der eigene gilt nur einer, der seit dem Anlegen
unverändert ist und nichts trägt, was erst die Schritte nach dem Anlegen
schreiben; sonst wird nichts verworfen, und `error` endet mit „möglicherweise
wurde trotzdem ein Node angelegt; bitte den Eingangsordner prüfen“. Mit
`node_id` (bestehender Node) wird ebenfalls nichts verworfen; vor dem Schreiben
prüft der Dienst dann das Bearbeitungsfenster: `403` außerhalb, `404` unbekannt,
`502`, wenn der Node wegen eines Repository-Fehlers nicht lesbar ist.

**Zeitlimit.** `/upload` und `/nodes` brechen nach `METADATA_AGENT_UPLOAD_TIMEOUT`
(Default 100 s) mit `504` und `error.code: UPLOAD_TIMEOUT` ab; für den Node gelten
dieselben Regeln (`discarded_node` oder `node`, nach der ersten Sammlungsanfrage
`node` mit `collections`). Das Vorschaubild darf das Zeitlimit nicht ausschöpfen:
Es lässt den Anfragen nach ihm eine Reserve – 15 s für die erste (eine je
Sammlung, eine je Workflow-Schritt), 5 s für jede weitere, höchstens 45 s; folgt
keine, 1 s. Ist das Bild bis dahin nicht da, geht der Upload ohne Bild weiter
(Grund in `preview.error`), statt am Zeitlimit den fertigen Node zu verlieren.
Das Zurücklesen eines fertigen Nodes (`node_full`) läuft mit eigener Grenze
(10 s) außerhalb des Zeitlimits.

## Kosten

`return_full_node: true` erzeugt einen zusätzlichen Repository-Aufruf nach allen
Schreibvorgängen. Der Node wird also im **finalen** Zustand gelesen — inklusive
Metadaten, Aspects, Collections und Extended Data, nicht im leeren Zustand direkt
nach dem Anlegen.

Deshalb Default `false`: `/upload` führt bereits mehrere sequentielle
edu-sharing-Aufrufe aus (je bis 45 s, zusammen begrenzt durch
`METADATA_AGENT_UPLOAD_TIMEOUT`), und auf Serverless
zählt jeder weitere.
