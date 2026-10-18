"""
MetadataService as it behaved before it was taken apart (audit M-01).

validate_metadata, export_to_markdown and generate_metadata each had a
cyclomatic complexity of 20-52. These tests pin what they returned for inputs
that reach every branch, so splitting them into named steps and modules can be
checked against the exact previous output (tests/fixtures/metadata_characterization/).

The expected files were written from the code before the split. A deliberate
behaviour change updates them on purpose; a refactoring never does.
"""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import metadata_service as metadata_module
from src.services.field_normalizer import get_field_normalizer
from src.services.output_normalizer import get_output_normalizer

FIXTURES = Path(__file__).parent / "fixtures" / "metadata_characterization"

CONCEPTS = [
    {"uri": "http://w3id.org/vocab/de", "label": {"de": "Deutsch", "en": "German"}},
    {"uri": "http://w3id.org/vocab/en", "label": {"de": "Englisch", "en": "English"}},
    {"value": "CC_BY", "label": {"de": "CC BY"}},
]

FIELDS = [
    {
        "id": "cclom:title",
        "group": "basics",
        "label": {"de": "Titel"},
        "system": {"datatype": "string", "required": True},
    },
    {
        "id": "cclom:general_description",
        "group": "basics",
        "label": {"de": "Beschreibung"},
        "system": {"datatype": "string", "required": True},
    },
    {
        "id": "schema:contactPoint",
        "group": "contact",
        "label": {"de": "Kontakt"},
        "system": {
            "datatype": "array",
            "multiple": True,
            "items": {
                "datatype": "object",
                "variants": [
                    {
                        "@type": "ContactPoint",
                        "fields": [{"id": "contactType"}, {"id": "email"}],
                    }
                ],
            },
        },
    },
    {
        "id": "schema:maximumAttendeeCapacity",
        "group": "event",
        "label": {"de": "Plätze"},
        "system": {"datatype": "integer"},
    },
    {
        "id": "schema:isAccessibleForFree",
        "group": "event",
        "label": {"de": "Kostenlos"},
        "system": {"datatype": "boolean"},
    },
    {
        "id": "schema:startDate",
        "group": "event",
        "label": {"de": "Beginn"},
        "system": {"datatype": "date", "multiple": True},
    },
    {
        "id": "schema:doorTime",
        "group": "event",
        "label": {"de": "Einlass"},
        "system": {"datatype": "datetime", "multiple": True},
    },
    {
        "id": "schema:startTime",
        "group": "event",
        "label": {"de": "Uhrzeit"},
        "system": {"datatype": "time", "multiple": True},
    },
    {
        "id": "ccm:wwwurl",
        "group": "basics",
        "label": {"de": "URL"},
        "system": {"datatype": "uri", "multiple": True},
    },
    {
        "id": "geo:latitude",
        "group": "place",
        "label": {"de": "Breite"},
        "system": {"datatype": "number", "multiple": True},
    },
    {
        "id": "geo:longitude",
        "group": "place",
        "label": {"de": "Länge"},
        "system": {"datatype": "number", "multiple": True},
    },
    {
        "id": "cclom:general_language",
        "group": "basics",
        "label": {"de": "Sprache"},
        "system": {
            "datatype": "array",
            "multiple": True,
            "vocabulary": {"type": "closed", "concepts": CONCEPTS},
        },
    },
    {
        "id": "schema:location",
        "group": "place",
        "label": {"de": "Ort"},
        "system": {"datatype": "array", "multiple": True},
    },
]
CORE_FIELDS = [
    {
        "id": "cclom:title",
        "group": "basics",
        "label": {"de": "Titel"},
        "system": {"datatype": "string", "required": True},
    },
    {
        "id": "ccm:oeh_publisher",
        "group": "basics",
        "label": {"de": "Verlag"},
        "system": {"datatype": "string", "required": True},
    },
]
SCHEMA = {
    "label": {"de": "Veranstaltung", "en": "Event"},
    "fields": FIELDS,
    "groups": [
        {"id": "basics", "label": {"de": "Grunddaten"}},
        {"id": "event", "label": {"de": "Termin"}},
        {"id": "place", "label": {"de": "Ort"}},
        {"id": "contact", "label": {"de": "Kontakt"}},
    ],
}
CORE = {
    "label": {"de": "Kern"},
    "fields": CORE_FIELDS,
    "groups": [
        {"id": "basics", "label": {"de": "Grunddaten"}},
        {"id": "meta", "label": {"de": "Meta"}},
    ],
}

VALIDATION_INPUTS = {
    "every_branch": {
        "metadataset": "event.json",
        "cclom:general_description": "",
        "schema:contactPoint": ["Besucherservice", {"email": "a@b.de"}],
        "schema:maximumAttendeeCapacity": ["dreihundert", 120],
        "schema:isAccessibleForFree": "ja",
        "schema:startDate": [
            "15.03.2025",
            "3. Mai 2026",
            "irgendwann",
            "2026-13-40",
            "2026-05-03",
        ],
        "schema:doorTime": ["15.03.2025 14:30", "morgen", "2026-05-03T10:00"],
        "schema:startTime": ["14 Uhr 30", "9:05", "mittags", "10:00:00"],
        "ccm:wwwurl": ["example.org/kurs", "kein link", "https://ok.example"],
        "geo:latitude": [91.5, 50.1],
        "geo:longitude": [-181, 11.3],
        "cclom:general_language": [
            "http://w3id.org/vocab/de",
            "deutsch",
            "Klingonisch",
            "CC_BY",
        ],
    },
    "legacy_schema_key": {
        "_schema": {"file": "core.json"},
        "cclom:title": "T",
        "ccm:oeh_publisher": "P",
    },
    "unknown_schema": {"metadataset": "fehlt.json"},
}

EXPORT_INPUTS = {
    "values_only": (
        {
            "metadataset": "event.json",
            "cclom:title": "Konzert",
            "schema:isAccessibleForFree": True,
            "cclom:general_language": ["http://w3id.org/vocab/de", "fr"],
            "schema:location": [
                {"name": "Kulturhaus"},
                {"streetAddress": "Hauptstr. 1", "addressLocality": "Weimar"},
            ],
            "schema:contactPoint": [{}],
            "ccm:wwwurl": "",
            "geo:latitude": [50.1],
        },
        {"language": "de"},
    ),
    "include_empty_en": (
        {
            "metadataset": "event.json",
            "cclom:title": "Concert",
            "schema:isAccessibleForFree": False,
        },
        {"language": "en", "include_empty": True},
    ),
    "unknown_schema": ({"metadataset": "fehlt.json"}, {"context": "nicht-da"}),
}


def _schemas(monkeypatch):
    def load_schema(context, version, schema_file):
        if context == "nicht-da" or schema_file == "fehlt.json":
            raise FileNotFoundError(f"Schema {schema_file} not found")
        return json.loads(json.dumps(CORE if schema_file == "core.json" else SCHEMA))

    def ai_fields(context, version, schema_file):
        if schema_file == "fehlt.json":
            raise FileNotFoundError(f"Schema {schema_file} not found")
        source = CORE if schema_file == "core.json" else SCHEMA
        return json.loads(json.dumps(source["fields"]))

    # Wherever the schema lookups are imported - before and after the split.
    for module_name in (
        "src.services.metadata_service",
        "src.services.metadata_validation",
        "src.services.metadata_export",
        "src.services.metadata_generation",
    ):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        monkeypatch.setattr(module, "load_schema", load_schema, raising=False)
        monkeypatch.setattr(module, "get_ai_fillable_fields", ai_fields, raising=False)
        monkeypatch.setattr(module, "get_schema_fields", ai_fields, raising=False)


class _LLM:
    """Stands in for LLMService: records the fields asked for, answers fixed values."""

    provider, model, requires_custom_header = "b-api-openai", "test-model", False

    def __init__(self, values, errors=(), skipped=0):
        self.values, self.errors, self.skipped = values, list(errors), skipped
        self.asked = []

    async def extract_fields_parallel(
        self, fields, text, existing_metadata, language, max_workers
    ):
        self.asked.append([f["id"] for f in fields])
        return {
            "values": {f["id"]: self.values.get(f["id"]) for f in fields},
            "errors": self.errors,
            "skipped": self.skipped,
        }


def _service(llm):
    service = metadata_module.MetadataService.__new__(metadata_module.MetadataService)
    service.llm_service = llm
    service.field_normalizer = get_field_normalizer()
    service.output_normalizer = get_output_normalizer()
    service.geocoding_service = SimpleNamespace()
    service.settings = SimpleNamespace()
    return service


EXISTING = {
    "cclom:title": "Mein Titel",
    "cclom:general_description": "Alt",
    "schema:startDate": ["2026-05-03"],
    "_origins": {"x": "y"},
}
GENERATION_INPUTS = {
    "fresh": dict(
        text="Ein Konzert.",
        llm={
            "cclom:title": "Konzert",
            "schema:startDate": ["15.03.2025"],
            "schema:isAccessibleForFree": "ja",
            "ccm:wwwurl": ["example.org"],
        },
    ),
    "re_extraction_with_origins": dict(
        text="Korrektur.",
        existing=EXISTING,
        origins={"cclom:title": "user", "cclom:general_description": "ai"},
        llm={
            "cclom:title": "KI-Titel",
            "cclom:general_description": "Neu",
            "schema:startDate": [],
        },
    ),
    "regenerate_listed_and_empty": dict(
        text="Noch einmal.",
        existing={**EXISTING, "ccm:wwwurl": [], "geo:latitude": [50.1]},
        regenerate_fields=["cclom:general_description"],
        regenerate_empty=True,
        llm={
            "cclom:general_description": "Neu",
            "ccm:wwwurl": ["https://x.example"],
            "cclom:title": "Nein",
        },
    ),
    "update_markers": dict(
        text='[IST-STAND] {"cclom:title": "Aus Marker", "metadataset": "x"} [UPDATE] Neuer Text.',
        llm={"cclom:general_description": "Aus Update"},
    ),
    "partial_failure": dict(
        text="Text.",
        llm={"cclom:title": "T"},
        errors=["schema:startDate: timeout"],
        skipped=2,
    ),
    "nothing_extracted": dict(text="Text.", llm={}, errors=["a", "b"]),
    "no_core": dict(
        text="Text.",
        llm={"cclom:title": "T"},
        include_core=False,
        normalize_output=False,
    ),
    "schema_missing": dict(text="Text.", llm={}, schema_file="fehlt.json"),
    "geocoding_fails": dict(
        text="Text.", llm={"schema:location": ["Weimar"]}, geocoding=True
    ),
}


async def _generate(case: dict, monkeypatch) -> dict:
    _schemas(monkeypatch)
    llm = _LLM(case["llm"], case.get("errors", ()), case.get("skipped", 0))
    service = _service(llm)

    async def geocode(metadata, language, **kwargs):
        raise RuntimeError("Photon nicht erreichbar")

    service.geocoding_service = SimpleNamespace(enrich_metadata_with_geocoding=geocode)
    result = await service.generate_metadata(
        text=case["text"],
        schema_file=case.get("schema_file", "event.json"),
        existing_metadata=case.get("existing"),
        enable_geocoding=case.get("geocoding", False),
        include_core=case.get("include_core", True),
        normalize_output=case.get("normalize_output", True),
        regenerate_fields=case.get("regenerate_fields"),
        regenerate_empty=case.get("regenerate_empty", False),
        origins=case.get("origins"),
    )
    result.pop("exportedAt")
    result["processing"].pop("processing_time_ms")
    result["processing"].pop("auth_source", None)
    result["_asked"] = llm.asked
    return result


def _expected(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", VALIDATION_INPUTS)
def test_validate_metadata_returns_what_it_did_before(monkeypatch, case):
    _schemas(monkeypatch)
    result = _service(None).validate_metadata(
        json.loads(json.dumps(VALIDATION_INPUTS[case]))
    )

    assert result == _expected(f"validate_{case}")


@pytest.mark.parametrize("case", EXPORT_INPUTS)
def test_export_to_markdown_returns_what_it_did_before(monkeypatch, case):
    _schemas(monkeypatch)
    metadata, options = EXPORT_INPUTS[case]
    markdown = _service(None).export_to_markdown(
        json.loads(json.dumps(metadata)), **options
    )

    assert markdown == _expected(f"export_{case}")["markdown"]


@pytest.mark.parametrize("case", GENERATION_INPUTS)
async def test_generate_metadata_returns_what_it_did_before(monkeypatch, case):
    result = await _generate(
        json.loads(json.dumps(GENERATION_INPUTS[case])), monkeypatch
    )

    assert result == _expected(f"generate_{case}")


def test_empty_defaults_are_fresh_objects_per_call():
    """The pre-split code built new lists; a shared one would leak edits between requests."""
    from src.services.metadata_generation import get_default_value_for_field

    first = get_default_value_for_field({"id": "schema:location"})
    first[0]["name"] = "verändert"

    assert get_default_value_for_field({"id": "schema:location"}) == [{}]
