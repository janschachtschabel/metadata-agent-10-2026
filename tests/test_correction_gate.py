"""
A long text next to `_source_text`: a new source only where the text is the
source (audit 14.11., S-39 / K-07).

With `input_source=text` a text over MAX_CORRECTION_LENGTH is a new source
pasted over the old one (the 2.1.1 widget sends `_source_text` with every run).
With a URL or a node the source is fetched: there the text can only be the
correction instruction, and one that long is refused - it was dropped instead,
with the warning that it had been "extracted as a new source". The refusal
comes before the source is fetched; the check inside with_correction that no
request could reach is gone.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.api.input_resolver import MAX_CORRECTION_LENGTH

URL = "https://example.org/kurs"
LONG = "k" * (MAX_CORRECTION_LENGTH + 1)
PREVIOUS = {"_source_text": "Alte Quelle über Orgelmusik.", "cclom:title": "Kurs"}


class _Generator:
    """Stands in for MetadataService: records the text it is given."""

    llm_service = None

    def __init__(self):
        self.texts = []

    async def generate_metadata(self, **kwargs):
        self.texts.append(kwargs["text"])
        return {
            "contextName": "default",
            "schemaVersion": "2.0.0",
            "metadataset": "event.json",
            "language": "de",
            "exportedAt": "2026-11-14T00:00:00Z",
            "metadata": {"cclom:title": "Kurs"},
            "processing": {"status": "success", "success": True, "warnings": []},
        }


class _Page:
    def __init__(self):
        self.fetched = 0

    async def fetch_from_url(self, *args, **kwargs):
        self.fetched += 1
        return "Abgerufener Seitentext."


@pytest.fixture
def run(monkeypatch):
    generator, page = _Generator(), _Page()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: generator)
    monkeypatch.setattr(deps, "get_input_source_service", lambda: page)

    def post(**body):
        with TestClient(main.app) as client:
            return client.post("/generate", json=body)

    post.generator, post.page = generator, page
    return post


def test_with_a_url_an_instruction_over_the_limit_is_refused_before_the_fetch(run):
    response = run(
        input_source="url", source_url=URL, text=LONG, existing_metadata=PREVIOUS
    )

    assert response.status_code == 400, response.text
    assert "Korrekturanweisung zu lang" in response.json()["detail"]
    assert str(MAX_CORRECTION_LENGTH) in response.json()["detail"]
    assert run.page.fetched == 0
    assert run.generator.texts == []


def test_with_a_url_an_instruction_at_the_limit_is_a_correction(run):
    response = run(
        input_source="url",
        source_url=URL,
        text=LONG[:-1],
        existing_metadata=PREVIOUS,
    )

    assert response.status_code == 200, response.text
    (sent,) = run.generator.texts
    assert "Abgerufener Seitentext." in sent
    assert sent.endswith(LONG[:-1])
    assert not any(
        "neue Quelle" in w for w in response.json()["processing"]["warnings"]
    )


def test_with_a_url_and_no_earlier_round_nothing_claims_a_new_source(run):
    response = run(input_source="url", source_url=URL, text=LONG)

    assert response.status_code == 200, response.text
    assert LONG not in run.generator.texts[0]
    assert response.json()["processing"]["warnings"] == []


def test_as_text_input_the_long_text_is_still_a_new_source(run):
    response = run(text=LONG, existing_metadata=PREVIOUS)

    assert response.status_code == 200, response.text
    assert run.generator.texts == [LONG]
    assert any("neue Quelle" in w for w in response.json()["processing"]["warnings"])
