"""
/detect-content-type, /extract-field and /generate read their input alike.

The three endpoints accept the same input sources and resolved them in three
copies that drifted (audit A-01): the same failed fetch was a 400 in two of them
and a 502 in the third (API-01), and /generate dropped a correction instruction
whenever the text was fetched, or cut it off with a long text (L-02).
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.input_source_service import InputData, InputSourceService
from src.utils.public_url import PublicUrlError

NODE_ID = "11111111-2222-3333-4444-555555555555"
URL = "https://example.org/kurs"
INSTRUCTION = "Bitte das Datum auf den 3. Mai 2026 korrigieren."

ENDPOINTS = {
    "/detect-content-type": {},
    "/extract-field": {"schema_file": "event.json", "field_id": "ccm:oeh_event_begin"},
    "/generate": {},
}


class _Input:
    """Input source service; `fail` makes every fetch raise it."""

    def __init__(self, fail=None, text="Abgerufener Seitentext."):
        self.fail, self.text = fail, text

    async def _maybe_fail(self):
        if self.fail:
            raise self.fail

    async def fetch_from_url(self, url, method=None, **kwargs):
        await self._maybe_fail()
        return self.text

    async def fetch_from_node_id(self, node_id, **kwargs):
        await self._maybe_fail()
        return InputData(text=self.text, existing_metadata={}, node_id=node_id)

    async def fetch_from_node_url(self, node_id, **kwargs):
        await self._maybe_fail()
        return InputData(text=self.text, existing_metadata={}, node_id=node_id)


class _Generator:
    """Metadata service that records the text /generate hands it."""

    def __init__(self):
        self.texts = []

    async def generate_metadata(self, text, **kwargs):
        self.texts.append(text)
        return {
            "contextName": "default",
            "schemaVersion": "1.8.1",
            "metadataset": "event.json",
            "language": "de",
            "exportedAt": "2026-10-17T00:00:00Z",
            "metadata": {"cclom:title": "Kurs"},
            "processing": {"status": "success", "success": True, "warnings": []},
        }


@pytest.fixture
def generator(monkeypatch):
    service = _Generator()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    return service


# ------------------------------------------------------------------- API-01


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize(
    "source",
    [
        {"input_source": "url", "source_url": URL},
        {"input_source": "node_id", "node_id": NODE_ID},
        {"input_source": "node_url", "node_id": NODE_ID},
    ],
)
def test_a_source_that_cannot_be_read_is_a_502_everywhere(monkeypatch, path, source):
    monkeypatch.setattr(
        deps, "get_input_source_service", lambda: _Input(RuntimeError("down"))
    )

    with TestClient(main.app) as client:
        response = client.post(path, json={**ENDPOINTS[path], **source})

    assert response.status_code == 502, response.text


@pytest.mark.parametrize("path", ENDPOINTS)
def test_a_url_that_is_not_public_is_the_callers_error(monkeypatch, path):
    monkeypatch.setattr(
        deps,
        "get_input_source_service",
        lambda: _Input(PublicUrlError("URL zeigt auf eine interne Adresse")),
    )

    with TestClient(main.app) as client:
        response = client.post(
            path, json={**ENDPOINTS[path], "input_source": "url", "source_url": URL}
        )

    assert response.status_code == 400, response.text


def _node_url_service(monkeypatch, wwwurl):
    """The real service: node without fulltext, every URL fetch refused as internal."""
    service = InputSourceService()

    async def metadata(*args, **kwargs):
        return {"properties": {"cclom:title": ["Kurs"], "ccm:wwwurl": [wwwurl]}}

    async def no_fulltext(*args, **kwargs):
        return None

    async def internal(url, *args, **kwargs):
        raise PublicUrlError("URL zeigt auf eine interne Adresse")

    monkeypatch.setattr(service, "fetch_node_metadata", metadata)
    monkeypatch.setattr(service, "fetch_node_text_content", no_fulltext)
    monkeypatch.setattr(service, "fetch_from_url", internal)
    monkeypatch.setattr(deps, "get_input_source_service", lambda: service)
    return service


@pytest.mark.parametrize("path", ENDPOINTS)
def test_a_node_url_source_url_that_is_not_public_is_the_callers_error(
    monkeypatch, path
):
    """Review #12: the CHANGELOG promised 400; the service fell back instead."""
    _node_url_service(monkeypatch, URL)

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={
                **ENDPOINTS[path],
                "input_source": "node_url",
                "node_id": NODE_ID,
                "source_url": "http://10.0.0.1/intern",
            },
        )

    assert response.status_code == 400, response.text
    assert "interne Adresse" in response.json()["detail"]


def test_with_stored_fulltext_the_source_url_is_not_fetched(monkeypatch, generator):
    # The CHANGELOG's 400 applies only without fulltext: the URL is a fallback.
    service = _node_url_service(monkeypatch, URL)

    async def fulltext(*args, **kwargs):
        return "Gespeicherter Volltext."

    monkeypatch.setattr(service, "fetch_node_text_content", fulltext)

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "input_source": "node_url",
                "node_id": NODE_ID,
                "source_url": "http://10.0.0.1/intern",
            },
        )

    assert response.status_code == 200, response.text
    assert "Gespeicherter Volltext." in generator.texts[0]


def test_a_non_public_url_stored_on_the_node_falls_back_to_the_node(
    monkeypatch, generator
):
    # Not the caller's fault: the URL comes from the repository (ccm:wwwurl).
    _node_url_service(monkeypatch, "http://10.0.0.1/intern")

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={"input_source": "node_url", "node_id": NODE_ID},
        )

    assert response.status_code == 200, response.text
    assert generator.texts[0].endswith("Kurs")
    warnings = response.json()["processing"]["warnings"]
    assert any("Titel, Beschreibung und Schlagworten" in w for w in warnings)


# ------------------------------------------------------------------- L-02


def test_a_correction_survives_a_fetched_source(monkeypatch, generator):
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Input())

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "input_source": "url",
                "source_url": URL,
                "text": INSTRUCTION,
                "existing_metadata": {
                    "_source_text": "Seitentext aus Runde 1.",
                    "cclom:title": "Kurs",
                },
            },
        )

    assert response.status_code == 200, response.text
    sent = generator.texts[0]
    assert "Abgerufener Seitentext." in sent
    assert sent.rstrip().endswith(INSTRUCTION)


def test_a_long_source_keeps_the_correction(generator):
    source = "Langer Quelltext. " * 40_000  # 720,000 characters

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": INSTRUCTION,
                "existing_metadata": {"_source_text": source, "cclom:title": "Kurs"},
            },
        )

    assert response.status_code == 200, response.text
    sent = generator.texts[0]
    # Text budget 150,000 since 19.10. (was 500,000; 200,000 measured slower).
    assert len(sent) <= 150_000
    assert sent.rstrip().endswith(INSTRUCTION)
    warnings = response.json()["processing"]["warnings"]
    assert any("(Original: 720.000 Zeichen)" in w for w in warnings), warnings


# ---------------------------- long sources (19.10.): 150,000 characters, warned


def _truncation_warnings(response) -> list[str]:
    return [
        w
        for w in response.json()["processing"]["warnings"]
        if w.startswith("Quelltext auf")
    ]


def test_a_long_source_is_cut_to_the_budget_and_the_client_is_told(generator):
    # The Goethe article: 412k characters took 56 LLM calls and 126 s; cut to
    # 200k 59 s, to 150k 35-42 s with the same fields (measured 19.10.).
    source = "x" * 250_000

    with TestClient(main.app) as client:
        response = client.post("/generate", json={"text": source})

    assert response.status_code == 200, response.text
    assert len(generator.texts[0]) == 150_000
    assert _truncation_warnings(response) == [
        "Quelltext auf 150.000 Zeichen gekürzt (Original: 250.000 Zeichen)."
    ]


def test_a_source_within_the_budget_is_not_cut_and_not_warned(generator):
    source = "x" * 150_000

    with TestClient(main.app) as client:
        response = client.post("/generate", json={"text": source})

    assert response.status_code == 200, response.text
    assert generator.texts[0] == source
    assert _truncation_warnings(response) == []


def test_a_long_new_text_over_an_old_source_is_cut_like_any_source(generator):
    # Over MAX_CORRECTION_LENGTH with _source_text: a new source (final audit
    # 18.10.), and as such subject to the text budget.
    new_text = "y" * 300_000

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": new_text,
                "existing_metadata": {"_source_text": "Alte Quelle."},
            },
        )

    assert response.status_code == 200, response.text
    assert generator.texts[0] == "y" * 150_000
    warnings = response.json()["processing"]["warnings"]
    assert any("neue Quelle" in w for w in warnings), warnings
    assert _truncation_warnings(response) == [
        "Quelltext auf 150.000 Zeichen gekürzt (Original: 300.000 Zeichen)."
    ]


def test_the_returned_source_text_is_the_source_alone(generator):
    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": INSTRUCTION,
                "existing_metadata": {
                    "_source_text": "Originaltext.",
                    "cclom:title": "Kurs",
                },
            },
        )

    # The widget keeps it for the next round and uploads it as
    # ccm:oeh_extendedText: no instruction, no copy of the values. The values
    # already carry the correction; repeating "shorter" would apply it twice.
    assert response.json()["_source_text"] == "Originaltext."


# ------------------------------------------------- review 18.10.: cheap refusals


@pytest.mark.parametrize("path", ["/detect-content-type", "/generate"])
def test_a_refused_provider_is_refused_before_the_source_is_fetched(monkeypatch, path):
    fetched = []

    class _Recording(_Input):
        async def fetch_from_url(self, url, method=None, **kwargs):
            fetched.append(url)
            return self.text

    monkeypatch.setattr(deps.settings, "llm_provider", "b-api-openai")
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Recording())

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={"input_source": "url", "source_url": URL, "llm_provider": "openai"},
        )

    assert response.status_code == 422, response.text
    assert fetched == []


# ------------------------------------------------- audit 13.11.2026, API-14


def _repository_answers(status):
    request = httpx.Request("GET", "https://repo.example/node")
    return httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(status, request=request)
    )


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("status", [403, 404])
def test_a_node_the_repository_does_not_have_is_a_404(monkeypatch, path, status):
    """
    A typo in node_id looked like a repository outage (502 "Failed to fetch
    input: HTTPStatusError") and counted towards the 5xx alert; /nodes,
    /workflow and /upload/verify answered 404 for the same node.
    """
    source = _Input(fail=_repository_answers(status))
    monkeypatch.setattr(deps, "get_input_source_service", lambda: source)

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={**ENDPOINTS[path], "input_source": "node_id", "node_id": NODE_ID},
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_a_repository_that_fails_stays_a_502(monkeypatch, path):
    source = _Input(fail=_repository_answers(500))
    monkeypatch.setattr(deps, "get_input_source_service", lambda: source)

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={**ENDPOINTS[path], "input_source": "node_id", "node_id": NODE_ID},
        )

    assert response.status_code == 502


@pytest.mark.parametrize("path", ENDPOINTS)
def test_a_model_that_is_not_released_is_refused_before_the_source_is_fetched(
    monkeypatch, path
):
    """A model outside the allow list cost a page fetch before it was refused."""
    fetched = []

    class _Counting(_Input):
        async def fetch_from_url(self, url, method=None, **kwargs):
            fetched.append(url)
            return await super().fetch_from_url(url, method, **kwargs)

    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Counting())
    monkeypatch.setattr(deps.settings, "allowed_llm_models", "gpt-6-luna")

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={
                **ENDPOINTS[path],
                "input_source": "url",
                "source_url": URL,
                "llm_model": "gpt-teuer",
            },
        )

    assert response.status_code == 422, response.text
    assert fetched == []
