"""Swagger UI from the agent itself - no CDN (/docs)."""

from fastapi import Request
from fastapi.openapi.docs import get_swagger_ui_html


from fastapi import APIRouter


router = APIRouter()


@router.get("/docs", include_in_schema=False)
async def swagger_ui(request: Request):
    # root_path: behind the B-API proxy the page lives under a path prefix.
    root = request.scope.get("root_path", "").rstrip("/")
    return get_swagger_ui_html(
        openapi_url=f"{root}{request.app.openapi_url}",
        title=f"{request.app.title} – Swagger UI",
        swagger_js_url=f"{root}/docs-assets/swagger-ui-bundle.js",
        swagger_css_url=f"{root}/docs-assets/swagger-ui.css",
        swagger_favicon_url=f"{root}/docs-assets/favicon-32x32.png",
        # Otherwise Swagger UI sends the spec to validator.swagger.io.
        swagger_ui_parameters={"validatorUrl": None},
    )
