"""
A field declared in core.json and in the content type is extracted once (audit 14.11.2026, L-35).

ccm:educationalintendedenduserrole stands in core.json and in four content
types (event, course, education_program, prompt). Both definitions went to the
model - two calls - and whichever answer came back later was the result. The
content type's definition wins: it is the more specific one, and it is the one
the normaliser and the validator already used.
"""

import collections
import json
import re

import httpx
import pytest

from src.config import Settings
from src.services import metadata_service as metadata_module
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.metadata_generation import load_extraction_fields
from src.services.metadata_service import MetadataService
from src.utils.schema_loader import get_ai_fillable_fields
from conftest import use_settings

ROLE = "ccm:educationalintendedenduserrole"
TEACHER = "http://w3id.org/openeduhub/vocabs/intendedEndUserRole/teacher"


class _Gateway:
    """Counts the field calls by the field id in the prompt."""

    def __init__(self):
        self.calls: collections.Counter = collections.Counter()

    def handler(self, request):
        prompt = json.loads(request.content)["messages"][-1]["content"]
        found = re.search(r"\(ID: ([^)]+)\)", prompt)
        field_id = found.group(1) if found else "?"
        self.calls[field_id] += 1
        value = [TEACHER] if field_id == ROLE else None
        content = json.dumps({"value": value})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )


@pytest.fixture
def gateway(monkeypatch):
    reset_provider_gates()
    fake = _Gateway()
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    llm = LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
    llm.http_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(metadata_module, "get_llm_service", lambda **kw: llm)
    yield fake
    reset_provider_gates()


async def test_no_field_of_an_event_is_asked_for_twice(gateway):
    result = await MetadataService().generate_metadata(
        "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
        schema_file="event.json",
        version="2.0.0",
        enable_geocoding=False,
    )

    assert gateway.calls[ROLE] == 1
    # A required field without a value is asked for once more (L-46).
    assert max(gateway.calls.values()) <= 2
    assert result["metadata"][ROLE] == [TEACHER]
    # 47 ids, not 48 definitions.
    assert result["processing"]["fields_total"] == len(
        {
            f["id"]
            for f in load_extraction_fields("default", "2.0.0", "event.json", True, [])
        }
    )
    assert result["processing"]["fields_total"] == 47


@pytest.mark.parametrize(
    "schema_file",
    ["event.json", "course.json", "education_program.json", "prompt.json"],
)
def test_the_definition_of_the_content_type_wins(schema_file):
    fields = load_extraction_fields("default", "2.0.0", schema_file, True, [])
    own = get_ai_fillable_fields("default", "2.0.0", schema_file)

    ids = [field["id"] for field in fields]
    assert len(ids) == len(set(ids))
    (role,) = [field for field in fields if field["id"] == ROLE]
    assert role is next(field for field in own if field["id"] == ROLE)


def test_core_fields_still_come_first():
    fields = load_extraction_fields("default", "2.0.0", "event.json", True, [])
    core = get_ai_fillable_fields("default", "2.0.0", "core.json")

    assert fields[0]["id"] == core[0]["id"]
