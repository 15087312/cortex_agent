# ============================================================
# Humanoid AGI Backend — Multi-stage Docker Build
# Stage 1: Builder — install deps, compile .py → .pyc, strip source
# Stage 2: Runtime — only .pyc + deps, no .py source files
# ============================================================

# ── Stage 1: Builder ──────────────────────────────────────
FROM python:3.9-slim AS builder

WORKDIR /build

# System deps for building native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy project files (exclude dev/test artifacts)
COPY . .

# Compile all .py to .pyc (write adjacent .pyc files, not __pycache__/)
RUN python -m compileall -b . && \
    find . -type f -name '*.py' -not -path './cli_tui/*' -delete && \
    find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null; true

# Strip .so debug symbols (smaller image)
RUN find /usr/local/lib/python3.9 -name '*.so' -exec strip --strip-unneeded {} \; 2>/dev/null; true


# ── Stage 2: Runtime ──────────────────────────────────────
FROM python:3.9-slim

WORKDIR /app

# Runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy Python site-packages from builder
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy compiled backend code (no .py source files)
COPY --from=builder /build/api /app/api
COPY --from=builder /build/config /app/config
COPY --from=builder /build/infra /app/infra
COPY --from=builder /build/modules /app/modules
COPY --from=builder /build/utils /app/utils
COPY --from=builder /build/plugins /app/plugins

# Runtime data directories
COPY --from=builder /build/data /app/data
COPY --from=builder /build/scripts /app/scripts

# Environment
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

# Default: start FastAPI server
ENTRYPOINT ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
