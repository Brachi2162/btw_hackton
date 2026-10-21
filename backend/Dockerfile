FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Make pip, requests, httpx (Anthropic SDK) trust the system CA store,
    # which includes any extra CAs from certs/ (see below).
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# Tesseract with Hebrew for OCR. (opencv-python-headless and PyMuPDF wheels
# need no extra system libraries.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tesseract-ocr \
    tesseract-ocr-heb \
    && rm -rf /var/lib/apt/lists/*

# Extra root CAs for TLS-intercepting network filters (e.g. NetFree).
# Put PEM *.crt files in backend/certs/ (git-ignored); empty = no-op.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates

WORKDIR /app

# Fallback, build-time only: hosts pip may reach without TLS verification,
# if you can't add your filter's CA to certs/. Leave empty everywhere else.
ARG PIP_TRUSTED_HOST=""

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "vera.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
