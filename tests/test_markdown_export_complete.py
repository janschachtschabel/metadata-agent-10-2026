"""
The Markdown export shows every filled field once, under the content type's
name (audit 14.11., L-39 / K-06).

Fields of a group that neither core.json nor the content type declares were
left out (the learning resource type in an event), a field both schemas declare
came twice (the audience), `[0]` and `[False]` counted as empty, and the title
was the file name - the schema files carry no label, the content-type concept
in core.json does. /info/schemas returns that label too.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.services.metadata_export import export_to_markdown, has_meaningful_value
from src.utils.schema_loader import get_available_schemas

LRT = "http://w3id.org/openeduhub/vocabs/new_lrt/36e68792-6159-481d-a97b-2c00901f4f78"
ROLE = "http://w3id.org/openeduhub/vocabs/intendedEndUserRole/teacher"
RECORD = {
    "cclom:title": "Bruchrechnung",
    "ccm:oeh_lrt": [LRT],
    "ccm:educationalintendedenduserrole": [ROLE],
}
SCHEMA_FILES = ["event.json", "learning_material.json", "core.json", "person.json"]


def _export(schema_file, language="de", **record):
    return export_to_markdown(
        {**RECORD, **record}, "default", "2.0.0", schema_file, language
    )


@pytest.mark.parametrize("schema_file", SCHEMA_FILES)
def test_every_filled_field_stands_there_exactly_once(schema_file):
    markdown = _export(schema_file)

    for line in (
        "**Titel:** Bruchrechnung",
        "**Lernressourcentyp:** Arbeitsblatt",
        "**Zielgruppe:** Lehrer/in",
    ):
        assert markdown.count(line) == 1, (line, markdown)


def test_fields_of_an_undeclared_group_come_last_under_their_own_heading():
    markdown = _export("event.json", **{"schema:maximumAttendeeCapacity": 12})

    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings[-1] == "## Weitere Angaben"
    assert markdown.index("## Weitere Angaben") < markdown.index("Lernressourcentyp")
    assert markdown.index("Teilnehmendenzahl") < markdown.index("## Weitere Angaben")
    assert "## Further details" in _export("event.json", "en")


def test_no_trailing_section_without_such_a_field():
    markdown = export_to_markdown(
        {"cclom:title": "T"}, "default", "2.0.0", "event.json"
    )

    assert "Weitere Angaben" not in markdown


@pytest.mark.parametrize(
    "schema_file, language, title",
    [
        ("event.json", "de", "# Veranstaltung"),
        ("event.json", "en", "# Event"),
        ("learning_material.json", "de", "# Lernmaterial"),
        ("core.json", "de", "# core.json"),  # no content type
    ],
)
def test_the_title_is_the_label_of_the_content_type(schema_file, language, title):
    assert _export(schema_file, language).splitlines()[0] == title


@pytest.mark.parametrize(
    "value", [0, False, [0], [False], [0.0], {"a": 0}, {"a": False}, [{"a": 0}], "x"]
)
def test_zero_and_false_are_values(value):
    assert has_meaningful_value(value)


@pytest.mark.parametrize(
    "value", [None, "", [], {}, [None], [""], [{}], [[]], {"a": None}, [{"a": ""}]]
)
def test_nothing_but_empty_entries_is_empty(value):
    assert not has_meaningful_value(value)


def test_a_list_of_zero_is_exported():
    markdown = _export("event.json", **{"schema:maximumAttendeeCapacity": [0]})

    assert "**Max. Teilnehmendenzahl:** 0" in markdown


# ------------------------------------------------------------ /info/schemas


def test_the_schema_list_carries_the_labels_of_the_content_types():
    labels = {
        schema["file"]: schema["label"]
        for schema in get_available_schemas("default", "2.0.0")
    }

    assert labels["event.json"] == {"de": "Veranstaltung", "en": "Event"}
    assert labels["learning_material.json"]["de"] == "Lernmaterial"
    # core.json is no content type: its file name stays.
    assert labels["core.json"] == {"de": "core.json", "en": "core.json"}
    named_like_the_file = [
        file for file, label in labels.items() if label.get("de") == file
    ]
    assert named_like_the_file == ["core.json"]


def test_the_endpoint_answers_with_them():
    with TestClient(main.app) as client:
        response = client.get("/info/schemas/default/latest")

    assert response.status_code == 200
    labels = {schema["file"]: schema["label"] for schema in response.json()}
    assert labels["event.json"] == {"de": "Veranstaltung", "en": "Event"}
