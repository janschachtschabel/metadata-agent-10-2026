"""
What the Qwen short test of 2026-10-15 found in the agent itself.

- The extraction prompt listed only the first 20 concepts of a vocabulary.
  Physik is concept 46 of 69 in ccm:taxonid; models that keep to the list
  answered "Chemie" for a physics page.
- Content-type detection runs under the extraction system prompt, which asks
  for the key "value"; qwen3.6 answered {"value": "event.json"} and the agent
  discarded a correct answer.
- Thinking models (qwen3.6-35b-a3b) spend the token budget on reasoning
  unless told reasoning_effort "none", and return content null. The agent
  counted that as "no value in the text" - a silent failure.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.services import llm_service as llm_module
from src.services.llm_errors import LLMAnswerTruncated, LLMEmptyAnswer
from src.services.llm_service import LLMService, reset_provider_gates
from src.utils.schema_loader import get_ai_fillable_fields, get_latest_version
from conftest import use_settings

MESSAGES = [{"role": "user", "content": "x"}]
CONTENT_TYPES = [
    {"schema_file": "event.json", "label": {"de": "Veranstaltung"}},
    {"schema_file": "learning_material.json", "label": {"de": "Lernmaterial"}},
]


class _Gateway:
    def __init__(self, answer=None, finish="stop"):
        self.answer, self.finish = answer, finish
        self.calls = []

    def handler(self, request):
        self.calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": self.answer},
                        "finish_reason": self.finish,
                    }
                ]
            },
        )


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(gateway, monkeypatch, provider="b-api-openai", model=None, **settings):
    defaults = {"b_api_key": "own-key", "llm_retry_delay": 0}
    cfg = Settings(_env_file=None, **{**defaults, **settings})
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider=provider, llm_model=model)
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    return service


def _core_field(field_id):
    fields = get_ai_fillable_fields(
        "default", get_latest_version("default"), "core.json"
    )
    return next(f for f in fields if f["id"] == field_id)


# ------------------------------------------------------------ vocabulary


def test_the_prompt_offers_every_subject_not_just_the_first_twenty(monkeypatch):
    service = _service(_Gateway(), monkeypatch)
    field = _core_field("ccm:taxonid")
    assert len(field["system"]["vocabulary"]["concepts"]) > 20

    prompt = service._build_extraction_prompt(
        field, "Gleichförmige Bewegung", None, "de"
    )

    assert "Physik" in prompt
    assert "weitere" not in prompt


def test_a_huge_vocabulary_is_still_capped(monkeypatch):
    service = _service(_Gateway(), monkeypatch)
    concepts = [
        {"uri": f"http://v/{i}", "label": {"de": f"Begriff {i}"}} for i in range(900)
    ]
    field = {
        "id": "x",
        "label": {"de": "X"},
        "system": {
            "datatype": "string",
            "vocabulary": {"type": "skos", "concepts": concepts},
        },
    }

    prompt = service._build_extraction_prompt(field, "Text", None, "de")

    assert "Begriff 299" in prompt and "Begriff 300" not in prompt
    assert "600 weitere" in prompt


# ------------------------------------------------------------ detection


@pytest.mark.asyncio
async def test_detection_accepts_the_value_key_the_system_prompt_asks_for(monkeypatch):
    service = _service(_Gateway('{\n  "value": "event.json"\n}'), monkeypatch)

    detection = await service.detect_content_type(
        "Konzert am 12. November", CONTENT_TYPES
    )

    assert detection.schema_file == "event.json"


# ------------------------------------------------------------ thinking models


@pytest.mark.asyncio
async def test_router_classic_body_can_carry_reasoning_effort(monkeypatch):
    gateway = _Gateway('{"value": "Physik"}')
    service = _service(
        gateway,
        monkeypatch,
        provider="b-api-router",
        model="academiccloud/qwen3.6-35b-a3b",
        llm_request_style="classic",
        b_api_router_reasoning_effort="none",
    )

    await service._call_llm(MESSAGES)

    body = gateway.calls[0]
    assert body["reasoning_effort"] == "none"
    assert "max_tokens" in body and "temperature" in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider, model",
    [("b-api-router", "schnell-guenstig"), ("b-api-academiccloud", None)],
)
async def test_without_the_setting_no_reasoning_effort_is_sent(
    monkeypatch, provider, model
):
    # gpt-4o and older classic models reject the parameter with 400.
    gateway = _Gateway('{"value": "x"}')
    service = _service(
        gateway,
        monkeypatch,
        provider=provider,
        model=model,
        llm_request_style="classic",
    )

    await service._call_llm(MESSAGES)

    assert "reasoning_effort" not in gateway.calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, ""])
async def test_an_empty_answer_is_an_error_not_a_missing_value(monkeypatch, answer):
    gateway = _Gateway(answer, finish="length")
    service = _service(gateway, monkeypatch)

    with pytest.raises(LLMEmptyAnswer) as raised:
        await service._call_llm(MESSAGES)

    assert not raised.value.fatal
    assert "length" in raised.value.message


@pytest.mark.asyncio
async def test_a_field_with_an_empty_answer_counts_as_failed(monkeypatch):
    service = _service(_Gateway(None, finish="length"), monkeypatch)

    field_id, value, error = await service.extract_field(
        _core_field("cclom:title"), "Text", None, "de"
    )

    assert value is None
    assert isinstance(error, LLMEmptyAnswer)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, expected",
    [
        ('{"value": ["learning_material.json"]}', "learning_material.json"),
        ('{"content_type": ["event.json", "learning_material.json"]}', None),
        ('{"value": {"schema": "event.json"}}', None),
        ('["event.json"]', "event.json"),
    ],
)
async def test_detection_survives_answers_that_are_not_a_plain_string(
    monkeypatch, answer, expected
):
    # qwen3.6 wrapped its answer in a list; `answer in allowed` raised TypeError.
    service = _service(_Gateway(answer), monkeypatch)

    detection = await service.detect_content_type(
        "Lernmaterial zur Bewegung", CONTENT_TYPES
    )

    assert detection.schema_file == expected
    if expected is None:
        assert detection.problem


# ------------------------------------------- answers that are no JSON object


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, field_id, expected",
    [
        ('"Bruchrechnung Klasse 6"', "cclom:title", "Bruchrechnung Klasse 6"),
        (
            '["Bruchrechnung", "Mathematik"]',
            "cclom:general_keyword",
            ["Bruchrechnung", "Mathematik"],
        ),
    ],
)
async def test_a_bare_json_value_is_taken_as_the_value(
    monkeypatch, answer, field_id, expected
):
    # json.loads returns a str or list here; `.get("value")` used to raise,
    # and the generic retry spent up to three more calls on a usable answer.
    gateway = _Gateway(answer)
    service = _service(gateway, monkeypatch)

    _, value, error = await service.extract_field(
        _core_field(field_id), "Text", None, "de"
    )

    assert error is None
    assert value == expected
    assert len(gateway.calls) == 1


# ------------------------------------------------------- rate limits (429)


class _ThrottlingGateway(_Gateway):
    """Answers 429 with Retry-After first, then a normal answer."""

    def __init__(self, retry_after):
        super().__init__('{"value": "Titel"}')
        self.retry_after = retry_after

    def handler(self, request):
        if not self.calls:
            self.calls.append(json.loads(request.content))
            return httpx.Response(
                429, headers={"Retry-After": self.retry_after}, text="slow down"
            )
        return super().handler(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after, expected", [("3", 3.0), ("600", 30.0)])
async def test_a_429_waits_as_long_as_the_gateway_asks_within_a_cap(
    monkeypatch, retry_after, expected
):
    # The linear 1 s/2 s backoff ignored Retry-After; against a gateway that
    # refills per minute both retries landed in the same closed window.
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(llm_module.asyncio, "sleep", fake_sleep)
    service = _service(_ThrottlingGateway(retry_after), monkeypatch, llm_retry_delay=1)

    answer = await service._call_llm(MESSAGES)

    assert answer["choices"][0]["message"]["content"] == '{"value": "Titel"}'
    assert waits == [expected]


# ------------------------------------------------------------ cut-off answers
#
# Audit 13.11., L-17: an answer cut off at the token limit (finish_reason
# "length") still has text. `{"value": "Ein langer Text` is no JSON; the
# fallback then took the first text in quotes - the key itself - and the
# description of the record became the word "value".


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    ['{"value": "Ein langer Text, der mitten im Satz', '{"value": [{"name": "A"}, {"n'],
)
async def test_an_answer_cut_off_at_the_token_limit_is_a_failure_not_a_value(
    monkeypatch, answer
):
    service = _service(_Gateway(answer, finish="length"), monkeypatch)

    field_id, value, error = await service.extract_field(
        _core_field("cclom:general_description"), "Ein Text", None, "de"
    )

    assert value is None
    assert isinstance(error, LLMAnswerTruncated)
    assert error.code == "LLM_ANSWER_TRUNCATED"
    assert "METADATA_AGENT_LLM_MAX_TOKENS" in error.message


@pytest.mark.asyncio
async def test_a_complete_answer_is_taken_as_before(monkeypatch):
    service = _service(_Gateway('{"value": "Eine Beschreibung."}'), monkeypatch)

    _, value, error = await service.extract_field(
        _core_field("cclom:general_description"), "Ein Text", None, "de"
    )

    assert (value, error) == ("Eine Beschreibung.", None)


@pytest.mark.parametrize(
    "content, expected",
    [
        ('{"value": "abgeschnitten', None),
        ('[{"name": "A"}, {"na', None),
        ('Der Titel lautet "Konzert in Weimar".', {"value": "Konzert in Weimar"}),
        ('"Konzert"', {"value": "Konzert"}),
    ],
)
def test_broken_json_is_no_source_of_values(monkeypatch, content, expected):
    service = _service(_Gateway(), monkeypatch)

    # Callers read the key "value"; whatever else the remains parse to is none.
    parsed = service._parse_json_response(content) or {}

    assert parsed.get("value") == (expected or {}).get("value")
