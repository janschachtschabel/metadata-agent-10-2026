"""
The client of /debug/dependencies verifies against the one TLS context of the
process (audit 14.11.2026, a small item of section Q; the pattern of L-41 / P-13).

check_dependencies opens an HTTP client per call. httpx built a TLS context
for it every time - loading the certificate bundle on the event loop - where
the repository, LLM and geocoder clients pass utils/tls_context
.shared_tls_context(). Nothing else about the client changes: the same probe
timeout, the same trace hooks; a client handed in is used as it is.
"""

import ssl

import httpx

from src.config import Settings
from src.services import diagnostics
from src.utils.request_id import TRACE_EVENT_HOOKS
from src.utils.tls_context import shared_tls_context


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        b_api_key="own-key",
        repository_url="https://repository.example/edu-sharing",
    )


def _answering(opened: list[dict]):
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        assert not args
        opened.append(kwargs)
        return real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(404)),
            timeout=kwargs["timeout"],
        )

    return client


async def test_the_client_opened_per_call_uses_the_shared_context(monkeypatch):
    opened: list[dict] = []
    monkeypatch.setattr(diagnostics.httpx, "AsyncClient", _answering(opened))

    report = await diagnostics.check_dependencies(_settings())

    assert report["checks"]["repository"]["reachable"] is True
    (kwargs,) = opened
    assert kwargs.get("verify") is shared_tls_context()
    # As before: nothing but the context is new.
    assert set(kwargs) == {"timeout", "event_hooks", "verify"}
    assert kwargs["timeout"] is diagnostics.PROBE_TIMEOUT
    assert kwargs["event_hooks"] is TRACE_EVENT_HOOKS


async def test_a_client_handed_in_is_used_and_left_open(monkeypatch):
    opened: list[dict] = []
    seen = []

    def answer(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": []})

    own = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    monkeypatch.setattr(diagnostics.httpx, "AsyncClient", _answering(opened))

    await diagnostics.check_dependencies(_settings(), own)

    assert opened == []
    assert seen
    assert not own.is_closed
    await own.aclose()


async def test_three_reports_build_one_context(monkeypatch):
    built = []
    real_context = ssl.create_default_context

    def counted(*args, **kwargs):
        built.append(kwargs)
        return real_context(*args, **kwargs)

    async def not_found(self, request, **kwargs):
        return httpx.Response(404, json={}, request=request)

    monkeypatch.setattr(ssl, "create_default_context", counted)
    monkeypatch.setattr(httpx.AsyncClient, "send", not_found)
    shared_tls_context.cache_clear()

    for _ in range(3):
        report = await diagnostics.check_dependencies(_settings())
        assert report["checks"]["repository"]["status"] == 404

    assert len(built) == 1
