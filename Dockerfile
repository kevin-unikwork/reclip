FROM python:3.12-slim

# Install ffmpeg and Node.js (required by yt-dlp for JavaScript challenge solving)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Install yt-dlp plugins: bgutil PO token provider
# Note: yt-dlp-ejs (EJS challenge solver scripts) is already installed via yt-dlp[default] in requirements.txt
# There is no --install-plugin ejs; EJS scripts ship as the yt-dlp-ejs PyPI package.
RUN yt-dlp --install-plugin bgutil-ytdlp-pot-provider 2>/dev/null || true

EXPOSE 8000
# Auto-update yt-dlp to latest on every deploy — critical for YouTube support
# since YouTube frequently changes its extraction logic.
CMD yt-dlp -U --quiet 2>/dev/null || true && \
    gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 120 app:app

