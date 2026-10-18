"""
/extract-field refuses native OpenAI on a B-API deployment before fetching.

The provider check ran only after resolve_input: the source was fetched (a
crawl, a repository read) for a request that was refused anyway. /generate
and /detect-content-type already refuse first (audit API-05).
"""

from fastapi.testclient import TestClient

from src import main
from src.api import deps


class _RecordingInput:
    def __init__(self):
        self.fetched = []

    async def fetch_from_url(self, url, method=None, **kwargs):
        self.fetched.append(url)
        return "Abgerufener Seitentext."


def test_native_openai_is_refused_before_the_source_is_fetched(monkeypatch):
    source = _RecordingInput()
    monkeypatch.setattr(deps, "get_input_source_service", lambda: source)
    monkeypatch.setattr(deps.settings, "llm_provider", "b-api-openai")
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/extract-field",
        json={
            "schema_file": "event.json",
            "field_id": "ccm:oeh_event_begin",
            "input_source": "url",
            "source_url": "https://example.org/kurs",
            "llm_provider": "openai",
        },
    )

    assert response.status_code == 422, response.text
    assert source.fetched == []
