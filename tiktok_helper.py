import os
import re
import logging
import requests
from pathlib import Path
import yt_dlp
import imageio_ffmpeg

logger = logging.getLogger("TikTokHelper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

TIKTOK_REGEX = re.compile(
    r"(https?://)?(?:[a-zA-Z0-9\-]+\.)?tiktok\.com/\S+"
)

def extract_tiktok_url(text: str) -> str | None:
    """Extracts the first valid TikTok URL from a message text."""
    if not text:
        return None
    match = TIKTOK_REGEX.search(text)
    if match:
        url = match.group(0)
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        return url
    return None

def get_tiktok_video_info(url: str) -> dict | None:
    """Extracts metadata for a TikTok video using TikWM API, with a fallback to yt-dlp."""
    api_url = "https://www.tikwm.com/api/"
    params = {"url": url, "hd": 1}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.tikwm.com/"
    }
    
    try:
        response = requests.post(api_url, data=params, headers=headers, timeout=15)
        response.raise_for_status()
        res_data = response.json()
        if res_data.get("code") == 0:
            data = res_data.get("data", {})
            return {
                "id": data.get("id", ""),
                "title": data.get("title", "TikTok Video"),
                "duration": float(data.get("duration", 0.0) or 0.0),
                "uploader": data.get("author", {}).get("unique_id", "TikTok User"),
                "url": url,
                "download_url": data.get("play", ""),
                "hd_download_url": data.get("hdplay", "")
            }
        else:
            logger.warning(f"TikWM API error: {res_data.get('msg')}")
    except Exception as e:
        logger.error(f"Failed to fetch TikTok info from TikWM: {e}")
        
    # Fallback to yt-dlp
    logger.info("Falling back to yt-dlp for TikTok info extraction...")
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "id": info.get("id", ""),
                "title": info.get("title", info.get("description", "TikTok Video")),
                "duration": float(info.get("duration", 0.0) or 0.0),
                "uploader": info.get("uploader", "TikTok User"),
                "url": url,
                "download_url": None
            }
    except Exception as ydl_err:
        logger.error(f"yt-dlp fallback also failed: {ydl_err}")
        
    return {
        "id": "tiktok_video",
        "title": "TikTok Video",
        "duration": 15.0,  # Fallback duration
        "uploader": "TikTok User",
        "url": url,
        "download_url": None
    }

def download_tiktok_video(url: str, output_path: str, progress_callback=None) -> str:
    """
    Downloads TikTok video. Tries TikWM first to get video without watermark,
    falls back to yt-dlp.
    """
    info = get_tiktok_video_info(url)
    download_url = None
    if info:
        # Use HD if available, otherwise standard play url
        download_url = info.get("hd_download_url") or info.get("download_url")
        
    if download_url:
        logger.info(f"Downloading TikTok video without watermark from TikWM: {download_url}")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.tikwm.com/"
            }
            response = requests.get(download_url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            
            with open(output_path, "wb") as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)
            logger.info(f"Downloaded via TikWM API to {output_path}")
            return output_path
        except Exception as err:
            logger.error(f"Failed to download via TikWM: {err}. Falling back to yt-dlp...")
            
    # Fallback to yt-dlp
    logger.info(f"Downloading TikTok video via yt-dlp: {url}")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    out_dir = str(Path(output_path).parent)
    base_name = Path(output_path).stem

    def ydl_hook(d):
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if total > 0:
                progress_callback(downloaded, total)

    ydl_opts = {
        "format": "best",
        "outtmpl": os.path.join(out_dir, f"{base_name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_exe,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [ydl_hook] if progress_callback else [],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    final_path = os.path.join(out_dir, f"{base_name}.mp4")
    if os.path.exists(final_path):
        return final_path
        
    for f in os.listdir(out_dir):
        if f.startswith(base_name):
            matched = os.path.join(out_dir, f)
            return matched

    raise FileNotFoundError(f"Downloaded TikTok video not found for {url}")
