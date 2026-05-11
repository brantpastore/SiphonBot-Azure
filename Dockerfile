FROM python:3.12-slim

WORKDIR /usr/src/app

# Install system deps: ffmpeg for media processing, curl+git for Node.js setup and bgutil clone
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone and build the bgutil PO token generation server (script mode, no sidecar needed)
ARG BGUTIL_VERSION=1.3.1
RUN git clone --depth 1 --single-branch --branch ${BGUTIL_VERSION} \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil-ytdlp-pot-provider && \
    cd /opt/bgutil-ytdlp-pot-provider/server && \
    npm ci && \
    npx tsc && \
    ls -la /opt/bgutil-ytdlp-pot-provider/server/build/main.js

COPY text_files/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir bgutil-ytdlp-pot-provider==${BGUTIL_VERSION}

COPY python_files /usr/src/app/python_files
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# The entrypoint starts the bgutil HTTP server (port 4416) before the bot so that
# yt-dlp uses HTTP mode, which manages visitor_data/PO-token state internally.
# This avoids the "bot-check" failures that occur when script mode uses a stale
# externally-supplied visitor_data.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "python_files/main.py"]