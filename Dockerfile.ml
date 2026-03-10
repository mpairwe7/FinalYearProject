# syntax=docker/dockerfile:1.7

# =============================================================================
# URA Chatbot ML Training - Dockerfile
# For local and CI/CD model training
# =============================================================================

ARG PYTHON_IMAGE=python:3.11.11-slim-bookworm
FROM ${PYTHON_IMAGE}

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./requirements.txt
ARG MLFLOW_VERSION=2.16.2
ARG DVC_VERSION=3.51.2
ARG KAGGLE_VERSION=1.6.17
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r requirements.txt && \
    uv pip install --system \
      "mlflow==${MLFLOW_VERSION}" \
      "dvc==${DVC_VERSION}" \
      "kaggle==${KAGGLE_VERSION}"

# Copy ML pipeline code
RUN groupadd --gid 1000 trainer && \
    useradd --uid 1000 --gid trainer --create-home --shell /usr/sbin/nologin trainer
COPY --chown=trainer:trainer ml ./ml
COPY --chown=trainer:trainer Data/dataset ./datasets

# Create output directories
RUN mkdir -p /app/artifacts/models /app/artifacts/metrics && \
    chown -R trainer:trainer /app

# Default command
USER trainer
CMD ["python", "ml/pipelines/train.py", "--config", "ml/configs/training_config.yaml"]
