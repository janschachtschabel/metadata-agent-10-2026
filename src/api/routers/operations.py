"""
Liveness, readiness and the debug endpoints on the public port.

What they answer is built in src/infra.py, which also serves the internal port.
"""

from fastapi import HTTPException, Request

from ...infra import (
    health_payload,
    readiness_response,
)
from ...services.llm_auth import (
    current_auth_source,
    mask_header,
)


from fastapi import APIRouter

from .. import deps


router = APIRouter()


# ============================================================================
# Health & Info Endpoints
# ============================================================================


@router.get("/health", include_in_schema=False)
async def health_check():
    """Liveness: the process answers (infra.py). With details on the internal port."""
    return health_payload(deps.settings, detailed=False)


@router.get("/ready", include_in_schema=False)
async def readiness_check():
    """Readiness: configuration that can work at all (infra.py). 503 if not; reasons in the log and on the internal port."""
    return readiness_response(deps.settings, detailed=False)


@router.get("/debug/auth", include_in_schema=False)
async def debug_auth(request: Request):
    """
    Does a forwarded B-API key arrive? Masked - presence, scheme, length only.

    Exists only with METADATA_AGENT_DEBUG_ENDPOINTS=true; meant for setting up
    the B-API proxy (forwardApiKey), not for normal operation.
    """
    if not deps.settings.debug_endpoints:
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "mode": deps.settings.b_api_auth_mode,
        "Authorization": mask_header(request.headers.get("authorization")),
        "X-API-KEY": mask_header(request.headers.get("x-api-key")),
        "b_api_key_configured": bool(deps.settings.b_api_key),
        "llm_calls_use": current_auth_source(deps.settings),
    }


@router.get("/debug/dependencies", include_in_schema=False)
async def debug_dependencies():
    """
    Are B-API, text extraction and repository reachable - and is the key taken?

    Exists only with METADATA_AGENT_DEBUG_ENDPOINTS=true (services/diagnostics.py).
    """
    if not deps.settings.debug_endpoints:
        raise HTTPException(status_code=404, detail="Not Found")
    return await deps.check_dependencies(deps.settings)
