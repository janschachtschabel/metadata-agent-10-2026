"""
/extract-field's core.json fallback survives any fault (final audit 18.10.).

2.1.1 caught every exception when looking a field up in core.json; 3.0.0 only
ValueError, so an unreadable core.json (OSError, broken JSON) turned "field not
found" into a 500.
"""

import json
import logging

import pytest
from fastapi import HTTPException

from src.api.routers import extract as extract_module
from src.models.schemas import ExtractFieldRequest


@pytest.mark.parametrize(
    "fault", [OSError("disk"), json.JSONDecodeError("broken", "{", 0)]
)
def test_an_unreadable_core_json_reports_the_field_as_unknown(
    monkeypatch, caplog, fault
):
    original = extract_module.load_schema

    def load_schema(context, version, schema_file):
        if schema_file == "core.json":
            raise fault
        return original(context, version, schema_file)

    monkeypatch.setattr(extract_module, "load_schema", load_schema)
    req = ExtractFieldRequest(
        text="Ein Text.", schema_file="event.json", field_id="ccm:gibt_es_nicht"
    )

    with caplog.at_level(logging.WARNING, logger=extract_module.__name__):
        with pytest.raises(HTTPException) as refused:
            extract_module._field_definition(req)

    assert refused.value.status_code == 404
    assert "core.json" in caplog.text
