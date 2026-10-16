"""
The repository gets what the caller reviewed, or it gets nothing.

Normalizing the LLM output (FieldNormalizer) only covers metadata that came out
of `/generate`, and it runs BEFORE the canvas shows the result — so whatever it
changes, an editor still sees and can reject. The web component repairs at the
input for the same reason: the corrected value is on screen before anything is
saved.

The write path has no such luxury. By then the caller has reviewed their data
and pressed save. Quietly lifting `"Musikschule"` into `{"name": "Musikschule"}`
there would put something in the repository that nobody ever saw — for a tool
whose whole purpose is curated metadata, that is worse than a failed write. So
`/upload`, `/nodes` and `/save-node` refuse, name the field, and touch nothing.

They refuse BEFORE the first request. The upload is create-then-populate, so a
late refusal would leave a node carrying only its title.

And `/validate` has to name the same values, otherwise nobody can find the
records that are already broken.
"""

import json
from pathlib import Path

import pytest

from src.services.field_normalizer import find_object_shape_violations
from src.services.metadata_service import MetadataService

SCHEMATA = (
    Path(__file__).resolve().parents[1] / "src" / "schemata" / "mds_oeh" / "v2.0.0"
)

# The three real failures, with the values the records actually carry.
BROKEN = [
    ("education_offer.json", "schema:provider", "Stuttgarter Musikschule"),
    ("person.json", "schema:contactPoint", ["Virtuelles Haus"]),
    ("event.json", "schema:organizer", ["TEEKAYART"]),
]


@pytest.fixture(scope="module")
def service():
    return MetadataService()


@pytest.mark.parametrize("schema_file,field_id,value", BROKEN)
def test_validate_reports_a_scalar_in_an_object_field(
    service, schema_file, field_id, value
):
    result = service.validate_metadata(
        metadata={"metadataset": schema_file, "cclom:title": "Titel", field_id: value},
        context="mds_oeh",
        version="2.0.0",
        schema_file=schema_file,
    )
    complaints = [e for e in result["errors"] if e["field_id"] == field_id]
    assert complaints, (
        f"{schema_file}/{field_id} = {value!r} was accepted. This is the value that "
        f"fixes the Elasticsearch path to `text`; /validate has to name it.\n"
        f"errors={result['errors']} warnings={result['warnings']}"
    )
    assert result["valid"] is False


@pytest.mark.parametrize("schema_file,field_id,value", BROKEN)
def test_a_correct_object_passes_validation(service, schema_file, field_id, value):
    schema = json.loads((SCHEMATA / schema_file).read_text(encoding="utf-8"))
    system = next(f["system"] for f in schema["fields"] if f["id"] == field_id)
    key = "contactType" if field_id == "schema:contactPoint" else "name"
    good = (
        [{key: "Ein Name"}] if system.get("datatype") == "array" else {key: "Ein Name"}
    )

    result = service.validate_metadata(
        metadata={"metadataset": schema_file, "cclom:title": "Titel", field_id: good},
        context="mds_oeh",
        version="2.0.0",
        schema_file=schema_file,
    )
    assert not [e for e in result["errors"] if e["field_id"] == field_id], result[
        "errors"
    ]


@pytest.mark.parametrize("schema_file,field_id,value", BROKEN)
def test_the_write_path_reports_the_field_and_changes_nothing(
    schema_file, field_id, value
):
    metadata = {"metadataset": schema_file, "cclom:title": "Titel", field_id: value}
    before = json.dumps(metadata, ensure_ascii=False, sort_keys=True)

    violations = find_object_shape_violations(metadata, "mds_oeh", "2.0.0", schema_file)

    assert [v["field_id"] for v in violations] == [field_id], violations
    assert violations[0]["expected_key"] in violations[0]["message"]
    assert json.dumps(metadata, ensure_ascii=False, sort_keys=True) == before, (
        "the metadata was modified. The repository must receive what the caller "
        "reviewed — a correction nobody saw is not an improvement."
    )


def test_correct_metadata_produces_no_findings():
    metadata = {
        "metadataset": "organization.json",
        "cclom:title": "Deutsches Technikmuseum",
        "schema:contactPoint": [{"contactType": "Besucherservice", "email": "a@b.de"}],
        "cclom:general_keyword": ["Technik", "Museum"],
        "schema:publicAccess": False,
    }
    assert (
        find_object_shape_violations(metadata, "mds_oeh", "2.0.0", "organization.json")
        == []
    )


def test_an_unknown_schema_does_not_block_the_write():
    """Refusing because a schema file is missing would be the worse failure."""
    metadata = {"cclom:title": "Titel", "schema:provider": "Irgendwer"}
    assert (
        find_object_shape_violations(metadata, "mds_oeh", "2.0.0", "gibtsnicht.json")
        == []
    )


def test_the_refusal_happens_before_anything_is_written():
    """
    Both write flows create the node first and populate it afterwards, so the
    check has to sit ahead of the first request — otherwise a refused write
    leaves a node behind carrying nothing but its title.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "services"
        / "repository_service.py"
    ).read_text(encoding="utf-8")

    for method in ("async def upload_metadata", "async def save_node"):
        body = source[source.index(method) :]
        check = body.index("find_object_shape_violations")
        first_write = min(
            body.index("_create_node("),
            body.index("_set_metadata("),
        )
        assert check < first_write, (
            f"{method}: the shape check runs after the first repository write. "
            "A refused upload would leave a half-created node in the inbox."
        )
