# ============================================================
# Lerna — Assessment Module · production image
# Railway-compatible: listens on $PORT (falls back to 8002)
# ============================================================
FROM python:3.12-slim

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

# --- non-root user + writable data dirs ---
RUN useradd --create-home --shell /bin/bash lerna \
    && mkdir -p /app/data \
    && chown -R lerna:lerna /app
USER lerna

# Railway injects PORT; use it when present (default 8002 for plain Docker)
ENV PORT=8002 \
    ASSESS_DB_URL=sqlite:////app/data/assessment.db

EXPOSE 8002

# single-line healthcheck (multi-line HEALTHCHECK broke Railway's validation)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8002'), timeout=4).status == 200 else 1)"

# shell form so $PORT expands at runtime (Railway requirement)
CMD uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-8002} --workers 2
