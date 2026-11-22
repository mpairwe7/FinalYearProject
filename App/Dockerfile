# syntax=docker/dockerfile:1.7

# =============================================================================
# URA Chatbot API - Production Dockerfile
# Multi-stage build for optimized image size
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder - Install dependencies
# -----------------------------------------------------------------------------
# uv comes from its own named stage, pinned by tag and digest, so the pin is a
# FROM line Dependabot can see rather than a COPY --from=<image> it cannot.
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv-binary

# Literal, patch-pinned base image: Dependabot's docker updater bumps FROM
# lines but cannot see an ARG default, which is how this sat on 3.11.11
# (Debian 12.10, OpenSSL 3.0.15) with 4 critical and 42 high fixable OS
# findings in the Trivy image scan.
FROM python:3.14.8-slim-bookworm@sha256:44e000a07b48bef1517a2b14adc93c17af728ca98858b5e24c6e1b18de1b2ef2 AS builder

# Install uv for fast dependency resolution
COPY --from=uv-binary /uv /usr/local/bin/uv

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

# Install Python dependencies from the hashed lock (ADR-0066, G690). It pins
# requirements.txt and requirements-receptionist.txt together, with the Pillow
# override in requirements-overrides.txt; its header has the compile command.
# torch is the CPU build: the laptop and CI that build this image have no NVIDIA
# GPU, the CUDA build added 2.78 GB of wheels, and GPU hosts build Dockerfile.gpu.
# The install names the index the lock was compiled against. docopt has no wheel,
# so builds stay allowed.
COPY App/backend/requirements.lock.txt ./requirements.lock.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --require-hashes -r requirements.lock.txt --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match

# -----------------------------------------------------------------------------
# Stage 2: Runtime - Production image
# -----------------------------------------------------------------------------
FROM python:3.14.8-slim-bookworm@sha256:44e000a07b48bef1517a2b14adc93c17af728ca98858b5e24c6e1b18de1b2ef2 AS runtime

# checkov:skip=CKV_DOCKER_9:apt below is a package upgraded for CVE-2026-109518; the command is apt-get
# Keep OS packages current with bookworm-security errata (fixes perl-base,
# libpcre2, openssl/libssl3 3.0.22-1~deb12u2 CVE-2026-84782, apt/libapt-pkg6.0 2.6.2 CVE-2026-109518).
RUN apt-get update \
    && apt-get -y --no-install-recommends dist-upgrade \
    && apt-get install -y --no-install-recommends --only-upgrade openssl libssl3 apt libapt-pkg6.0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# The base image's own packaging tools are never used here (the app runs from
# /opt/venv) and contain vendored dependencies (pip vendors msgpack and urllib3;
# setuptools vendors jaraco.context).
RUN python -m pip uninstall -y pip setuptools wheel && \
    rm -rf /usr/local/lib/python3.14/site-packages/*

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
COPY --chown=appuser:appuser App/plugins ./plugins/
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
# Which commit this image was built from: exported as ura_build_info and
# recorded in every audit row's provenance. Declared last so a new commit does
# not invalidate any cached layer above.
ARG GIT_SHA=""
ENV APP_BUILD_SHA=${GIT_SHA}

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
