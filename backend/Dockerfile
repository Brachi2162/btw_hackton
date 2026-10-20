FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract with Hebrew for OCR. (opencv-python-headless and PyMuPDF wheels
# need no extra system libraries.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-heb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Optional, build-time only: space-separated hosts pip may reach without TLS
# verification. Needed behind TLS-intercepting filters (e.g. NetFree) whose
# certificate chain OpenSSL cannot verify. Leave empty everywhere else.
ARG PIP_TRUSTED_HOST=""

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "vera.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
