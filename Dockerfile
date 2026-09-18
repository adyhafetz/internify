FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates qpdf \
    && rm -rf /var/lib/apt/lists/*

# Tectonic: self-contained TeX engine, installed as a single static binary.
# Check https://github.com/tectonic-typesetting/tectonic/releases for the latest
# version/asset name if this URL 404s after a new release.
ARG TECTONIC_VERSION=0.15.0
RUN curl -fsSL \
    "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    -o /tmp/tectonic.tar.gz \
    && tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin \
    && rm /tmp/tectonic.tar.gz \
    && chmod +x /usr/local/bin/tectonic

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY service ./service

EXPOSE 8000
CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8000"]
