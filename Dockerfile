# syntax=docker/dockerfile:1.7

# =============================================================================
# URA Chatbot API - Production Dockerfile
# Multi-stage build for optimized image size
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder - Install dependencies
# -----------------------------------------------------------------------------
ARG PYTHON_IMAGE=python:3.11.11-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

# Install Python dependencies
COPY App/backend/requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match

# -----------------------------------------------------------------------------
# Stage 2: Runtime - Production image
# -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Labels for container registry
LABEL org.opencontainers.image.title="URA Chatbot API" \
      org.opencontainers.image.description="Uganda Revenue Authority Chatbot API" \
      org.opencontainers.image.vendor="mpairweLandwind" \
      org.opencontainers.image.source="https://github.com/mpairweLandwind/FinalYearProject"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    PORT=8000 \
    WORKERS=4

WORKDIR /app

# Create non-root user
RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY --chown=appuser:appuser App/backend/app ./app/
COPY --chown=appuser:appuser App/backend/entrypoint.sh /usr/local/bin/entrypoint.sh

# Create necessary directories (including models + offline bundle dirs)
RUN mkdir -p /app/models /app/logs /app/cache /app/offline /app/quantized /app/hf_cache /app/data_store && \
    chown -R appuser:appuser /app && \
    chmod +x /usr/local/bin/entrypoint.sh

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request;url=f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/health';sys.exit(0 if urllib.request.urlopen(url, timeout=5).status == 200 else 1)"]

# Expose port
EXPOSE 8000

# Start API server
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
