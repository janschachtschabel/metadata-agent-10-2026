"""
/validate against closed vocabularies keyed by `value` instead of `uri`.

Languages, licences and nationalities list their concepts as
{"value": "de"}; checking only `uri` reported every valid entry of those
fields as "not in vocabulary".
"""

from src.services.metadata_service import get_metadata_service


def _warnings_for(field_id, metadata, schema_file):
    result = get_metadata_service().validate_metadata(
        metadata, context="default", version="2.0.0", schema_file=schema_file
    )
    return [w for w in result.get("warnings", []) if w["field_id"] == field_id]


def test_a_valid_language_code_is_accepted():
    assert (
        _warnings_for(
            "cclom:general_language", {"cclom:general_language": ["de"]}, "core.json"
        )
        == []
    )


def test_a_valid_licence_key_is_accepted():
    metadata = {"ccm:commonlicense_key": "CC_BY"}
    assert (
        _warnings_for("ccm:commonlicense_key", metadata, "learning_material.json") == []
    )


def test_an_unknown_language_code_is_still_reported():
    assert _warnings_for(
        "cclom:general_language",
        {"cclom:general_language": ["xx-unknown"]},
        "core.json",
    )
