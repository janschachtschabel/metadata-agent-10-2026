"""
/generate, /detect-content-type and /extract-field take their input alike.

Audit 13.11., A-02: the block that says where the text comes from stood three
times in schemas.py, with its validators. It is one base model now; the three
requests must keep it in front, in this order (the order of the OpenAPI).
"""

import pytest

from src.models.schemas import (
    DetectContentTypeRequest,
    ExtractFieldRequest,
    GenerateRequest,
    InputSourceRequest,
)

BLOCK = [
    "input_source",
    "text",
    "source_url",
    "extraction_method",
    "output_format",
    "node_id",
    "repository",
]


@pytest.mark.parametrize(
    "model", [GenerateRequest, DetectContentTypeRequest, ExtractFieldRequest]
)
def test_a_request_starts_with_the_shared_input_block(model):
    assert issubclass(model, InputSourceRequest)
    assert list(model.model_fields)[: len(BLOCK)] == BLOCK
    # The validators of the block come along.
    required = {"field_id": "cclom:title", "schema_file": "event.json"}
    fields = model.model_fields
    own = {k: v for k, v in required.items() if k in fields and fields[k].is_required()}
    assert model(text="  a\x00b  ", **own).text == "ab"
    with pytest.raises(ValueError):
        model(input_source="node_id", node_id="../../etc/passwd", **own)
