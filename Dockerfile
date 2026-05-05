# ─────────────────────────────────────────────────────────────
#  CupGet API — Dockerfile (optimised for Render.com)
#  Base: Python 3.12 slim + FFmpeg + yt-dlp
# ─────────────────────────────────────────────────────────────

FROM python:3.12-slim

# ── Labels ───────────────────────────────────────────────────
LABEL maintainer="CupGet Team"
LABEL description="CupGet Video Downloader API"
LABEL version="1.0.0"

# ── System dependencies & FFmpeg ─────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
        wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────
# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────
COPY . .

# ── Non-root user for security ────────────────────────────────
RUN addgroup --system cupget \
 && adduser --system --ingroup cupget cupget \
 && mkdir -p /tmp/cupget \
 && chown -R cupget:cupget /app /tmp/cupget

USER cupget

# ── Runtime config ────────────────────────────────────────────
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# ── Health check ──────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# ── Start server ──────────────────────────────────────────────
# Render injects $PORT automatically; we bind to 0.0.0.0
CMD uvicorn main:app \
        --host 0.0.0.0 \
        --port ${PORT} \
        --workers 2 \
        --log-level info \
        --timeout-keep-alive 60
