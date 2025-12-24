# FastAPI application container
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps (add build-essential if compiling packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt ./
RUN if [ -f requirements.txt ]; then \
      pip install --no-cache-dir --upgrade pip && \
      pip install --no-cache-dir -r requirements.txt; \
    else \
      echo "requirements.txt not found, skipping deps"; \
    fi

# Copy application code
COPY . .

# Expose API port
EXPOSE 8000

# Default command (override in compose/infra as needed)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
