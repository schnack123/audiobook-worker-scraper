# Requires the shared core as a named build context:
#   docker buildx build --platform linux/amd64 --build-context core=../audiobook-core -t <image> .
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CHROME_BIN=/usr/bin/chromium \
    DISPLAY=:99 \
    SCRAPER_NO_SANDBOX=1

# Chromium (headed, under Xvfb - headless Chrome is much easier to detect)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    xvfb \
    fonts-liberation \
    fonts-noto-color-emoji \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=core / /tmp/audiobook-core
RUN pip install /tmp/audiobook-core && rm -rf /tmp/audiobook-core

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m scraper && chown -R scraper /app \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
USER scraper

CMD ["./entrypoint.sh"]
