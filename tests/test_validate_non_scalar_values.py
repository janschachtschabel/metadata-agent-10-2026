"""
/validate with an object or a list where a closed vocabulary expects a concept.

Since skos counts as closed (L-09), more than a hundred fields are checked
against their concepts. `v in valid` on a dict or list raised TypeError
(unhashable) and /validate answered 500; 2.1.1 answered with a result.
Such a value is no concept: a warning, never a crash.
"""

from fastapi.testclient import TestClient

from src import main
from src.services.metadata_service import get_metadata_service

DISCIPLINE = "http://w3id.org/openeduhub/vocabs/discipline/460"


def _taxonid_warnings(value):
    result = get_metadata_service().validate_metadata(
        {"ccm:taxonid": value},
        context="default",
        version="2.0.0",
        schema_file="core.json",
    )
    return [w for w in result["warnings"] if w["field_id"] == "ccm:taxonid"]


def test_a_dict_in_a_skos_field_is_a_warning_not_a_crash():
    warnings = _taxonid_warnings([{"uri": DISCIPLINE}])

    assert len(warnings) == 1
    assert "no concept" in warnings[0]["message"]


def test_a_nested_list_in_a_skos_field_is_a_warning_not_a_crash():
    warnings = _taxonid_warnings([[DISCIPLINE]])

    assert len(warnings) == 1
    assert "no concept" in warnings[0]["message"]


def test_a_valid_concept_next_to_a_dict_only_reports_the_dict():
    warnings = _taxonid_warnings([DISCIPLINE, {"uri": DISCIPLINE}])

    assert len(warnings) == 1


def test_the_endpoint_answers_200_for_a_dict_in_a_skos_field():
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/validate", json={"metadata": {"ccm:taxonid": [{"uri": DISCIPLINE}]}}
    )

    assert response.status_code == 200, response.text
    messages = [w["message"] for w in response.json()["warnings"]]
    assert any("no concept" in m for m in messages)
