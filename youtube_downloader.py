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


try:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    ffmpeg_dir = os.path.dirname(ffmpeg_exe)
    if ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    ffmpeg_exe = "ffmpeg"

YOUTUBE_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["android", "ios", "web_creator", "mweb", "web"]
    }
}


def get_youtube_video_info(url: str) -> dict:
    """Extracts metadata for a YouTube video without downloading the stream."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {
            "id": info.get("id", ""),
            "title": info.get("title", "YouTube Video"),
            "duration": float(info.get("duration", 0.0) or 0.0),
            "uploader": info.get("uploader", "YouTube"),
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

    ydl_opts = {
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, f"{base_name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_exe,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "nopart": True,
        "overwrites": True,
        "windowsfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
        "progress_hooks": [ydl_hook] if progress_callback else [],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.warning(f"yt_dlp download notice: {e}. Attempting file recovery...")
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

    ydl_opts = {
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, f"{base_name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_exe,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "nopart": True,
        "overwrites": True,
        "windowsfilenames": True,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
        "download_ranges": download_ranges,
        "force_keyframes_at_cuts": True,
        "progress_hooks": [ydl_hook] if progress_callback else [],
    }


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
