"""
Repository endpoints answer with the status that names who failed (audit API-01).

A failing repository is a 502 (bad gateway), a node that does not exist a 404
- as on /nodes - and a request the agent cannot accept a 4xx. /upload/verify
answered "node not found" with 502, /node a failing repository with 400 - the
caller retried what it should have fixed, and fixed what it should have retried.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import repository_review as review_module
from src.services.repository_service import RepositoryService

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"


class _Repository:
    def __init__(self, verify=None, create=None):
        self._verify, self._create = verify, create

    async def verify_node(self, **kwargs):
        return self._verify

    async def create_node(self, **kwargs):
        return self._create


def _client(monkeypatch, repository):
    monkeypatch.setattr(deps, "get_repository_service", lambda: repository)
    return TestClient(main.app)


# ----------------------------------------------------------- /upload/verify


@pytest.mark.parametrize(
    "result, status",
    [
        ({"success": False, "error_code": "node_not_found", "error": "weg"}, 404),
        ({"success": False, "error": "Failed to fetch node: HTTP 500"}, 502),
    ],
)
def test_verify_tells_a_missing_node_from_a_failing_repository(
    monkeypatch, result, status
):
    with _client(monkeypatch, _Repository(verify=result)) as client:
        response = client.post(f"/upload/verify/{NODE_ID}")

    assert response.status_code == status, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_status, code", [(404, "node_not_found"), (500, None)])
async def test_verify_node_names_a_missing_node(monkeypatch, repo_status, code):
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(repo_status, text="nope")

    monkeypatch.setattr(
        review_module.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    result = await RepositoryService("user", "password").verify_node(NODE_ID)

    assert result["success"] is False
    assert result.get("error_code") == code


# --------------------------------------------------------------------- /node


def test_a_failing_repository_is_a_502_when_creating_a_node(monkeypatch):
    failed = {"success": False, "error": "HTTP 500 beim Anlegen"}

    with _client(monkeypatch, _Repository(create=failed)) as client:
        response = client.post("/node", json={"metadata": {"cclom:title": "Titel"}})

    assert response.status_code == 502, response.text


def test_a_missing_title_stays_the_callers_error(monkeypatch):
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("user", "password")
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/node", json={"metadata": {"ccm:wwwurl": "https://e.org"}}
        )

    assert response.status_code == 400
