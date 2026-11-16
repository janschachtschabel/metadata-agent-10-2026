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
- text_extraction and geocoder through the B-API (its proxy paths): asked with
  the same key, on an endpoint that answers 200 only to a key with the right
  for that proxy service - 401/403 shows up as authorized: false. Photon
  called directly is a third party and is not asked.

Reports addresses, status, time and the kind of failure - never a key.
"""

import asyncio
import time
from typing import Any, Optional

import httpx

from ..config import Settings
from .llm_auth import b_api_auth, b_api_headers_for
from .llm_errors import LLMError
from ..utils.request_id import TRACE_EVENT_HOOKS
from ..utils.tls_context import shared_tls_context
from .service_ways import REFUSALS

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
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        # InvalidURL is no HTTPError: a configured address that is no URL is a
        # finding of this report, not a 500 (audit 14.11., L-N).
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


async def _check_service(
    client: httpx.AsyncClient, settings: Settings, url: str, probe: str = ""
) -> dict[str, Any]:
    """
    A service that may sit behind the B-API: reachable, and is the key taken?

    `probe` is appended for the call through the B-API: its proxy answers 403
    to paths it does not know, so the bare address says nothing about the key.
    """
    if not url:
        return {"configured": False}
    try:
        headers = b_api_headers_for(url, settings)
    except LLMError as e:
        # No key at all: nothing to send, and nothing is sent.
        return {"url": url, "error": e.code, "message": e.message}
    if not headers:
        return await _check_reachable(client, url)
    result, response = await _probe(client, f"{url}{probe}", headers)
    result = {**result, "url": url}
    if response is not None:
        result["auth_source"] = b_api_auth(settings)[1]
        result["authorized"] = response.status_code == 200
    return result


async def _check_geocoder(
    client: httpx.AsyncClient, settings: Settings
) -> dict[str, Any]:
    if not settings.services_via_b_api:
        return {
            "url": settings.geocoder_api_url,
            "skipped": "Photon wird direkt aufgerufen (Fremddienst, nicht geprüft)",
        }
    return await _check_service(
        client, settings, settings.geocoder_api_url, "?q=Berlin&limit=1"
    )


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
    """Run the checks side by side and report them with the configuration."""
    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=PROBE_TIMEOUT,
        event_hooks=TRACE_EVENT_HOOKS,
        verify=shared_tls_context(),
    )
    try:
        llm, text_extraction, geocoder, repository = await asyncio.gather(
            _check_llm(client, settings),
            _check_service(
                client, settings, settings.text_extraction_api_url, "/metrics"
            ),
            _check_geocoder(client, settings),
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
        # Ways to text extraction and geocoder the B-API refused lately, with
        # the seconds until they are tried again (service_ways.py).
        "refused_ways": REFUSALS.snapshot(),
        "checks": {
            "llm": llm,
            "text_extraction": text_extraction,
            "geocoder": geocoder,
            "repository": repository,
        },
    }
