# LibreOffice conversion service — formula recalculation, Office → PDF.
#
# Runs as a standalone container alongside the backend.  Communicates via
# HTTP (POST file → receive converted file).
#
# Build:
#   docker compose -f docker/docker-compose.yml build libreoffice
#
# Run:
#   docker compose -f docker/docker-compose.yml up -d libreoffice

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    HOME=/tmp/libreoffice-home \
    PYTHONUNBUFFERED=1

# Install LibreOffice + Chinese fonts + Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc \
    libreoffice-writer \
    libreoffice-impress \
    libreoffice-common \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    python3 \
    python3-pip \
    curl \
    default-jre \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py ./

# Create a dedicated user for LibreOffice (no root)
RUN useradd -m -d /tmp/libreoffice-home -s /bin/bash libre && \
    mkdir -p /data/input /data/output && \
    chown -R libre:libre /data /app

USER libre

EXPOSE 8101

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8101", "--workers", "1"]