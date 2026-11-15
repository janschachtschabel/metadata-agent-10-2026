"""
The cache primer runs while the content type is detected (15.11.2026).

Both need nothing but the text, and they were sent one after the other: the
detection, then the primer, then the field calls. Measured through the B-API
the primer takes 1.1 s (gpt-6-luna, 3,900 tokens) - time in which the answer of
the detection was already there and no field call was on its way.
"""

import asyncio
import json

import httpx
import pytest

from src.config import Settings
from src.services import metadata_service as metadata_module
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.metadata_service import MetadataService
from src.utils.schema_loader import get_ai_fillable_fields
from conftest import use_settings

# Above the primer's minimum length (llm_service._PRIMER_MIN_TEXT_CHARS).
LONG_TEXT = "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin. " * 90
SHORT_TEXT = "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin."


def _answer(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class _Gateway:
    """Tells primer, detection and field calls apart and notes their order."""

    def __init__(self, detection=None, primer_answers=True):
        self.kinds: list[str] = []
        self.primer_arrived = asyncio.Event()
        self.primer_arrived_during_detection = False
        self.primer_stopped = False
        self._detection = detection or _answer('{"content_type": "event.json"}')
        self._primer_answers = primer_answers

    async def handler(self, request):
        messages = json.loads(request.content)["messages"]
        if len(messages) == 3:
            self.kinds.append("field")
            return _answer('{"value": null}')
        if isinstance(messages[1]["content"], list):
            self.kinds.append("primer")
            self.primer_arrived.set()
            if not self._primer_answers:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.primer_stopped = True
                    raise
            return _answer("ok")
        self.kinds.append("detection")
        # The answer waits for the primer: sent only after the detection, it
        # never arrives in time.
        try:
            await asyncio.wait_for(self.primer_arrived.wait(), 0.5)
            self.primer_arrived_during_detection = True
        except asyncio.TimeoutError:
            pass
        return self._detection


def _llm(monkeypatch, gateway):
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    llm = LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
    llm.http_client = httpx.AsyncClient(transport=httpx.MockTransport(gateway.handler))
    return llm


def _run(monkeypatch, gateway, text, **options):
    llm = _llm(monkeypatch, gateway)
    monkeypatch.setattr(metadata_module, "get_llm_service", lambda **kw: llm)
    return MetadataService().generate_metadata(
        text, version="2.0.0", enable_geocoding=False, **options
    )


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


async def test_the_primer_is_on_its_way_while_the_type_is_detected(monkeypatch):
    gateway = _Gateway()

    result = await _run(monkeypatch, gateway, LONG_TEXT, schema_file="auto")

    assert gateway.primer_arrived_during_detection
    assert gateway.kinds.count("primer") == 1, "sent once, not again at the fan-out"
    assert gateway.kinds.count("detection") == 1
    # No field call before the primer: it could not read what is not written yet.
    assert "field" not in gateway.kinds[: gateway.kinds.index("primer")]
    assert result["metadataset"] == "event.json"
    assert result["processing"]["fields_total"] == 47


async def test_a_chosen_type_primes_right_before_the_field_calls(monkeypatch):
    gateway = _Gateway()

    await _run(monkeypatch, gateway, LONG_TEXT, schema_file="event.json")

    assert gateway.kinds[0] == "primer"
    assert gateway.kinds.count("primer") == 1
    assert "detection" not in gateway.kinds


async def test_a_short_text_gets_no_early_primer_either(monkeypatch):
    gateway = _Gateway()

    await _run(monkeypatch, gateway, SHORT_TEXT, schema_file="auto")

    assert "primer" not in gateway.kinds
    assert gateway.kinds[0] == "detection"


async def test_a_single_field_call_does_not_wait_for_the_primer(monkeypatch):
    # One field call cannot read back what the primer wrote: it is sent at
    # once, and the primer that is still on its way is stopped.
    gateway = _Gateway(primer_answers=False)
    llm = _llm(monkeypatch, gateway)
    (title,) = [
        f
        for f in get_ai_fillable_fields("default", "2.0.0", "core.json")
        if f["id"] == "cclom:title"
    ]

    async with llm.text_cache_primer(LONG_TEXT, "de") as primer:
        await asyncio.wait_for(gateway.primer_arrived.wait(), 2)
        await asyncio.wait_for(
            llm.extract_fields_parallel([title], LONG_TEXT, primer=primer), 5
        )

    # The required title without a value is asked for once more (L-46).
    assert gateway.kinds == ["primer", "field", "field"]
    assert gateway.primer_stopped


async def test_a_refused_key_at_the_detection_stops_the_primer(monkeypatch):
    # The run ends there; a primer nobody waits for must not stay on its way.
    gateway = _Gateway(
        detection=httpx.Response(401, text="invalid key"), primer_answers=False
    )

    result = await _run(monkeypatch, gateway, LONG_TEXT, schema_file="auto")

    assert result["processing"]["status"] == "failed"
    assert gateway.primer_stopped
    assert "field" not in gateway.kinds


async def test_with_metadata_at_hand_the_primer_waits_for_the_fields(monkeypatch):
    # A re-extraction may leave one field or none to extract, and that is known
    # only after the detection. A primer sent before it was stopped half-way
    # and may be billed all the same (review 15.11.).
    gateway = _Gateway()

    await _run(
        monkeypatch,
        gateway,
        LONG_TEXT,
        schema_file="auto",
        existing_metadata={"cclom:title": "Workshop"},
    )

    assert not gateway.primer_arrived_during_detection
    assert gateway.kinds[:2] == ["detection", "primer"]
    assert gateway.kinds.count("primer") == 1
