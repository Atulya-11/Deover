import os
import shutil
import tempfile
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, jsonify, render_template, request, send_file, after_this_request

app = Flask(__name__)

ALLOWED_DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com",
    "facebook.com", "fb.watch", "fb.com",
)
MAX_DURATION = int(os.environ.get("MAX_DURATION", 3600))  # seconds
STANDARD_HEIGHTS = [2160, 1440, 1080, 720, 480, 360, 240, 144]


def valid_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def friendly_error(e: Exception) -> str:
    msg = str(e).lower()
    if any(k in msg for k in ("private", "login", "sign in", "log in", "cookies", "authentication")):
        return "Ye video private hai ya login maangta hai. Sirf public video ka link chalega."
    if "unavailable" in msg or "removed" in msg or "not exist" in msg:
        return "Ye video available nahi hai (delete ya block ho sakta hai)."
    if "too long" in msg or "does not pass filter" in msg:
        return "Video bahut lamba hai. Limit se chhota video try karo."
    return "Video fetch nahi ho paya. Link check karo aur dobara try karo."


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not valid_url(url):
        return jsonify(error="Sirf YouTube, Instagram ya Facebook ka link daalo."), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True}) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify(error=friendly_error(e)), 400

    heights = {
        f["height"] for f in data.get("formats", [])
        if f.get("vcodec") not in (None, "none") and f.get("height")
    }
    available = [h for h in STANDARD_HEIGHTS if any(x >= h for x in heights)]
    # keep only heights the video really has (closest standard buckets)
    available = [h for h in STANDARD_HEIGHTS if h in heights] or sorted(heights, reverse=True)[:6]

    return jsonify(
        title=data.get("title"),
        thumbnail=data.get("thumbnail"),
        duration=data.get("duration"),
        uploader=data.get("uploader"),
        qualities=available,
    )


@app.route("/api/download")
def download():
    url = request.args.get("url", "").strip()
    quality = request.args.get("quality", "720")
    if not valid_url(url):
        return jsonify(error="Invalid link"), 400

    tmp = tempfile.mkdtemp(prefix="dl_")

    @after_this_request
    def cleanup(response):
        response.call_on_close(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return response

    opts = {
        "quiet": True,
        "noplaylist": True,
        "outtmpl": os.path.join(tmp, "%(title).80s.%(ext)s"),
        "restrictfilenames": True,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration < {MAX_DURATION}"),
    }
    if quality == "audio":
        opts.update(
            format="bestaudio/best",
            postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        )
    else:
        try:
            h = int(quality)
        except ValueError:
            h = 720
        opts.update(
            format=f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
            merge_output_format="mp4",
        )

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify(error=friendly_error(e)), 400

    files = os.listdir(tmp)
    if not files:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify(error="Download fail ho gaya, dobara try karo."), 500
    return send_file(os.path.join(tmp, files[0]), as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
