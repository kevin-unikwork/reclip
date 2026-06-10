# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI. Paste links from YouTube, Instagram, Facebook, and 1000+ other sites — download as MP4 or MP3.

![Python](https://img.shields.io/badge/python-3.12+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- Download videos from 1000+ supported sites via [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- MP4 video or MP3 audio extraction
- Quality/resolution picker (1080p, 720p, 480p, 360p etc.)
- Bulk downloads — paste multiple URLs at once
- Cookie manager UI — upload platform cookies without redeploying
- PO Token support via [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
- JavaScript challenge solving via Node.js 22 + yt-dlp-ejs
- Auto keep-alive pings to prevent Render free tier spin-down
- Per-platform rate limiting and retry logic

## Quick Start (Local)

```bash
git clone https://github.com/kevin-unikwork/reclip.git
cd reclip
pip install -r requirements.txt
python app.py
```

Open **http://localhost:3000**

Or with Docker:

```bash
docker build -t reclip .
docker run -p 8000:8000 reclip
```

## Deploy on Render

1. Fork this repo
2. Create a new **Web Service** on [render.com](https://render.com) → connect your repo → choose **Docker**
3. Create a **Private Service** for the bgutil PO token provider:
   - Image: `docker.io/brainicism/bgutil-ytdlp-pot-provider:latest`
   - Name: `bgutil-pot-provider`
4. Set environment variables on the web service (see below)

## Environment Variables

| Variable | Description | Example |
|---|---|---|
| `PORT` | Port to listen on | `8000` |
| `BGUTIL_POT_URL` | URL of the bgutil PO token service | `https://bgutil-pot-provider.onrender.com` |
| `BGUTIL_PUBLIC_URL` | Public URL of bgutil (used for keep-alive pings) | `https://bgutil-pot-provider.onrender.com` |
| `YTDLP_YOUTUBE_COOKIES` | YouTube cookies in Netscape format (env var) | *(paste cookie file content)* |
| `YTDLP_INSTAGRAM_COOKIES` | Instagram cookies in Netscape format | *(paste cookie file content)* |
| `YTDLP_COOKIES_GIST_URL` | GitHub Gist raw URL to auto-refresh YouTube cookies | `https://gist.githubusercontent.com/...` |
| `YTDLP_PROXY` | Residential proxy URL for bypassing IP blocks | `socks5://user:pass@host:port` |
| `COOKIES_SYNC_TOKEN` | Secret token to protect the cookie update API | `mysecrettoken` |
| `RENDER` | Set automatically by Render — enables cloud mode | *(auto)* |

## API Endpoints

### `GET /`
Main web UI.

---

### `POST /api/info`
Fetch video metadata (title, thumbnail, available formats).

**Request:**
```json
{ "url": "https://www.youtube.com/watch?v=..." }
```

**Response:**
```json
{
  "status": "info_ready",
  "info": {
    "title": "Video Title",
    "thumbnail": "https://...",
    "duration": 330,
    "uploader": "Channel Name",
    "formats": [
      { "id": "137", "label": "1080p", "height": 1080 },
      { "id": "136", "label": "720p",  "height": 720 }
    ]
  }
}
```

---

### `GET /api/status/<job_id>`
Poll download job status.

**Response:**
```json
{
  "status": "done",
  "filename": "Video Title.mp4",
  "error": null
}
```

Status values: `fetching_info`, `info_ready`, `downloading`, `done`, `error`

---

### `GET /api/file/<job_id>`
Stream the downloaded file to the browser.

---

### `POST /api/download`
Start a download job.

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "format": "video",
  "format_id": "137",
  "title": "Video Title"
}
```

`format` can be `"video"` or `"audio"`. `format_id` is optional — omit for best quality.

---

### `POST /api/cookies/update`
Upload cookies in Netscape format without redeploying.

**Request:**
```json
{
  "platform": "youtube",
  "cookies": "# Netscape HTTP Cookie File\n.youtube.com\t...",
  "token": "mysecrettoken"
}
```

`platform` can be `youtube`, `instagram`, or `facebook`.  
`token` is only required if `COOKIES_SYNC_TOKEN` env var is set.

---

### `POST /api/cookies/delete`
Delete a saved cookie file.

**Request:**
```json
{
  "platform": "youtube",
  "token": "mysecrettoken"
}
```

---

### `GET /api/debug`
Inspect the runtime environment. Shows yt-dlp version, Node path, bgutil connectivity, and runs a test download against a YouTube URL.

**Optional query param:** `?url=https://...` to test a specific URL.

**Response includes:**
```json
{
  "yt_dlp_version": "2026.06.09",
  "node_path": "/usr/bin/node",
  "bgutil_pot_url": "https://bgutil-pot-provider.onrender.com",
  "bgutil_ping": { "version": "1.3.1", "server_uptime": 1234 },
  "bgutil_pot_works": true,
  "cookie_file_used": null,
  "cookie_file_exists": false,
  "test_no_cookies_returncode": 0,
  "test_no_cookies_stderr": ""
}
```

---

## Cookie Manager (UI)

If YouTube downloads fail with bot detection errors, upload fresh cookies:

1. Install **[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** in Chrome/Brave
2. Go to `youtube.com` while **logged into your Google account**
3. Click the extension → **Export** → copies Netscape format cookies
4. Open your ReClip app → scroll to bottom → click **Toggle Cookie Manager**
5. Select **YouTube** → paste cookies → click **Save Cookies**

Cookies uploaded this way are saved to `cookies/youtube.txt` inside the container and used for all subsequent YouTube requests.

---

## Architecture

```
Browser → Flask (app.py)
              ├── /api/info      → yt-dlp -j (metadata)
              ├── /api/download  → yt-dlp (download)
              └── /api/cookies/* → cookie file management

yt-dlp → bgutil-pot-provider (PO token)
       → Node.js 22 (JS challenge solving via yt-dlp-ejs)
       → cookies/youtube.txt (optional, for bot bypass)
```

## Stack

- **Backend:** Python 3.12 + Flask
- **Frontend:** Vanilla HTML/CSS/JS (no build step)
- **Download engine:** yt-dlp + ffmpeg
- **JS challenge solver:** Node.js 22 + yt-dlp-ejs
- **PO token provider:** bgutil-ytdlp-pot-provider
- **Deployment:** Docker on Render

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
