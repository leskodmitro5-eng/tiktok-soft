import os
import re
import time
import logging
from pathlib import Path
import yt_dlp
import imageio_ffmpeg

logger = logging.getLogger("YouTubeDownloader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)


def extract_youtube_url(text: str) -> str | None:
    """Extracts the first valid YouTube URL from a message text."""
    if not text:
        return None
    match = YOUTUBE_REGEX.search(text)
    if match:
        url = match.group(0)
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        return url
    return None


BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "cookies.txt"

# If YOUTUBE_COOKIES is provided via env var, write it to cookies.txt
cookies_env = os.getenv("YOUTUBE_COOKIES", "").strip()
if cookies_env:
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write(cookies_env)
    except Exception as e:
        logger.warning(f"Failed to write cookies.txt from environment: {e}")

try:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    ffmpeg_dir = os.path.dirname(ffmpeg_exe)
    if ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    ffmpeg_exe = "ffmpeg"

def build_youtube_ydl_opts(custom_opts: dict = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/bestvideo+bestaudio/best",
    }
    if custom_opts:
        opts.update(custom_opts)

    has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 10
    if has_cookies:
        opts["cookiefile"] = str(COOKIES_FILE)
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["web", "android", "ios", "mweb"]
            }
        }
    else:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "ios", "web"]
            }
        }
    return opts


def get_youtube_video_info(url: str) -> dict:
    """Extracts metadata for a YouTube video without downloading or format evaluation, with oEmbed fallback."""
    # 1. Primary: yt-dlp with process=False (extracts raw JSON without evaluating format requirements)
    client_candidates = [
        ["web", "android", "ios", "mweb"] if (COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 10) else ["android", "ios", "web"],
        ["android"],
        ["ios"],
        ["web"]
    ]

    for clients in client_candidates:
        ydl_opts = build_youtube_ydl_opts({
            "skip_download": True,
            "check_formats": False,
            "extractor_args": {
                "youtube": {
                    "player_client": clients
                }
            }
        })
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
                if info:
                    title = info.get("title") or "YouTube Video"
                    dur = float(info.get("duration", 0.0) or 0.0)
                    vid_id = info.get("id") or "video"
                    return {
                        "id": vid_id,
                        "title": title,
                        "duration": dur,
                        "uploader": info.get("uploader") or info.get("channel") or "YouTube",
                        "url": url
                    }
        except Exception as e:
            logger.debug(f"Raw extract notice for {clients}: {e}")

    # 2. Secondary: Official YouTube oEmbed API for instant title fetching
    try:
        import requests
        r = requests.get(f"https://www.youtube.com/oembed?url={url}&format=json", timeout=4)
        if r.status_code == 200:
            data = r.json()
            match = YOUTUBE_REGEX.search(url)
            vid_id = match.group(4) if match else "youtube_video"
            return {
                "id": vid_id,
                "title": data.get("title", "YouTube Video"),
                "duration": 60.0,
                "uploader": data.get("author_name", "YouTube"),
                "url": url
            }
    except Exception as oe_err:
        logger.debug(f"oEmbed fallback notice: {oe_err}")

    # 3. Tertiary: Fallback parsing from regex
    match = YOUTUBE_REGEX.search(url)
    vid_id = match.group(4) if match else "youtube_video"
    return {
        "id": vid_id,
        "title": "YouTube Video",
        "duration": 60.0,
        "uploader": "YouTube",
        "url": url
    }


def download_youtube_video(url: str, output_path: str, progress_callback=None) -> str:
    """
    Downloads YouTube video at best quality up to 1080p and merges video+audio into MP4.
    Includes Windows file-lock recovery for [WinError 32].
    """
    out_dir = str(Path(output_path).parent)
    base_name = Path(output_path).stem

    # Clean any pre-existing files in the target directory
    if os.path.exists(out_dir):
        for f in os.listdir(out_dir):
            if f.startswith(base_name):
                try:
                    os.remove(os.path.join(out_dir, f))
                except Exception:
                    pass

    def ydl_hook(d):
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if total > 0:
                progress_callback(downloaded, total)

    ydl_opts = build_youtube_ydl_opts({
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/bestvideo+bestaudio/best",
        "outtmpl": os.path.join(out_dir, f"{base_name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_exe,
        "merge_output_format": "mp4",
        "nopart": True,
        "overwrites": True,
        "progress_hooks": [ydl_hook] if progress_callback else [],
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.warning(f"yt_dlp primary download notice: {e}. Retrying with universal format 'best'...")
        try:
            fallback_opts = build_youtube_ydl_opts({
                "format": "best",
                "outtmpl": os.path.join(out_dir, f"{base_name}.%(ext)s"),
                "ffmpeg_location": ffmpeg_exe,
                "merge_output_format": "mp4",
                "nopart": True,
                "overwrites": True,
            })
            with yt_dlp.YoutubeDL(fallback_opts) as ydl_fb:
                ydl_fb.download([url])
        except Exception as fb_err:
            logger.warning(f"yt_dlp fallback download error: {fb_err}")
        time.sleep(1.0)


    final_path = os.path.join(out_dir, f"{base_name}.mp4")

    # 1. Direct check
    for _ in range(5):
        if os.path.exists(final_path) and os.path.getsize(final_path) > 1024:
            logger.info(f"YouTube video downloaded successfully -> {final_path} (size: {os.path.getsize(final_path)/(1024*1024):.1f} MB)")
            return final_path
        time.sleep(0.4)

    # 2. Check for .temp.mp4 leftover from ffmpeg merger and atomically rename
    temp_path = os.path.join(out_dir, f"{base_name}.temp.mp4")
    if os.path.exists(temp_path) and os.path.getsize(temp_path) > 1024:
        for _ in range(10):
            try:
                os.replace(temp_path, final_path)
                logger.info(f"Recovered and renamed {temp_path} -> {final_path}")
                return final_path
            except PermissionError:
                time.sleep(0.3)

    # 3. Fallback search
    for f in os.listdir(out_dir):
        if f.startswith(base_name) and not f.endswith(".part"):
            matched = os.path.join(out_dir, f)
            if os.path.getsize(matched) > 1024:
                logger.info(f"YouTube downloaded file found -> {matched}")
                return matched

    raise FileNotFoundError(f"Downloaded YouTube video not found for {url}")


def download_youtube_section(url: str, start_sec: float, end_sec: float, output_path: str, progress_callback=None) -> str:
    """
    Downloads only a specific segment [start_sec, end_sec] from a YouTube video without downloading the full video.
    Ideal for fast highlights slicing on long videos.
    """
    out_dir = str(Path(output_path).parent)
    base_name = Path(output_path).stem

    os.makedirs(out_dir, exist_ok=True)

    def ydl_hook(d):
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if total > 0:
                progress_callback(downloaded, total)

    download_ranges = yt_dlp.utils.download_range_func(None, [(start_sec, end_sec)])

    ydl_opts = build_youtube_ydl_opts({
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, f"{base_name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_exe,
        "merge_output_format": "mp4",
        "nopart": True,
        "overwrites": True,
        "download_ranges": download_ranges,
        "force_keyframes_at_cuts": True,
        "progress_hooks": [ydl_hook] if progress_callback else [],
    })




    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.warning(f"yt_dlp section download notice: {e}")

    final_path = os.path.join(out_dir, f"{base_name}.mp4")
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024:
        logger.info(f"YouTube section [{start_sec:.1f}s - {end_sec:.1f}s] downloaded -> {final_path}")
        return final_path

    # Fallback to standard download + slice if section download failed
    logger.info("Direct section download was not available for stream, downloading full video and slicing...")
    full_path = os.path.join(out_dir, f"full_{base_name}.mp4")
    download_youtube_video(url, full_path, progress_callback)
    
    import subprocess
    cmd = [
        ffmpeg_exe, "-y",
        "-ss", str(round(start_sec, 2)),
        "-to", str(round(end_sec, 2)),
        "-i", full_path,
        "-c", "copy",
        final_path
    ]
    subprocess.run(cmd, check=True)
    if os.path.exists(full_path):
        try:
            os.remove(full_path)
        except Exception:
            pass
    return final_path
