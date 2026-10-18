"""
One way to put a preview image on a node (audit S-02, D-03, S-07).

- /screenshot with node_id used to overwrite any node's preview, also of
  published nodes: the editing window that /upload and /nodes enforce did not
  apply. RepositoryService.upload_preview checks it first.
- /upload set the preview only after the workflow, so the node entered the
  editorial queue without a picture. The capture is now awaited and stored
  before the workflow runs.
- A preview fetched from a third-party URL must not carry the tracing headers
  meant for the agent's own services.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import repository_service as repo_module
from src.services.repository_service import RepositoryService
from src.utils.request_id import traced

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Repository:
    """A fake edu-sharing: answers by URL, records every request in order."""

    def __init__(self, created: datetime):
        self.created = created
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "GET" and "/metadata" in url:
            return httpx.Response(
                200,
                json={
                    "node": {
                        "properties": {},
                        "aspects": [],
                        "createdAt": self.created.isoformat(),
                    }
                },
            )
        if "/children?" in url:
            return httpx.Response(200, json={"node": {"ref": {"id": NODE_ID}}})
        if request.url.host == "93.184.216.34":
            # A third-party image host; only a real image passes (audit S-10).
            return httpx.Response(200, content=PNG)
        return httpx.Response(200, json={})

    def urls(self, method=None):
        return [str(r.url) for r in self.requests if method in (None, r.method)]


@pytest.fixture
def repository(monkeypatch):
    fake = _Repository(created=datetime.now(timezone.utc))
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(fake.handler), **kwargs)

    monkeypatch.setattr(repo_module.httpx, "AsyncClient", client)
    return fake


async def test_a_node_outside_its_window_keeps_its_preview(repository):
    repository.created = datetime.now(timezone.utc) - timedelta(hours=3)

    result = await RepositoryService("u", "p").upload_preview(NODE_ID, PNG, "image/png")

    assert result["success"] is False
    assert result["error_code"] == "node_too_old"
    assert not [u for u in repository.urls("POST") if "/preview" in u]


async def test_a_node_inside_its_window_gets_the_preview(repository):
    result = await RepositoryService("u", "p").upload_preview(NODE_ID, PNG, "image/png")

    assert result["success"] is True
    posts = [u for u in repository.urls("POST") if f"/{NODE_ID}/preview" in u]
    assert len(posts) == 1 and "mimetype=image%2Fpng" in posts[0]


async def _upload(preview_source):
    return await RepositoryService("u", "p").upload_metadata(
        metadata={
            "cclom:title": "Bruchrechnung",
            "metadataset": "learning_material.json",
        },
        check_duplicates=False,
        write_extended_data=False,
        preview_source=preview_source,
    )


async def test_upload_stores_the_preview_before_the_workflow(repository):
    async def capture():
        return PNG, "image/png"

    result = await _upload(capture)

    urls = repository.urls()  # the workflow is a PUT, the preview a POST
    preview_at = next(
        i for i, u in enumerate(urls) if u.endswith("&createVersion=true")
    )
    workflow_at = next(i for i, u in enumerate(urls) if u.endswith("/workflow"))
    assert preview_at < workflow_at
    assert result["preview"]["success"] is True
    # The image itself travels in the multipart part 'image', not just its type.
    body = repository.requests[preview_at].read()
    assert b'name="image"' in body
    assert PNG in body


async def test_a_failed_capture_does_not_stop_the_upload(repository):
    async def capture():
        raise RuntimeError("browser crashed")

    result = await _upload(capture)

    assert result["success"] is True
    # The type, not the text: an exception text can name internal hosts.
    assert result["preview"] == {
        "success": False,
        "error": "Vorschaubild nicht verfügbar (RuntimeError)",
    }
    assert any(u.endswith("/workflow") for u in repository.urls("PUT"))


async def test_a_third_party_preview_gets_no_tracing_headers(repository):
    service = RepositoryService("u", "p")
    with traced({"x-client-trace-id": "trace-1"}):
        async with repo_module.httpx.AsyncClient(
            event_hooks=repo_module.OWN_SERVICE_HOOKS
        ) as client:
            # The path /nodes takes for its `preview` parameter.
            result = await service._preview_from_source(
                client,
                "https://repo.example/edu-sharing",
                NODE_ID,
                service._fetched_preview("http://93.184.216.34/image.png"),
            )

    by_host = {
        r.url.host: r.headers.get("x-client-trace-id") for r in repository.requests
    }
    assert by_host["93.184.216.34"] is None
    assert by_host["repo.example"] == "trace-1"
    assert result["success"] is True


def test_screenshot_answers_403_for_a_node_outside_its_window(monkeypatch):
    shot = SimpleNamespace(
        image_bytes=PNG,
        mimetype="image/png",
        format="png",
        width=1,
        height=1,
        size_bytes=len(PNG),
        capture_time_ms=1,
        method="playwright",
        url="https://example.org/",
    )

    async def capture(**kwargs):
        return shot

    async def preview_refusal(node_id):
        return {"success": False, "error_code": "node_too_old", "error": "zu alt"}

    monkeypatch.setattr(
        deps, "get_screenshot_service", lambda: SimpleNamespace(capture=capture)
    )
    monkeypatch.setattr(
        deps,
        "get_repository_service",
        lambda: SimpleNamespace(preview_refusal=preview_refusal),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/screenshot", json={"url": "https://example.org/", "node_id": NODE_ID}
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "zu alt"


def test_the_preview_fetch_names_the_current_version():
    from src.config import Settings

    version = Settings(_env_file=None).app_version
    assert f"MetadataAgentAPI/{version} " in repo_module.PREVIEW_USER_AGENT
