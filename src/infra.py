"""
Liveness, readiness and the internal port.

Team 6 separates the public API from infrastructure endpoints: /health,
/ready and /metrics belong on a second port that the ingress does not expose.
With METADATA_AGENT_INTERNAL_PORT set, the agent starts that second server in
the same process; the public port keeps /health and /ready (both hidden from
the OpenAPI spec) so existing probes and the Docker HEALTHCHECK keep working.
/metrics is served on the internal port only.
"""

import asyncio
import contextlib
import logging
import os
import socket
from typing import Awaitable, Callable

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings
from .metrics import metrics_response, precreate_alert_series
from .utils.request_id import current_request_id
from .utils.security_headers import ScreenshotBrowserRefusalMiddleware
from .utils.schema_loader import get_available_contexts

logger = logging.getLogger(__name__)


def health_payload(settings: Settings, detailed: bool = True) -> dict:
    """
    Liveness: the process answers. Says nothing about the dependencies.

    Provider and auth mode only on the internal port (detailed): the public
    one answers anyone who reaches it (audit S-08).
    """
    payload = {"status": "healthy", "version": settings.app_version}
    if detailed:
        payload["llm_provider"] = settings.llm_provider
        payload["b_api_auth_mode"] = settings.b_api_auth_mode
    return payload


def readiness_problems(settings: Settings) -> list[str]:
    """
    What keeps this instance from doing its work at all - configuration only.

    External services are deliberately not called: a B-API outage would make
    every pod unready at once, and the ingress would answer for them.

    B_API_KEY missing in mode auto is no problem here: behind the B-API proxy
    every call carries the caller's forwarded credential (startup warning).
    """
    problems = []
    if not get_available_contexts():
        problems.append("Keine Schema-Kontexte geladen")
    if settings.llm_provider.startswith("b-api"):
        if settings.b_api_auth_mode == "static" and not settings.b_api_key:
            problems.append("B_API_KEY fehlt (B_API_AUTH_MODE=static)")
    elif not settings.openai_api_key:
        problems.append("OPENAI_API_KEY fehlt (llm_provider=openai)")
    # Audit OPS-12: the chart marks every secret key optional, so a typo in
    # secret.existingSecret started a "ready" pod whose /upload and /nodes
    # answer 503. A deployment that only generates switches this off
    # (METADATA_AGENT_REQUIRE_REPOSITORY=false; then a start warning).
    if settings.require_repository:
        for name, value in (
            ("WLO_GUEST_USERNAME", settings.wlo_guest_username),
            ("WLO_GUEST_PASSWORD", settings.wlo_guest_password),
        ):
            if not value:
                problems.append(f"{name} fehlt (Repository: /upload, /nodes)")
    return problems


_NOT_READY = (
    "Dienst nicht bereit: Die Konfiguration ist unvollständig (Gründe im Log und "
    "auf dem internen Port)."
)

# The problems last written to the log: the probe asks every few seconds.
_logged_problems: list[str] = []


def readiness_response(settings: Settings, detailed: bool = True) -> JSONResponse:
    """
    200 ready, or 503 - with the reasons only on the internal port (detailed):
    the public one answers anyone who reaches it and names no setting (S-08).
    """
    problems = readiness_problems(settings)
    if problems != _logged_problems:
        if problems:
            logger.warning("Nicht bereit: %s", "; ".join(problems))
        _logged_problems[:] = problems
    if problems:
        # The API's error shape, as every other error (audit 18.10. final).
        content: dict = {
            "status": "not_ready",
            "detail": _NOT_READY,
            "error": {
                "code": "NOT_READY",
                "message": _NOT_READY,
                "request_id": current_request_id(),
            },
        }
        if detailed:
            content["problems"] = problems
        return JSONResponse(status_code=503, content=content)
    return JSONResponse(content={"status": "ready"})


def create_internal_app(get_settings: Callable[[], Settings]) -> FastAPI:
    """The app of the internal port: health, ready, metrics - no API spec."""
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    # On loopback no NetworkPolicy applies: a page being captured must not read
    # /metrics or /ready through the screenshot browser (audit 13.11., S-23).
    app.add_middleware(ScreenshotBrowserRefusalMiddleware)
    # Before the first scrape, so the critical alerts see the first failure.
    precreate_alert_series(get_settings())

    @app.get("/health")
    async def health():
        return health_payload(get_settings())

    @app.get("/ready")
    async def ready():
        return readiness_response(get_settings())

    @app.get("/metrics")
    async def metrics():
        return metrics_response()

    return app


class InternalPortUnavailable(RuntimeError):
    """The internal port cannot be bound - taken, or not allowed."""


def _bind(host: str, port: int) -> socket.socket:
    """
    Bind the internal port here instead of in uvicorn.

    uvicorn answers a failed bind with sys.exit(1), which ends the whole process
    and the public API with it. Bound here, a taken port is an exception the
    lifespan can report.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = None
    try:
        # Inside the try: on a host without IPv6 "::" already fails here
        # (audit 14.11., O-37).
        sock = socket.socket(family, socket.SOCK_STREAM)
        if os.name != "nt":
            # A restart must not wait for TIME_WAIT. On Windows the option would
            # let a second process bind the same port, hiding the conflict.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as e:
        if sock is not None:
            sock.close()
        raise InternalPortUnavailable(
            f"Interner Port {port} auf {host} ist belegt oder nicht verfügbar "
            f"({e.strerror or e}). METADATA_AGENT_INTERNAL_PORT braucht einen "
            "freien Port und genau einen Worker je Container, "
            "METADATA_AGENT_INTERNAL_HOST eine Adresse, die der Host kennt."
        ) from e
    return sock


class _InternalServer(uvicorn.Server):
    """
    A second uvicorn server in the running process.

    It leaves the signals to the main server - capture_signals would replace
    the main server's handlers - and is stopped by the main app's lifespan.
    """

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def start_internal_server(
    app: FastAPI, port: int, host: str = "0.0.0.0"
) -> Callable[[], Awaitable[None]]:
    """
    Start serving `app` on `port`; returns the coroutine function that stops it.

    Raises:
        InternalPortUnavailable: the port could not be bound.
    """
    sock = _bind(host, port)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,  # keep the agent's logging setup
        access_log=False,  # probes every few seconds would flood the log
        lifespan="off",
        ws="none",  # no websockets here
    )
    server = _InternalServer(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))

    async def stop() -> None:
        server.should_exit = True
        await task

    return stop
