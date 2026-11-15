"""
The schema markers of a record are checked before anything is written.

Audit 13.11., API-13: /validate and /export refused `metadataset: 5` with 422
(API-07), the write paths did not. The schema helpers swallowed the error, the
node was written with the core fields only and sent to the review workflow -
and then the answer model failed: the client saw 500 INTERNAL_ERROR for a node
that existed, and trying again created a second one.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.repository_service import NodeWrite, with_resolved_schema
from src.services.repository_support import identifying_url

NODE_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
EVENT_URI = "http://w3id.org/openeduhub/vocabs/contentTypes/event"


class _MustNotWrite:
    """Any call into the repository fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"repository called: {name}")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(deps, "get_repository_service", lambda: _MustNotWrite())
    monkeypatch.setattr(deps.settings, "wlo_guest_username", "user")
    monkeypatch.setattr(deps.settings, "wlo_guest_password", "secret")
    with TestClient(main.app) as client:
        yield client


BAD_MARKERS = [
    {"metadataset": 5},
    {"metadataset": ["event.json"]},
    {"schemaVersion": 2},
    {"contextName": {"name": "default"}},
    {"_schema": "event.json"},
]


@pytest.mark.parametrize("marker", BAD_MARKERS)
@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_a_marker_that_is_no_text_is_refused_before_anything_is_written(
    client, path, marker
):
    record = {"cclom:title": "Titel", "metadataset": "event.json", **marker}

    nested = client.post(path, json={"metadata": record})
    flat = client.post(path, json=record)

    for response in (nested, flat):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("marker", BAD_MARKERS)
def test_verify_refuses_such_a_marker_too(client, marker):
    record = {"cclom:title": "Titel", **marker}

    response = client.post(
        f"/upload/verify/{NODE_ID}", json={"expected_metadata": record}
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"ccm:wwwurl": 123}, "123"),
        ({"ccm:wwwurl": ["https://example.org/a"]}, "https://example.org/a"),
        ({"schema:url": "https://example.org/b"}, "https://example.org/b"),
        ({"ccm:wwwurl": [""]}, None),
        ({}, None),
    ],
)
def test_the_identifying_url_is_text_or_nothing(metadata, expected):
    """A number there ended as a 500 in the answer model, after the write."""
    assert identifying_url(metadata) == expected


def test_a_content_type_uri_selects_the_schema_as_in_validate():
    job = NodeWrite(metadata={"cclom:title": "T", "metadataset": EVENT_URI})

    resolved = with_resolved_schema(job)

    assert resolved.metadata["metadataset"] == "event.json"
    # The caller's record is not changed.
    assert job.metadata["metadataset"] == EVENT_URI


@pytest.mark.parametrize(
    "metadataset", ["event.json", None, "http://example.org/kein/inhaltstyp"]
)
def test_anything_else_is_left_as_it_is(metadataset):
    job = NodeWrite(metadata={"cclom:title": "T", "metadataset": metadataset})

    assert with_resolved_schema(job) == replace(job)
