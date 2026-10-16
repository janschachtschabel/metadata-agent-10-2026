# Objektfelder in `ccm:oeh_extendedData`

Für alle, die am Metadaten-Agenten oder am Widget arbeiten. Wer ein Schema
anfasst, einen Normalisierer ändert oder einen neuen Schreibweg baut, sollte
dieses Dokument gelesen haben — die hier beschriebene Regel ist nicht Kosmetik,
ihre Verletzung hat im August 2026 den Suchindex von Staging lahmgelegt.

---

## Die Regel

> **Ein Feld, das das Schema als Objekt deklariert, muss ein Objekt enthalten —
> immer, auf jedem Weg, auch wenn nur ein Name bekannt ist.**

```jsonc
// falsch
"schema:provider": "Stuttgarter Musikschule"
"schema:contactPoint": ["Virtuelles Haus"]

// richtig
"schema:provider": { "name": "Stuttgarter Musikschule" }
"schema:contactPoint": [ { "contactType": "Virtuelles Haus" } ]
```

## Warum das so hart ist

`ccm:oeh_extendedData` wird als ein JSON-Blob gespeichert und von Elasticsearch
mit **dynamischem Mapping** indiziert. Der erste Datensatz, der einen Pfad
füllt, legt dessen Typ für den **gesamten Index** fest.

Steht dort ein String, ist der Pfad ab da `text`. Jeder spätere Datensatz mit
der korrekten Objektstruktur wird mit `mapper_parsing_exception` **abgewiesen
und landet gar nicht erst im Index**. Der Datensatz existiert im Repository,
aber die Suche kennt ihn nicht — und der collectionSyncTracker, der später
seine Sammlungszugehörigkeit schreiben will, scheitert mit
`document_missing_exception` und bleibt stehen. Aus einem falsch getippten
Namen wird so ein Ausfall der Sammlungszuordnung für alle Inhalte.

Der reale Ablauf:

| Datum | Node | Was passierte |
|---|---|---|
| 17.03.2026 | `18ea4081-6c54-4603-aa40-816c54f60385` | Handeingabe `schema:contactPoint: ["Virtuelles Haus"]` — legt den Pfad auf `text` fest |
| 07.08.2026 | `b5b3ade7-b1b6-4b54-b3ad-e7b1b64b54ec` | schreibt die **korrekte** Objektstruktur — wird abgewiesen, kommt nie in den Index |
| ab 07.08.2026 | — | collectionSyncTracker hängt; Sammlung „Optik" meldet im Repository 273 Referenzen, in der Sammlungsansicht 0 |

Die Ironie, die man sich merken sollte: **abgewiesen wird der Datensatz, der es
richtig macht.**

---

## Die vier Tore

Jeder Weg, auf dem ein Wert in `ccm:oeh_extendedData` gelangen kann, hat sein
eigenes Tor. Wer einen neuen Weg baut, braucht auch ein Tor.

| # | Weg | Tor | Verhalten |
|---|---|---|---|
| 1 | KI-Extraktion über `/generate` | `FieldNormalizer._normalize_object_field` | **korrigiert** |
| 2 | Eingabe im Widget | Maske zeigt das Objektformular; `FieldValidationService.validateObject` | **korrigiert, mit Hinweis** |
| 3 | `/validate` | `MetadataService.validate_metadata` | **meldet als Fehler** |
| 4 | `/upload`, `/nodes`, `/save-node` | `find_object_shape_violations` in `upload_metadata` / `save_node` | **lehnt ab** |

### Warum mal korrigiert und mal abgelehnt wird

Der Unterschied ist **nicht** technisch, sondern eine Frage der Zustimmung:

- **Tor 1 und 2 korrigieren**, weil das Ergebnis danach noch auf dem Bildschirm
  erscheint. Die Redaktion sieht den korrigierten Wert und kann ihn ändern oder
  löschen, bevor irgendetwas gespeichert wird.
- **Tor 4 lehnt ab**, weil dort schon gespeichert wird. Eine Korrektur nach dem
  Absenden würde etwas ins Repository schreiben, das nie jemand gesehen hat.
  Für ein Kurationswerkzeug ist das schlimmer als ein fehlgeschlagener Schreibvorgang.

**Das ist die Regel für neuen Code:** solange der Wert danach noch angezeigt und
verworfen werden kann, darf korrigiert werden. Danach nur noch ablehnen.

### Tor 4 im Detail

Die Prüfung sitzt **vor dem ersten Repository-Aufruf**. Beide Schreibabläufe
legen den Node zuerst an und befüllen ihn danach — eine späte Ablehnung würde
einen Node zurücklassen, der nur seinen Titel trägt. In `_write_extended_fields`
steht zusätzlich ein Riegel, der wirft statt zu schreiben; er sollte
unerreichbar sein.

`find_object_shape_violations` **verändert die Metadaten nicht.** Das hält der
Test `test_the_write_path_reports_the_field_and_changes_nothing` fest, indem er
das Dict vorher und nachher zeichenweise vergleicht.

---

## Was die Schemata dafür liefern müssen

Alle vier Tore leiten ihr Verhalten aus dem Schema ab. Das geht nur, wenn das
Schema eindeutig ist. Drei Regeln, festgehalten in
`tests/test_schema_declarations.py`:

**1. Jede Liste sagt, was sie enthält.**

```jsonc
"system": { "datatype": "array", "items": { "datatype": "string" } }   // ok
"system": { "datatype": "string", "multiple": true }                   // ok, Typ ist ableitbar
"system": { "datatype": "array" }                                      // NICHT ok
```

**2. Objektlisten haben genau einen Stil.**

```jsonc
// so
"system": {
  "datatype": "array",
  "multiple": true,
  "items": { "datatype": "object", "discriminator": "@type", "variants": [ … ] }
}

// nicht mehr so — die Varianten liegen dann im system statt in items, und
// weder llm_service._build_object_structure noch der ShapeExpanderService des
// Widgets finden sie dort. Personenfelder hatten deshalb im Editor überhaupt
// kein Objektformular.
"system": { "datatype": "object", "multiple": true, "variants": [ … ] }
```

**3. Kein Feldpfad ist in einem Schema ein Objekt und in einem anderen ein Skalar.**

Alle Inhaltsarten und alle Schemaversionen schreiben in **denselben**
Indexpfad. Ein Feld, das in `source.json` ein Objekt und in `curriculum.json`
ein String ist, ist derselbe Konflikt wie oben — nur noch nicht ausgelöst.

---

## Wenn du etwas änderst

### Ein neues Objektfeld ins Schema aufnehmen

1. Im Stil aus Regel 2 deklarieren, mit `items.variants`.
2. `pytest tests/test_schema_declarations.py` — muss grün sein.
3. `pytest tests/test_object_fields_normalized.py` — läuft datengetrieben über
   **alle** Objektfelder und nimmt das neue automatisch mit. Es gibt keine
   Namensliste, die du pflegen musst; genau so eine Liste war die Ursache.

### Das Zielunterfeld ändern

Ein nackter String landet im Unterfeld `name`, wenn eine Variante eines
deklariert, sonst im ersten deklarierten Unterfeld (bei `ContactPoint` also
`contactType`). Diese Regel steht **zweimal** im Code und muss übereinstimmen:

- Backend: `FieldNormalizer._object_key` in `src/services/field_normalizer.py`
- Widget: `objectKeyFor` in `src/app/core/object-shape.ts`

Weichen sie voneinander ab, bekommt derselbe Wert je nach Weg zwei Formen.
Gemessen am 18.08.2026 stimmen beide für alle 2209 Felddeklarationen überein.
**Diese Übereinstimmung ist nicht durch einen Test abgesichert** — jede Seite
pinnt nur ihre eigenen zwei Konventionen. Wer hier etwas ändert, ändert es an
beiden Stellen und prüft die andere von Hand.

### Eine neue Schemaversion anlegen

Das interne Feld `version` muss dem Ordnernamen entsprechen. Das klingt banal,
war aber in 26 von 28 Dateien des Ordners `v2.0.0` falsch (`1.8.1`) — und
Datensätze speichern diesen Wert als `schemaVersion`, sodass jede spätere
Validierung gegen das falsche Schema läuft.

### Einen neuen Schreibweg bauen

Ruf `find_object_shape_violations` auf, **bevor** du das Repository anfasst, und
brich ab, wenn etwas zurückkommt. Nicht reparieren — siehe oben.

---

## Was du nicht tun solltest

**Keine Namensliste.** Die Ursache des Vorfalls war eine Liste von sechs
Feldnamen in `output_normalizer.py`, die Strings in Objekte hob. Die Schemata
deklarieren 37 Objektfelder; abgedeckt waren vier, alle aus dem
Veranstaltungskontext. `person.json` (14 Objektfelder) und `organization.json`
(11) hatten Abdeckung **null** — und genau dort ist der Schaden entstanden.
Leite das Verhalten aus dem Schema ab, nicht aus einer Liste.

**Nicht aus dem Wert schließen.** Das Widget entschied früher anhand des Wertes,
ob ein Feld ein Objektfeld ist. Bei einem String lautete die Antwort „nein",
also erschien ein einzeiliges Textfeld — in das die Redaktion prompt einen Namen
tippte. Die Frage beantwortet das Schema, nie der Wert.

**Skalare Listenelemente nicht nachträglich normalisieren.** Der Objektzweig
greift bewusst nur bei `items.datatype: object`. `_normalize_date` und
`_normalize_number` geben `None` zurück, wenn sie einen Wert nicht deuten
können — pro Element angewandt würde das falsche, aber rettbare Werte still
löschen.

---

## Absicherung

| Prüfung | Ort | Umfang |
|---|---|---|
| Schemadeklarationen | `tests/test_schema_declarations.py` | 254 Prüfungen über 84 Dateien |
| Normalisierung | `tests/test_object_fields_normalized.py` | jedes Objektfeld, datengetrieben |
| Namensliste bleibt weg | `tests/test_output_normalizer_list_is_redundant.py` | Äquivalenz + Quelltextprüfung |
| Validierung und Schreibsperre | `tests/test_object_shape_is_enforced.py` | die drei realen Fälle, Unveränderlichkeit, Reihenfolge |
| Widget: Objektform | `src/app/core/shape-expander.service.spec.ts` | leer, skalar, korrekt |
| Widget: Maske | `src/app/core/canvas.service.spec.ts` | Entscheidung aus dem Schema |
| Widget: Konvention | `src/app/core/object-shape.spec.ts` | Zielunterfeld, Reparatur |

CI führt beides aus: `run tests` im Backend (`uv sync` + `pytest`) und
`widget build and tests` im Widget (`ng test` + `ng build`). Bis zum 18.08.2026
gab der Backend-Job nur einen Merkzettel aus und wurde grün — 1176 Tests liefen
nirgends.

---

## Was außerhalb dieser Repositories liegt

- **Das Mapping ist noch vergiftet.** Der Pfad bleibt `text`, bis der Index neu
  aufgebaut wird. Sinnvoll wäre dabei ein Index-Template, das den
  `extendedData`-Teilbaum dem dynamischen Mapping entzieht (`"enabled": false`
  oder `flattened`).
- **Der collectionSyncTracker bricht ab**, statt ein einzelnes nicht
  indizierbares Dokument zu überspringen. Solange das so ist, genügt ein
  schemawidriger Datensatz, um die Sammlungszuordnung stillzulegen.
- **19 Datensätze in Staging tragen noch falsche Werte** (17 aus einem
  Importlauf vom 11.–13.03.2026, zwei Handeingaben). Sie müssen vor oder mit dem
  Reindex bereinigt werden; `b5b3ade7-…` muss danach einmal angefasst werden,
  damit der Tracker ihn aufnimmt.
