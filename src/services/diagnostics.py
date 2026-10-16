"""
Can this instance reach what it depends on, and is its B-API key taken?

Behind GET /debug/dependencies, only with METADATA_AGENT_DEBUG_ENDPOINTS.
Written for what production showed on 2026-10-14: /health answered 200 while
every LLM call got a 403 and every URL extraction a connect timeout.

- llm: lists the models (GET <api_base>/models) with the key the LLM calls of
  this request would carry. That uses no tokens and is refused like a
  generation would be, so a key without the right shows up as 401/403.
- text_extraction, repository: one GET to the configured address. Any HTTP
  answer counts as reachable - the question is DNS, network and TLS, not a
  particular endpoint of the service.

Reports addresses, status, time and the kind of failure - never a key.
"""

import asyncio
import time
from typing import Any, Optional

import httpx

from ..config import Settings
from .llm_auth import b_api_auth
from .llm_errors import LLMError
from ..utils.request_id import TRACE_EVENT_HOOKS

# Short on purpose: a dependency that needs longer is a finding in itself, and
# the report waits for the slowest check.
PROBE_TIMEOUT = httpx.Timeout(5.0)


def _ms_since(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


async def _probe(
    client: httpx.AsyncClient, url: str, headers: Optional[dict[str, str]] = None
) -> tuple[dict[str, Any], Optional[httpx.Response]]:
    """One GET: the report entry, and the response if one came back."""
    start = time.monotonic()
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        return {
            "url": url,
            "reachable": False,
            "error": type(e).__name__,
            "ms": _ms_since(start),
        }, None
    return {
        "url": url,
        "reachable": True,
        "status": response.status_code,
        "ms": _ms_since(start),
    }, response


async def _check_reachable(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    return (await _probe(client, url))[0]


async def _check_llm(client: httpx.AsyncClient, settings: Settings) -> dict[str, Any]:
    if not settings.llm_provider.startswith("b-api"):
        return {
            "skipped": f"llm_provider={settings.llm_provider} ist kein B-API-Provider"
        }
    config = settings.get_llm_config()
    url = f"{config['api_base']}/models"
    model = config["model"]
    try:
        headers, auth_source = b_api_auth(settings)
    except LLMError as e:
        # No key at all: nothing to send, and nothing is sent.
        return {"url": url, "model": model, "error": e.code, "message": e.message}
    result, response = await _probe(client, url, headers)
    result = {**result, "model": model}
    if response is None:
        return result
    result["auth_source"] = auth_source
    result["authorized"] = response.status_code == 200
    # A route has to be listed by the router to be usable; provider/model works
    # without a route and is never listed.
    if (
        config["provider"] == "b-api-router"
        and "/" not in model
        and result["authorized"]
    ):
        result["route_listed"] = model in _listed_ids(response)
    return result


def _listed_ids(response: httpx.Response) -> set:
    try:
        data = response.json().get("data", [])
    except (ValueError, AttributeError):
        return set()
    return {entry.get("id") for entry in data if isinstance(entry, dict)}


async def check_dependencies(
    settings: Settings, client: Optional[httpx.AsyncClient] = None
) -> dict[str, Any]:
    """Run the three checks side by side and report them with the configuration."""
    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=PROBE_TIMEOUT, event_hooks=TRACE_EVENT_HOOKS
    )
    try:
        llm, text_extraction, repository = await asyncio.gather(
            _check_llm(client, settings),
            _check_reachable(client, settings.text_extraction_api_url),
            _check_reachable(client, settings.repository_url),
        )
    finally:
        if own_client:
            await client.aclose()
    return {
        "version": settings.app_version,
        "llm_provider": settings.llm_provider,
        "b_api_auth_mode": settings.b_api_auth_mode,
        "config_warnings": settings.config_warnings(),
        "checks": {
            "llm": llm,
            "text_extraction": text_extraction,
            "repository": repository,
        },
    }
