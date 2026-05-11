FROM python:3.12-slim

WORKDIR /usr/src/app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY text_files/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install yt-dlp with PO token plugin support
RUN pip install --no-cache-dir --upgrade yt-dlp

# Optional: Install bgutil PO token provider (uncomment if using bgutil service)
# RUN pip install --no-cache-dir yt-dlp-pot

COPY python_files /usr/src/app/python_files

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# YouTube extraction with PO token / visitor data / cookies support
# Set these via Container App environment variables or secrets:
#   YTDLP_YOUTUBE_PO_TOKEN - PO token for YouTube auth
#   YTDLP_YOUTUBE_VISITOR_DATA - Visitor data for YouTube
#   YTDLP_COOKIES_FILE - Path to cookies.txt if using cookie-based auth
#   YTDLP_PROXY - Proxy URL if needed (e.g., socks5://host:port)

CMD ["python", "python_files/main.py"]