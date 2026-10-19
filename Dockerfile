# =============================================================================
# Metadata Agent API — Docker Image
# =============================================================================
# Includes Playwright + Chromium for privacy-safe screenshot capture.
# Screenshots are always captured here; 'pageshot' is only a legacy alias.
#
# Build:  docker build -t metadata-agent-api .
# Run:    docker run -d -p 127.0.0.1:8000:8000 --read-only --tmpfs /tmp
#           --tmpfs /home/appuser --stop-timeout 320 -e B_API_KEY=... metadata-agent-api
#         (one line; README.md, "Docker")
# =============================================================================

# Base images by digest: a tag can move under an unchanged Dockerfile. Update
# both with `docker buildx imagetools inspect <image:tag>` (the CI's Trivy scan
# reports when a fixed vulnerability makes that due).

# Build stage — install Python dependencies via uv
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS builder

WORKDIR /app

# Pinned: the version that wrote uv.lock; :latest could change the build unnoticed.
COPY --from=ghcr.io/astral-sh/uv:0.7.13@sha256:6c1e19020ec221986a210027040044a5df8de762eb36d5240e382bc41d7a9043 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Production stage
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

WORKDIR /app

# Install Playwright system dependencies (Chromium)
# These are the minimal packages needed for headless Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libx11-xcb1 \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Playwright Chromium browser into shared location, readable by the
# non-root appuser. User and permissions in the same layer as the browser and
# before COPY src/ (audit 18.10. final): a code change rebuilds neither.
# /app stays owned by root: the process serving requests can read its code but
# not change it, and no chown copies the venv into another layer. Chromium
# writes only to /tmp and the home directory.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN useradd -m -u 1000 appuser \
    && mkdir -p $PLAYWRIGHT_BROWSERS_PATH \
    && playwright install chromium \
    && chmod -R o+rx $PLAYWRIGHT_BROWSERS_PATH

# Copy application code (includes src/static/widget/ if present)
COPY src/ ./src/
# The image distributes third-party components; their notices go with it.
COPY LICENSE NOTICE ./

USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Which release and commit the image is (both CIs pass them). Last, so a new
# commit changes only the image metadata, no layer.
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="metadata-agent-api" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.licenses="Apache-2.0"
