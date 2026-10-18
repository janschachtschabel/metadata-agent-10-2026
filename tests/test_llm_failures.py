"""
How LLM calls fail - and that they never fail silently.

Observed in production on 2026-10-14 through the WLO proxy: every field call
was refused with 403, yet /generate answered 200 with processing.success=true
and 0 of 50 fields, and the content type came back as learning_material.json
with confidence 'high'. Each refused field was additionally retried three
times. These tests pin the opposite: a refused key stops the work at once and
surfaces as a typed error; nothing is turned into a confident result.
"""

import httpx
import pytest

from src.config import Settings
from src.services import llm_service as llm_module
from src.services.llm_auth import forwarded_authorization
from src.services.llm_errors import (
    LLMAuthForbidden,
    LLMAuthRejected,
    LLMModelNotFound,
    LLMRateLimited,
    LLMRequestRejected,
    LLMUnreachable,
    LLMUpstreamError,
)
from src.services.llm_service import LLMService, reset_provider_gates

B_API_403_BODY = (
    '{"timestamp":"2026-10-14T13:44:36.107Z","status":403,"error":"Forbidden",'
    '"trace":"org.springframework.security.authorization.AuthorizationDeniedException: '
    'Access Denied\\n\\tat org.springframework...OpenAiApiService.forward"}'
)


class _Gateway:
    """A fake B-API: answers every call the same way and records what it got."""

    def __init__(self, status=200, body=None, exc=None):
        self.status, self.body, self.exc = status, body, exc
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.exc:
            raise self.exc
        if self.status == 200:
            return httpx.Response(200, json=self.body)
        return httpx.Response(self.status, text=self.body or "")


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(gateway, monkeypatch, **settings):
    cfg = Settings(_env_file=None, llm_retry_delay=0, b_api_key="own-key", **settings)
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    return service


def _answer(content):
    return {"choices": [{"message": {"content": content}}]}


MESSAGES = [{"role": "user", "content": "x"}]


# ------------------------------------------------------------ _call_llm


@pytest.mark.asyncio
async def test_a_403_is_not_retried_and_raised_as_forbidden(monkeypatch):
    gateway = _Gateway(403, B_API_403_BODY)
    service = _service(gateway, monkeypatch)

    with pytest.raises(LLMAuthForbidden) as raised:
        await service._call_llm(MESSAGES)

    assert len(gateway.calls) == 1
    assert raised.value.fatal
    assert raised.value.upstream_status == 403
    assert raised.value.auth_source == "static"
    assert "trace" not in raised.value.message  # no upstream stack trace


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["", "\n", "   "])
async def test_a_403_without_a_body_is_still_a_fatal_forbidden(monkeypatch, body):
    # An empty or blank body used to raise IndexError while being shortened -
    # the 403 then passed as an ordinary error and nothing stopped early.
    gateway = _Gateway(403, body)
    service = _service(gateway, monkeypatch)

    with pytest.raises(LLMAuthForbidden) as raised:
        await service._call_llm(MESSAGES)

    assert raised.value.fatal
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_a_403_does_not_claim_to_know_which_right_is_missing(monkeypatch):
    # In production the right missing was the LLM endpoint (OpenAiApiService
    # .forward), not the model - the message must not point at the model only.
    service = _service(_Gateway(403, B_API_403_BODY), monkeypatch)

    with pytest.raises(LLMAuthForbidden) as raised:
        await service._call_llm(MESSAGES)

    assert "Endpunkt" in raised.value.message
    assert "gpt-6-luna" in raised.value.message


@pytest.mark.asyncio
async def test_a_404_names_the_model_and_logs_the_address(monkeypatch, caplog):
    service = _service(_Gateway(404, ""), monkeypatch)

    with pytest.raises(LLMModelNotFound) as raised:
        await service._call_llm(MESSAGES)

    assert "gpt-6-luna" in raised.value.message
    # The address is for the operator, not for clients (audit S-06).
    assert service.api_base not in raised.value.message
    assert service.api_base in caplog.text


@pytest.mark.asyncio
async def test_a_401_is_raised_as_rejected_key(monkeypatch):
    service = _service(_Gateway(401, ""), monkeypatch)

    with pytest.raises(LLMAuthRejected):
        await service._call_llm(MESSAGES)


@pytest.mark.asyncio
async def test_a_forwarded_key_goes_out_alone_and_unchanged(monkeypatch):
    gateway = _Gateway(200, _answer("{}"))
    service = _service(gateway, monkeypatch)

    with forwarded_authorization("Bearer user-key"):
        await service._call_llm(MESSAGES)

    sent = gateway.calls[0].headers
    assert sent["authorization"] == "Bearer user-key"
    assert "x-api-key" not in sent


@pytest.mark.asyncio
async def test_without_forwarded_key_the_own_key_goes_out_as_x_api_key(monkeypatch):
    gateway = _Gateway(200, _answer("{}"))
    service = _service(gateway, monkeypatch)

    await service._call_llm(MESSAGES)

    sent = gateway.calls[0].headers
    assert sent["x-api-key"] == "own-key"
    assert "authorization" not in sent


@pytest.mark.asyncio
async def test_persistent_throttling_ends_as_rate_limited(monkeypatch):
    gateway = _Gateway(429, '{"message":"API rate limit exceeded"}')
    service = _service(gateway, monkeypatch)

    with pytest.raises(LLMRateLimited) as raised:
        await service._call_llm(MESSAGES)

    assert len(gateway.calls) == service.max_retries
    assert raised.value.http_status == 503


@pytest.mark.asyncio
async def test_persistent_server_errors_end_as_a_non_fatal_upstream_error(monkeypatch):
    service = _service(_Gateway(500, "boom"), monkeypatch)

    with pytest.raises(LLMUpstreamError) as raised:
        await service._call_llm(MESSAGES)

    assert not raised.value.fatal


@pytest.mark.asyncio
async def test_another_client_error_rejects_only_this_request(monkeypatch):
    service = _service(
        _Gateway(400, '{"error":{"message":"bad parameter"}}'), monkeypatch
    )

    with pytest.raises(LLMRequestRejected) as raised:
        await service._call_llm(MESSAGES)

    assert not raised.value.fatal
    assert "bad parameter" in raised.value.message


@pytest.mark.asyncio
async def test_an_unreachable_gateway_is_fatal_after_the_retries(monkeypatch):
    gateway = _Gateway(exc=httpx.ConnectError("refused"))
    service = _service(gateway, monkeypatch)

    with pytest.raises(LLMUnreachable):
        await service._call_llm(MESSAGES)

    assert len(gateway.calls) == service.max_retries


# ------------------------------------------------------------ extraction


FIELD = {
    "id": "cclom:title",
    "label": {"de": "Titel"},
    "system": {"datatype": "string"},
}


@pytest.mark.asyncio
async def test_a_field_is_not_retried_on_top_of_the_transport_retries(monkeypatch):
    """_call_llm already retried the 5xx; extract_field must not multiply that."""
    gateway = _Gateway(500, "boom")
    service = _service(gateway, monkeypatch)

    field_id, value, error = await service.extract_field(FIELD, "Text")

    assert value is None and error
    assert len(gateway.calls) == service.max_retries


@pytest.mark.asyncio
async def test_parallel_extraction_stops_at_the_first_refused_key(monkeypatch):
    gateway = _Gateway(403, B_API_403_BODY)
    service = _service(gateway, monkeypatch)
    fields = [{**FIELD, "id": f"field{i}"} for i in range(50)]

    result = await service.extract_fields_parallel(fields, "Text", max_workers=5)

    assert isinstance(result["fatal_error"], LLMAuthForbidden)
    assert result["values"] == {}
    assert len(gateway.calls) <= 5  # at most the calls already in flight
    assert result["skipped"] >= 45


@pytest.mark.asyncio
async def test_parallel_extraction_reports_field_errors_without_stopping(monkeypatch):
    service = _service(_Gateway(500, "boom"), monkeypatch)
    fields = [{**FIELD, "id": f"field{i}"} for i in range(3)]

    result = await service.extract_fields_parallel(fields, "Text", max_workers=3)

    assert result["fatal_error"] is None
    assert len(result["errors"]) == 3
    assert isinstance(result["first_error"], LLMUpstreamError)


# ------------------------------------------------------------ content type


CONTENT_TYPES = [
    {"schema_file": "event.json", "label": {"de": "Veranstaltung"}},
    {"schema_file": "learning_material.json", "label": {"de": "Lernmaterial"}},
]


@pytest.mark.asyncio
async def test_content_type_detection_raises_on_a_refused_key(monkeypatch):
    service = _service(_Gateway(403, B_API_403_BODY), monkeypatch)

    with pytest.raises(LLMAuthForbidden):
        await service.detect_content_type("Konzert am 12. November", CONTENT_TYPES)


@pytest.mark.asyncio
async def test_content_type_detection_returns_a_known_answer(monkeypatch):
    service = _service(
        _Gateway(200, _answer('{"content_type": "event.json"}')), monkeypatch
    )

    detection = await service.detect_content_type(
        "Konzert am 12. November", CONTENT_TYPES
    )

    assert detection.schema_file == "event.json"
    assert detection.problem is None


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ['{"content_type": "recipe.json"}', "kein JSON"])
async def test_an_unusable_answer_is_reported_not_replaced(monkeypatch, content):
    service = _service(_Gateway(200, _answer(content)), monkeypatch)

    detection = await service.detect_content_type(
        "Konzert am 12. November", CONTENT_TYPES
    )

    assert detection.schema_file is None
    assert detection.problem
