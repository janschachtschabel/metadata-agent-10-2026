"""
Update markers with a record that is too deep or empty (audit 14.11.2026, L-38).

- "[IST-STAND] [[[[… [UPDATE] text" with thousands of brackets ended in a 500:
  json.loads raises RecursionError, and only JSONDecodeError was caught. Such
  a text is no record - it stays plain text, like any other broken JSON.
- "[IST-STAND] {} [UPDATE] text" was not split at all: the empty record
  counted as "no markers", and the markers went to the model as part of the
  text. An empty record is a record: the text after [UPDATE] is the source,
  the metadata of the request stays as it is, and the marker warning is given.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.config import Settings
from src.services import metadata_service as metadata_module
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.metadata_generation import apply_update_markers, parse_update_text
from src.services.metadata_service import MetadataService
from conftest import use_settings


@pytest.mark.parametrize("depth", [2_000, 5_000, 100_000])
@pytest.mark.parametrize("bracket", ["[", '{"a":'])
def test_a_record_nested_too_deep_is_plain_text(depth, bracket):
    text = "[IST-STAND] " + bracket * depth + " [UPDATE] neuer Text"

    assert parse_update_text(text) == (text, None)
    assert apply_update_markers(text, {"a": 1}) == (text, {"a": 1}, False)


def test_generate_answers_a_deep_record_without_a_500(monkeypatch):
    # The real MetadataService and LLMService in front of a fake gateway.
    reset_provider_gates()
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    llm = LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
    answer = {"choices": [{"message": {"content": '{"value": null}'}}]}
    llm.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=answer))
    )
    monkeypatch.setattr(metadata_module, "get_llm_service", lambda **kw: llm)
    service = MetadataService()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    text = "[IST-STAND] " + "[" * 5_000 + " [UPDATE] neuer Text"

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={"text": text, "schema_file": "event.json", "enable_geocoding": False},
        )
    reset_provider_gates()

    assert response.status_code == 200, response.text
    assert response.json()["processing"]["status"] == "success"


@pytest.mark.parametrize(
    "text",
    [
        "[IST-STAND] {} [UPDATE] Neuer Text",
        "[CURRENT]{}[UPDATE]Neuer Text",
        # Nothing but the header of an exported record.
        '[IST-STAND] {"contextName": "default", "language": "de"} [UPDATE] Neuer Text',
    ],
)
def test_an_empty_record_still_splits_the_text(text):
    assert parse_update_text(text) == ("Neuer Text", {})
    assert apply_update_markers(text, None) == ("Neuer Text", None, True)
    assert apply_update_markers(text, {"a": 1}) == ("Neuer Text", {"a": 1}, True)


def test_a_record_with_values_merges_as_before():
    text = '[ist-stand] {"cclom:title": "T"} [update] Neuer Text'

    assert apply_update_markers(text, {"cclom:title": "alt", "x": 1}) == (
        "Neuer Text",
        {"cclom:title": "T", "x": 1},
        True,
    )
    assert apply_update_markers(text, None) == (
        "Neuer Text",
        {"cclom:title": "T"},
        True,
    )


def test_text_without_markers_is_untouched():
    assert apply_update_markers("Ein Text", {"a": 1}) == ("Ein Text", {"a": 1}, False)
