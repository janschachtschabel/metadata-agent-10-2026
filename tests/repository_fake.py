"""
A recording stand-in for httpx.AsyncClient, answering like edu-sharing.

Shared by the write-path tests of the audit round of 14.11.: nothing here can
reach the repository, so they run the real RepositoryService against this.
Subclasses change single answers in `answer`.
"""

import json as json_module
from datetime import datetime, timezone
from typing import Any

from src.services import repository_service as repo_module

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"


class Response:
    def __init__(self, status_code: int = 200, payload: Any = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json_module.dumps(self._payload)

    def json(self):
        return self._payload


def fresh_node(**properties) -> dict:
    """A node as a read returns it right after its creation."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ref": {"id": NODE_ID},
        "createdAt": now,
        "modifiedAt": now,
        "aspects": [],
        "properties": properties,
    }


class FakeRepository:
    # Every request: {"method", "url", "json"}.
    calls: list[dict] = []
    # Answer of the search the duplicate check runs.
    search_hits: list[dict] = []
    # The newest children of the inbox, as the direct read returns them.
    inbox: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, files=None):
        return await self._request("POST", url, json)

    async def put(self, url, headers=None, json=None):
        return await self._request("PUT", url, json)

    async def get(self, url, headers=None):
        return await self._request("GET", url, None)

    async def delete(self, url, headers=None):
        return await self._request("DELETE", url, None)

    async def _request(self, method, url, body):
        type(self).calls.append({"method": method, "url": url, "json": body})
        return await self.answer(method, url, body)

    async def answer(self, method, url, body) -> Response:
        if method == "POST" and "/children?" in url:
            return Response(200, {"node": {"ref": {"id": NODE_ID}}})
        if method == "POST" and "ngsearch" in url:
            return Response(200, {"nodes": type(self).search_hits})
        if method == "GET" and "/children?" in url:
            return Response(200, {"nodes": type(self).inbox})
        if method == "GET":
            return Response(200, {"node": fresh_node()})
        return Response(200, {})


def install(monkeypatch, client_class=FakeRepository, search_hits=(), inbox=()):
    """Put the fake in place of httpx.AsyncClient; returns its list of calls."""
    client_class.calls = []
    client_class.search_hits = list(search_hits)
    client_class.inbox = list(inbox)
    monkeypatch.setattr(repo_module.httpx, "AsyncClient", client_class)
    monkeypatch.setattr(repo_module.httpx, "Timeout", lambda *a, **k: None)
    return client_class.calls


def requests(calls, method, part="") -> list[dict]:
    """The recorded calls of one method whose URL contains `part`."""
    return [c for c in calls if c["method"] == method and part in c["url"]]
