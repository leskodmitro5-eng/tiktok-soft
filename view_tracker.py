import logging
import requests
import yt_dlp
from datetime import datetime
from database import (
    db_add_tracked_video,
    db_update_tracked_video_views,
    db_get_user_tracked_videos
)

logger = logging.getLogger("ViewTracker")


def extract_platform_from_url(url: str) -> str:
    """Identifies the target platform from the URL string."""
    u = url.lower()
    if "tiktok.com" in u:
        return "tiktok"
    elif "youtube.com" in u or "youtu.be" in u:
        return "youtube_shorts"
    elif "instagram.com" in u:
        return "instagram"
    return "other"


def get_tiktok_online_stats(url: str) -> dict | None:
    """Extracts live metrics directly from TikTok using TikWM API."""
    api_url = "https://www.tikwm.com/api/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.tikwm.com/"
    }
    params = {"url": url, "hd": 1}
    try:
        r = requests.post(api_url, data=params, headers=headers, timeout=12)
        if r.status_code == 200:
            res = r.json()
            if res.get("code") == 0:
                d = res.get("data", {})
                return {
                    "platform": "tiktok",
                    "title": d.get("title") or "TikTok Video",
                    "views": int(d.get("play_count") or 0),
                    "likes": int(d.get("digg_count") or 0),
                    "comments": int(d.get("comment_count") or 0),
                    "url": url,
                    "checked_at": datetime.now().isoformat()
                }
    except Exception as e:
        logger.warning(f"TikWM error for {url}: {e}")
    return None


def get_youtube_online_stats(url: str) -> dict | None:
    """Extracts live metrics from YouTube Shorts / YouTube using yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            
            views = info.get("view_count") or 0
            likes = info.get("like_count") or 0
            comments = info.get("comment_count") or 0
            title = info.get("title") or "YouTube Shorts"
            
            return {
                "platform": "youtube_shorts",
                "title": title,
                "views": int(views),
                "likes": int(likes),
                "comments": int(comments),
                "url": url,
                "checked_at": datetime.now().isoformat()
            }
    except Exception as e:
        logger.warning(f"yt-dlp error for {url}: {e}")
    return None


def get_video_online_stats(url: str) -> dict | None:
    """Auto-routes URL to appropriate platform scraper."""
    platform = extract_platform_from_url(url)
    if platform == "tiktok":
        stats = get_tiktok_online_stats(url)
        if stats:
            return stats
        return get_youtube_online_stats(url)
    else:
        stats = get_youtube_online_stats(url)
        if stats:
            return stats
        return get_tiktok_online_stats(url)


def register_video_for_tracking(user_id: int, url: str, job_id: str = "") -> dict:
    """
    Fetches initial stats and registers video into SQLite database.
    """
    stats = get_video_online_stats(url)
    platform = stats.get("platform", extract_platform_from_url(url)) if stats else extract_platform_from_url(url)
    views = stats.get("views", 0) if stats else 0
    title = stats.get("title", "Video") if stats else "Video"
    
    track_id = db_add_tracked_video(
        user_id=user_id,
        job_id=job_id or f"manual_{track_id_hash(url)}",
        platform=platform,
        url=url,
        initial_views=views
    )
    
    return {
        "track_id": track_id,
        "platform": platform,
        "url": url,
        "views": views,
        "title": title,
        "likes": stats.get("likes", 0) if stats else 0,
        "comments": stats.get("comments", 0) if stats else 0
    }


def refresh_tracked_videos_for_user(user_id: int) -> list[dict]:
    """
    Refreshes live view counts for all videos monitored by the user.
    """
    items = db_get_user_tracked_videos(user_id, limit=20)
    updated = []
    
    for item in items:
        url = item.get("url")
        if not url:
            continue
        fresh = get_video_online_stats(url)
        if fresh and fresh.get("views") is not None:
            new_views = fresh["views"]
            db_update_tracked_video_views(item["id"], new_views)
            item["current_views"] = new_views
            item["title"] = fresh.get("title", "Video")
            item["likes"] = fresh.get("likes", 0)
            item["comments"] = fresh.get("comments", 0)
        updated.append(item)
        
    return updated


def track_id_hash(url: str) -> str:
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()[:8]
