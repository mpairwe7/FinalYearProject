# =============================================================================
# URA Chatbot ML Training - Dockerfile
# For local and CI/CD model training
# =============================================================================

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install mlflow dvc kaggle

# Copy ML pipeline code
COPY ml ./ml
COPY Data/dataset ./datasets

# Create output directories
RUN mkdir -p /app/artifacts/models /app/artifacts/metrics

# Default command
CMD ["python", "ml/pipelines/train.py", "--config", "ml/configs/training_config.yaml"]
