FROM python:3.13-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN sed -i '/mlx-lm/d; /pyaudio/d; /pyautogui/d' requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir textual>=2.0.0

# ── Runtime stage ──
FROM python:3.13-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy project code (test/docs/config excluded via .dockerignore)
COPY . .

# Pre-compile for faster startup
RUN python -m compileall -q .

# Remove test files and source .py (keep compiled .pyc)
RUN find /app -path '*/tests/*' -delete && \
    find /app -name '*.py' ! -path '/app/scripts/*' ! -path '/app/api/*' ! -path '/app/cli_tui/*' -delete

# Create required data directories
RUN mkdir -p data/memory data/cache data/search_cache

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Environment
ENV APP_ENV=production
ENV LOG_LEVEL=INFO
ENV SERVER_PORT=8080
ENV EMBEDDING_DEVICE=cpu
ENV EMBEDDING_LOCAL_FILES_ONLY=True
# SIMPLE_API_KEY must be provided at runtime via environment variable or docker-compose

EXPOSE 8080

CMD ["python", "scripts/start_all.py"]

# ── CLI TUI image ──
FROM python:3.13-slim AS cli

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir textual>=2.0.0 aiohttp>=3.9.1

COPY . .

# Keep only CLI-related .py (remove everything else)
RUN python -m compileall -q cli_tui && \
    find /app -name '*.py' -not -path '/app/cli_tui/*' -delete

RUN useradd --create-home --shell /bin/bash cliuser
USER cliuser

ENV API_BASE_URL=http://localhost:8080

ENTRYPOINT ["python", "-m", "cli_tui.main"]
