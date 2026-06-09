import os
import uuid
import glob
import json
import subprocess
import threading
import time
import shutil
import sys
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
BASE_DIR = os.path.dirname(__file__)
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIES_DIR, exist_ok=True)


def init_cookies_from_env():
    youtube_cookies_env = os.environ.get("YTDLP_YOUTUBE_COOKIES")
    if youtube_cookies_env:
        try:
            with open(os.path.join(COOKIES_DIR, "youtube.txt"), "w", encoding="utf-8") as f:
                f.write(youtube_cookies_env.strip() + "\n")
            print(f"Successfully wrote cookies/youtube.txt ({len(youtube_cookies_env)} bytes)")
        except Exception as e:
            print(f"Error writing youtube cookies: {e}")

    instagram_cookies_env = os.environ.get("YTDLP_INSTAGRAM_COOKIES")
    if instagram_cookies_env:
        try:
            with open(os.path.join(COOKIES_DIR, "instagram.txt"), "w", encoding="utf-8") as f:
                f.write(instagram_cookies_env.strip() + "\n")
            print(f"Successfully wrote cookies/instagram.txt ({len(instagram_cookies_env)} bytes)")
        except Exception as e:
            print(f"Error writing instagram cookies: {e}")


init_cookies_from_env()

jobs = {}
info_cache = {}
INFO_CACHE_TTL = 300
INFO_TO_DOWNLOAD_GAP_SECONDS = 8
PLATFORM_LOCKS = {
    "youtube": threading.Semaphore(1),
    "instagram": threading.Semaphore(1),
}
PLATFORM_MIN_INTERVALS = {
    "youtube": 10,
    "instagram": 5,
}
platform_last_request = {}
platform_last_request_lock = threading.Lock()
YTDLP_RETRYABLE_ERRORS = ("HTTP Error 429", "Too Many Requests")


def get_platform(url):
    hostname = (urlparse(url).hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    if "youtube.com" in hostname or "youtu.be" in hostname:
        return "youtube"
    if "instagram.com" in hostname:
        return "instagram"
    if "facebook.com" in hostname or "fb.watch" in hostname:
        return "facebook"
    return "generic"


def get_cookies_file(url):
    env_cookies_file = os.environ.get("YTDLP_COOKIES_FILE")
    if env_cookies_file and os.path.exists(env_cookies_file):
        return env_cookies_file

    platform = get_platform(url)

    platform_candidates = []
    if platform == "youtube":
        platform_candidates = ["youtube.txt", "www.youtube.com_cookies.txt", "cookies.txt"]
    elif platform == "instagram":
        platform_candidates = ["instagram.txt", "www.instagram.com_cookies.txt", "cookies.txt"]
    elif platform == "facebook":
        platform_candidates = ["facebook.txt", "www.facebook.com_cookies.txt", "cookies.txt"]
    else:
        platform_candidates = ["cookies.txt"]

    for filename in platform_candidates:
        candidate = os.path.join(COOKIES_DIR, filename)
        if os.path.exists(candidate):
            return candidate

    legacy_root_cookie = os.path.join(BASE_DIR, "cookies.txt")
    if os.path.exists(legacy_root_cookie):
        return legacy_root_cookie

    return None


def get_ytdlp_executable():
    # 1. Try finding in standard PATH
    path_executable = shutil.which("yt-dlp")
    if path_executable:
        return path_executable

    # 2. Try looking in the same directory as the running Python interpreter (e.g. inside venv/Scripts)
    python_dir = os.path.dirname(sys.executable)
    for ext in ("", ".exe"):
        candidate = os.path.join(python_dir, f"yt-dlp{ext}")
        if os.path.exists(candidate):
            return candidate

    # Fallback to "yt-dlp"
    return "yt-dlp"


def build_yt_dlp_cmd(url, *extra_args, use_fallback=False):
    cmd = [
        get_ytdlp_executable(),
        "--no-playlist",
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    ]

    # Optional residential proxy — set YTDLP_PROXY env var on Render to route
    # YouTube requests through a non-datacenter IP (e.g. socks5://user:pass@host:port)
    proxy = os.environ.get("YTDLP_PROXY")
    if proxy:
        cmd += ["--proxy", proxy]

    platform = get_platform(url)
    cookies_file = get_cookies_file(url)

    is_cloud = os.environ.get("RENDER") or proxy

    if platform == "youtube":
        if is_cloud or use_fallback:
            if cookies_file:
                # With cookies: use the standard web client with full webpage access.
                # yt-dlp needs to load the page to extract the PO (Proof of Origin)
                # token from the authenticated session.
                cmd += [
                    "--extractor-args",
                    "youtube:player_client=web,mweb,web_embedded",
                ]
            else:
                # Without cookies: use mobile clients that don't require PO tokens.
                cmd += [
                    "--extractor-args",
                    "youtube:player_client=mweb,web_embedded;player_skip=webpage,configs",
                ]

    if cookies_file and (is_cloud or use_fallback or os.environ.get("YTDLP_COOKIES_FILE")):
        cmd += ["--cookies", cookies_file]
    cmd += list(extra_args)
    return cmd


def wait_for_platform_cooldown(platform):
    min_interval = PLATFORM_MIN_INTERVALS.get(platform, 0)
    if min_interval <= 0:
        return

    with platform_last_request_lock:
        last_request = platform_last_request.get(platform)
        now = time.time()
        if last_request is not None:
            wait_time = min_interval - (now - last_request)
            if wait_time > 0:
                time.sleep(wait_time)
                now = time.time()
        platform_last_request[platform] = now


def run_yt_dlp_with_retries(cmd, timeout, platform, max_attempts=None):
    if max_attempts is None:
        max_attempts = 3 if platform == "youtube" else 1
    last_result = None

    for attempt in range(max_attempts):
        if attempt > 0:
            backoff_seconds = 10 * (2 ** (attempt - 1))
            time.sleep(backoff_seconds)
            wait_for_platform_cooldown(platform)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        last_result = result
        stderr = (result.stderr or "").strip()
        if result.returncode == 0:
            return result
        if platform != "youtube":
            return result
        if not any(error_text in stderr for error_text in YTDLP_RETRYABLE_ERRORS):
            return result

    return last_result


def wait_after_recent_info_fetch(url):
    cached = info_cache.get(url)
    if not cached:
        return

    fetched_at = cached.get("timestamp")
    if fetched_at is None:
        return

    elapsed = time.time() - fetched_at
    wait_time = INFO_TO_DOWNLOAD_GAP_SECONDS - elapsed
    if wait_time > 0:
        time.sleep(wait_time)


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")
    platform = get_platform(url)
    platform_lock = PLATFORM_LOCKS.get(platform)

    cmd = build_yt_dlp_cmd(url, "-o", out_template, use_fallback=False)

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    cmd.append(url)

    try:
        if platform_lock:
            platform_lock.acquire()
        wait_after_recent_info_fetch(url)
        wait_for_platform_cooldown(platform)

        result = run_yt_dlp_with_retries(cmd, timeout=300, platform=platform)
        is_cloud = os.environ.get("RENDER") or os.environ.get("YTDLP_PROXY")
        if result.returncode != 0 and not is_cloud:
            # Try fallback run
            cmd_fallback = build_yt_dlp_cmd(url, "-o", out_template, use_fallback=True)
            if format_choice == "audio":
                cmd_fallback += ["-x", "--audio-format", "mp3"]
            elif format_id:
                cmd_fallback += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
            else:
                cmd_fallback += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]
            cmd_fallback.append(url)
            result = run_yt_dlp_with_retries(cmd_fallback, timeout=300, platform=platform)

        if result.returncode != 0:
            job["status"] = "error"
            job["error"] = result.stderr.strip() or "yt-dlp failed"
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        job["status"] = "done"
        job["file"] = chosen
        ext = os.path.splitext(chosen)[1]
        title = job.get("title", "").strip()
        # Sanitize title for filename
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:20].strip()
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)
    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Download timed out (5 min limit)"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        if platform_lock:
            platform_lock.release()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cached = info_cache.get(url)
    now = time.time()
    if cached and now - cached["timestamp"] < INFO_CACHE_TTL:
        return jsonify(cached["data"])

    platform = get_platform(url)
    cmd = build_yt_dlp_cmd(url, "-j", url, use_fallback=False)
    try:
        wait_for_platform_cooldown(platform)
        result = run_yt_dlp_with_retries(cmd, timeout=35, platform=platform, max_attempts=1)
        is_cloud = os.environ.get("RENDER") or os.environ.get("YTDLP_PROXY")
        if result.returncode != 0 and not is_cloud:
            # Fallback retry
            cmd_fallback = build_yt_dlp_cmd(url, "-j", url, use_fallback=True)
            result = run_yt_dlp_with_retries(cmd_fallback, timeout=35, platform=platform, max_attempts=1)

        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip() or "yt-dlp failed"}), 400

        stdout = (result.stdout or "").strip()
        if not stdout:
            return jsonify({"error": "yt-dlp returned no metadata"}), 400

        try:
            info = json.loads(stdout)
        except json.JSONDecodeError:
            return jsonify({"error": "yt-dlp returned invalid metadata"}), 400

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append({
                "id": f["format_id"],
                "label": f"{height}p",
                "height": height,
            })
        formats.sort(key=lambda x: x["height"], reverse=True)

        response_data = {
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        }
        info_cache[url] = {"timestamp": now, "data": response_data}

        return jsonify(response_data)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
