# KPI Bridge: application image (API + built frontend, same origin) and worker.
# Base images pinned to a precise version. No Vite server in the distribution.

FROM node:24.21.0-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
COPY shared/ /src/shared/
RUN npm run build

FROM python:3.13.9-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    KPI_DATA_DIR=/data KPI_STATIC_DIR=/app/static KPI_LOCALES_DIR=/app/shared/locales \
    PYTHONPATH=/app/backend KPI_TESSERACT_CMD=/usr/bin/tesseract
# apt-get upgrade applies the Debian security fixes published since the base image was built.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra tesseract-ocr-deu tesseract-ocr-spa \
      tesseract-ocr-rus tesseract-ocr-chi-sim \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home /nonexistent --shell /usr/sbin/nologin kpi
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --require-hashes -r /app/backend/requirements.lock
COPY backend/kpibridge /app/backend/kpibridge
COPY shared/ /app/shared/
COPY --from=frontend /src/frontend/dist /app/static
RUN mkdir -p /data && chown kpi:kpi /data
USER kpi
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health',timeout=2);sys.exit(0)"
CMD ["python", "-m", "kpibridge.app"]
