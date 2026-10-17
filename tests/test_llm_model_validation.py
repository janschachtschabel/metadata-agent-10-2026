"""
llm_model comes from the request body and goes on unchanged: into the body
sent to the B-API, into error messages and into log lines. It is a model or
route name, so it is held to what such names look like - short, and without
blanks or control characters that could forge a log line.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src import main
from src.models.schemas import (
    DetectContentTypeRequest,
    ExtractFieldRequest,
    GenerateRequest,
)

VALID = [
    "gpt-6-luna",
    "openai/gpt-6-luna",
    "deepseek-v4-flash",
    "metadata_agent.v2",
    "org:model@1",
]
INVALID = ["gpt 6", "gpt-6\nINFO forged", "a" * 201, "modell-ä", "x;rm"]


def _requests(model):
    return [
        GenerateRequest(text="Ein Text.", llm_model=model),
        DetectContentTypeRequest(text="Ein Text.", llm_model=model),
        ExtractFieldRequest(
            text="Ein Text.",
            field_id="cclom:title",
            schema_file="event.json",
            llm_model=model,
        ),
    ]


@pytest.mark.parametrize("model", VALID)
def test_model_and_route_names_are_accepted(model):
    for request in _requests(model):
        assert request.llm_model == model


@pytest.mark.parametrize("model", INVALID)
def test_anything_else_is_refused(model):
    for build in (GenerateRequest, DetectContentTypeRequest):
        with pytest.raises(ValidationError):
            build(text="Ein Text.", llm_model=model)
    with pytest.raises(ValidationError):
        ExtractFieldRequest(
            text="Ein Text.",
            field_id="cclom:title",
            schema_file="event.json",
            llm_model=model,
        )


def test_the_api_answers_422_before_any_llm_call():
    with TestClient(main.app) as client:
        response = client.post(
            "/generate", json={"text": "Ein Text.", "llm_model": "gpt 6\nx"}
        )

    assert response.status_code == 422
