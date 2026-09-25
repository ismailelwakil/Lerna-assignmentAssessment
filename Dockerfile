# ============================================================
# Lerna — Assessment Module · production image
# ============================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# --- dependencies (cached layer) ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- application code ---
COPY app ./app
COPY ui ./ui
COPY streamlit_app.py .
COPY tests ./tests

# --- non-root user + writable data dir ---
RUN useradd --create-home --shell /bin/bash lerna \
    && mkdir -p /app/data \
    && chown -R lerna:lerna /app
USER lerna

# persistent state (SQLite DB, uploads) lives here — mount a volume
VOLUME ["/app/data"]

ENV ASSESS_DB_URL=sqlite:////app/data/assessment.db \
    ASSESS_ENV=production

EXPOSE 8002 8501

# --- healthcheck against the API ---
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=4).status==200 else 1)" \
  || exit 1

# default: REST API. The Streamlit UI runs as a second container
# (see docker-compose.yml) using the same image with a different command.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
