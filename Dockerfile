FROM python:3.11-slim

# Don't write .pyc files: the project folders are mounted from the host, and root-owned cache files there
# make them impossible to delete without sudo.
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates qpdf \
    && rm -rf /var/lib/apt/lists/*

# Tectonic: self-contained TeX engine, installed as a single static binary. The build picks the file for this machine,
# so the image also builds on ARM (Apple Silicon, Raspberry Pi, Oracle's free ARM servers).
# Check https://github.com/tectonic-typesetting/tectonic/releases for the latest
# version/asset name if this URL 404s after a new release.
ARG TECTONIC_VERSION=0.15.0
RUN case "$(uname -m)" in \
      x86_64) arch=x86_64 ;; \
      aarch64|arm64) arch=aarch64 ;; \
      *) echo "Unsupported machine type: $(uname -m)" >&2; exit 1 ;; \
    esac \
    && curl -fsSL \
    "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-${arch}-unknown-linux-musl.tar.gz" \
    -o /tmp/tectonic.tar.gz \
    && tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin \
    && rm /tmp/tectonic.tar.gz \
    && chmod +x /usr/local/bin/tectonic

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY service ./service
COPY bot ./bot

EXPOSE 8080
CMD ["python", "-m", "bot.main"]
