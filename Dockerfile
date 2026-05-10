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
    npx tsc

COPY text_files/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir bgutil-ytdlp-pot-provider==${BGUTIL_VERSION}

COPY python_files /usr/src/app/python_files

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Keep the runtime deterministic so the bundled yt-dlp plugin stays aligned with the
# yt-dlp version installed during the image build.
CMD ["python", "python_files/main.py"]