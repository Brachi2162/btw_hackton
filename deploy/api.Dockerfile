# The API for the cloud (Render), built from the repo root. Same system
# packages as backend/Dockerfile, plus what docker-compose mounts at run time:
# config/ -> /config, prompts/ -> /prompts, and the cached sample reads in
# /data/cache, so the demo reads the sample forms with no model key (replay).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_THREAD_LIMIT=1 \
    DATA_DIR=/data \
    VLM_MODE=replay \
    CPU_WORKERS=1 \
    MAX_CONCURRENT_JOBS=2 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    openssl \
    fonts-dejavu-core \
    tesseract-ocr \
    tesseract-ocr-heb \
    && rm -rf /var/lib/apt/lists/*

# Only for building behind a TLS-intercepting filter (NetFree), as in
# backend/Dockerfile: in the cloud certs/ holds no certificate and the script
# is a no-op (NETFREE_REFRESH_CERTS=0).
COPY backend/certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates
COPY backend/netfree-certs.sh /usr/local/bin/netfree-certs
RUN chmod +x /usr/local/bin/netfree-certs
ARG NETFREE_REFRESH_CERTS=0

WORKDIR /app
COPY backend/requirements.txt .
RUN netfree-certs && pip install --upgrade pip && pip install -r requirements.txt

COPY backend/ /app/
COPY config/ /config/
COPY prompts/ /prompts/
COPY data/cache/vlm/ /data/cache/vlm/

EXPOSE 8000
# Render sets PORT; locally it is 8000.
CMD ["sh", "-c", "exec uvicorn vera.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
