"""
The clients for the repository and the text extraction verify against the one
TLS context of the process (audit 14.11.2026, L-41 / P-13).

The write path, the checks around it and the preview fetch each open an HTTP
client per call. httpx built a TLS context for every one of them - loading the
certificate bundle, about 400 ms on the measured machine, on the event loop.
They now pass utils/tls_context.shared_tls_context(), as the LLM and geocoder
clients do. Nothing else about the clients changes: the same verification
(httpx's default context), the same timeouts, the same hooks.
"""

import ssl
from datetime import datetime, timezone

import httpx
import pytest
from repository_fake import NODE_ID, FakeRepository

from src.metrics import OWN_SERVICE_HOOKS, THIRD_PARTY_HOOKS
from src.services import input_source_service as input_module
from src.services import repository_service as repo_module
from src.services.repository_nodes import DISCARD_TIMEOUT_SECONDS
from src.services.repository_service import PreviewError, RepositoryService
from src.utils.tls_context import shared_tls_context

BASE = "https://repository.example/edu-sharing"
RECORD = {"cclom:title": "Titel", "ccm:wwwurl": "https://example.org/seite"}


@pytest.fixture
def clients(monkeypatch):
    """The keyword arguments of every client the code under test opens."""
    opened: list[dict] = []
    FakeRepository.calls, FakeRepository.search_hits, FakeRepository.inbox = [], [], []

    def client(*args, **kwargs):
        assert not args
        opened.append(kwargs)
        return FakeRepository()

    monkeypatch.setattr(repo_module.httpx, "AsyncClient", client)
    return opened


def _service() -> RepositoryService:
    return RepositoryService("konto", "passwort")


async def _create(service):
    await service.create_node(RECORD)


async def _save(service):
    await service.save_node(RECORD)


async def _preview_refusal(service):
    await service.preview_refusal(NODE_ID)


async def _upload_preview(service):
    await service.upload_preview(NODE_ID, b"kein Bild", "image/png")


async def _fetch_preview(service):
    with pytest.raises(PreviewError):
        await service._fetched_preview("")()


async def _verify(service):
    await service.verify_node(NODE_ID)


async def _workflow(service):
    await service.set_workflow(NODE_ID, ["200_tocheck"])


async def _discard(service):
    await service._discard_node(BASE, NODE_ID)


async def _find_lost(service):
    await service._find_lost_node(BASE, NODE_ID, RECORD, datetime.now(timezone.utc))


LONG, SHORT = (45.0, 10.0), (30.0, 10.0)
DISCARD = (DISCARD_TIMEOUT_SECONDS, DISCARD_TIMEOUT_SECONDS)

# call, (read timeout, connect timeout) of its client, hooks of its client
PER_CALL = [
    (_create, LONG, OWN_SERVICE_HOOKS),
    (_save, LONG, OWN_SERVICE_HOOKS),
    (_preview_refusal, LONG, OWN_SERVICE_HOOKS),
    (_upload_preview, LONG, OWN_SERVICE_HOOKS),
    (_fetch_preview, SHORT, THIRD_PARTY_HOOKS),
    (_verify, SHORT, OWN_SERVICE_HOOKS),
    (_workflow, LONG, OWN_SERVICE_HOOKS),
    (_discard, DISCARD, OWN_SERVICE_HOOKS),
    (_find_lost, DISCARD, OWN_SERVICE_HOOKS),
]


@pytest.mark.parametrize(
    "call, timeout, hooks", PER_CALL, ids=[c[0].__name__ for c in PER_CALL]
)
async def test_a_client_opened_per_call_uses_the_shared_context(
    clients, call, timeout, hooks
):
    await call(_service())

    assert clients, "the call opened no client"
    for kwargs in clients:
        assert kwargs.get("verify") is shared_tls_context()
        # As before: nothing but the context is new.
        assert set(kwargs) == {"timeout", "event_hooks", "verify"}
        assert (kwargs["timeout"].read, kwargs["timeout"].connect) == timeout
        assert kwargs["event_hooks"] is hooks


def test_the_client_of_the_input_service_uses_the_shared_context(monkeypatch):
    opened: list[dict] = []
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        opened.append(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(input_module.httpx, "AsyncClient", client)

    service = input_module.InputSourceService()

    (kwargs,) = opened
    assert kwargs.get("verify") is shared_tls_context()
    assert (service.timeout.read, service.timeout.connect) == SHORT
    assert kwargs["event_hooks"] is OWN_SERVICE_HOOKS


async def test_three_calls_build_one_context(monkeypatch):
    """Real clients, answered without a network: how often is a context built?"""
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
    service = _service()

    for _ in range(3):
        refusal = await service.preview_refusal(NODE_ID)
        assert refusal["error_code"] == "node_not_found"

    assert len(built) == 1


def test_the_shared_context_verifies_as_the_default_of_httpx_does():
    shared, default = shared_tls_context(), httpx.create_ssl_context()

    assert shared.verify_mode == default.verify_mode == ssl.CERT_REQUIRED
    assert shared.check_hostname is default.check_hostname is True
    assert shared.minimum_version == default.minimum_version
