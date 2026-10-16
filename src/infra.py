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
import os
import socket
from typing import Awaitable, Callable

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings
from .metrics import metrics_response
from .utils.schema_loader import get_available_contexts


def health_payload(settings: Settings) -> dict:
    """Liveness: the process answers. Says nothing about the dependencies."""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "llm_provider": settings.llm_provider,
        "b_api_auth_mode": settings.b_api_auth_mode,
    }


def readiness_problems(settings: Settings) -> list[str]:
    """
    What keeps this instance from doing its work at all - configuration only.

    External services are deliberately not called: a B-API outage would make
    every pod unready at once, and the ingress would answer for them.
    """
    problems = []
    if not get_available_contexts():
        problems.append("Keine Schema-Kontexte geladen")
    if settings.llm_provider.startswith("b-api"):
        if settings.b_api_auth_mode == "static" and not settings.b_api_key:
            problems.append("B_API_KEY fehlt (B_API_AUTH_MODE=static)")
    elif not settings.openai_api_key:
        problems.append("OPENAI_API_KEY fehlt (llm_provider=openai)")
    return problems


def readiness_response(settings: Settings) -> JSONResponse:
    problems = readiness_problems(settings)
    if problems:
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "problems": problems}
        )
    return JSONResponse(content={"status": "ready"})


def create_internal_app(get_settings: Callable[[], Settings]) -> FastAPI:
    """The app of the internal port: health, ready, metrics - no API spec."""
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

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
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        # A restart must not wait for TIME_WAIT. On Windows the option would
        # let a second process bind the same port, hiding the conflict.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        sock.close()
        raise InternalPortUnavailable(
            f"Interner Port {port} ist belegt oder nicht verfügbar ({e.strerror or e}). "
            "METADATA_AGENT_INTERNAL_PORT braucht einen freien Port und genau einen "
            "Worker je Container."
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
