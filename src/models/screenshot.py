"""Models of /screenshot; /generate and /upload share the capture method."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .common import MAX_URL_LENGTH, validate_node_id


class ScreenshotMethod(str, Enum):
    """Screenshot capture method."""

    PAGESHOT = "pageshot"  # Legacy alias — captured locally like PLAYWRIGHT
    PLAYWRIGHT = "playwright"  # Internal Playwright (privacy-safe, requires install)


class ScreenshotRequest(BaseModel):
    """Request model for standalone screenshot capture."""

    url: str = Field(max_length=MAX_URL_LENGTH, description="Webpage URL to capture")
    method: ScreenshotMethod = Field(
        default=ScreenshotMethod.PLAYWRIGHT,
        description="Capture method: 'playwright' (local, default) or the legacy alias 'pageshot' (also captured locally)",
    )
    width: int = Field(
        default=1920, ge=320, le=3840, description="Viewport width (320-3840)"
    )
    height: int = Field(
        default=1080, ge=200, le=2160, description="Viewport height (200-2160)"
    )
    format: str = Field(
        default="png",
        description="Image format: 'png' or 'jpeg'. Other values are delivered (and labelled) as png",
    )
    full_page: bool = Field(default=False, description="Capture entire scrollable page")
    delay: int = Field(
        default=2000, ge=0, le=10000, description="Wait before capture in ms (0-10000)"
    )
    # Optional: upload directly to a node
    node_id: Optional[str] = Field(
        default=None,
        description="If provided, upload screenshot as preview to this edu-sharing node. Must be a node UUID.",
    )
    _check_node_id = field_validator("node_id")(validate_node_id)
    repository: str = Field(
        default="staging",
        deprecated=True,
        description="Deprecated — wird ignoriert. Die Repository-URL kommt aus METADATA_AGENT_REPOSITORY_URL. Wird aus Abwärtskompatibilität weiterhin akzeptiert.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://klexikon.zum.de/wiki/Erde",
                    "method": "playwright",
                    "width": 1920,
                    "height": 1080,
                    "format": "png",
                }
            ]
        }
    }


class ScreenshotResponse(BaseModel):
    """Response from screenshot capture."""

    success: bool = Field(
        description="The capture succeeded. Whether the image reached the node "
        "is `preview_uploaded`."
    )
    method: str = Field(
        description="Method used: always 'playwright', also for requests with the legacy alias 'pageshot'"
    )
    url: str = Field(description="Captured URL")
    format: str = Field(description="Image format")
    mimetype: str = Field(description="MIME type (e.g. image/png)")
    width: int = Field(description="Viewport width")
    height: int = Field(description="Viewport height")
    size_bytes: int = Field(description="Image size in bytes")
    capture_time_ms: int = Field(description="Capture duration in ms")
    image_base64: Optional[str] = Field(
        default=None, description="Base64-encoded image (only in JSON response mode)"
    )
    # Preview upload result (if node_id was provided)
    preview_uploaded: Optional[bool] = Field(
        default=None, description="Whether preview was uploaded to node"
    )
    node_id: Optional[str] = Field(
        default=None, description="Node ID if preview was uploaded"
    )
    error: Optional[str] = Field(
        default=None,
        description="Why the preview was not stored at the node "
        "(`preview_uploaded: false`)",
    )
