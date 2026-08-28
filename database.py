import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("Database")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tiktok_soft.db"
HOOKS_JSON_FILE = BASE_DIR / "hook_learning_db.json"
HIGHLIGHTS_JSON_FILE = BASE_DIR / "highlight_learning_db.json"


def get_db_connection() -> sqlite3.Connection:
    """Creates a thread-safe connection to the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initializes the database schema and performs auto-migration if needed."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Hooks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hooks (
                job_id TEXT PRIMARY KEY,
                created_at TEXT,
                start_time REAL,
                end_time REAL,
                duration REAL,
                quote TEXT,
                reason TEXT,
                method TEXT,
                rating INTEGER,
                rated_at TEXT,
                raw_json TEXT
            )
        """)
        
        # 2. Highlights table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS highlights (
                clip_job_id TEXT PRIMARY KEY,
                created_at TEXT,
                title TEXT,
                start_time REAL,
                end_time REAL,
                duration REAL,
                visual_action_score INTEGER,
                audio_emotion_score INTEGER,
                viral_coefficient REAL,
                target_platform TEXT,
                has_hardcoded_subs INTEGER,
                suggested_cta TEXT,
                reason TEXT,
                hook_start REAL,
                hook_end REAL,
                rating INTEGER,
                rated_at TEXT,
                raw_json TEXT
            )
        """)

        # 3. Jobs table (Queue & History)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                user_id INTEGER,
                media_type TEXT,
                title TEXT,
                status TEXT,
                duration_sec REAL,
                created_at TEXT,
                completed_at TEXT,
                error_message TEXT
            )
        """)

        # 4. Users & SaaS Paywall table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                credits_balance INTEGER DEFAULT 3,
                tier TEXT DEFAULT 'free',
                subscription_expires_at TEXT,
                referrer_id INTEGER,
                total_spent_stars INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)

        # 5. Referrals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER UNIQUE,
                reward_given INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)

        # 6. Tracked Videos (Viral View Tracker)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tracked_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                job_id TEXT,
                platform TEXT,
                url TEXT,
                initial_views INTEGER DEFAULT 0,
                current_views INTEGER DEFAULT 0,
                last_checked_at TEXT,
                created_at TEXT
            )
        """)
        
        conn.commit()

    migrate_from_json_if_needed()


def migrate_from_json_if_needed() -> None:
    """Seamlessly imports existing JSON learning databases into SQLite if empty."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check hooks
        cursor.execute("SELECT COUNT(*) as cnt FROM hooks")
        hooks_cnt = cursor.fetchone()["cnt"]
        if hooks_cnt == 0 and HOOKS_JSON_FILE.exists():
            try:
                with open(HOOKS_JSON_FILE, "r", encoding="utf-8") as f:
                    hooks_data = json.load(f)
                
                migrated = 0
                for item in hooks_data:
                    job_id = item.get("job_id")
                    if not job_id:
                        continue
                    h_info = item.get("hook_info", {})
                    cursor.execute("""
                        INSERT OR REPLACE INTO hooks (
                            job_id, created_at, start_time, end_time, duration,
                            quote, reason, method, rating, rated_at, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        job_id,
                        item.get("timestamp", datetime.now().isoformat()),
                        h_info.get("start", 0.0),
                        h_info.get("end", 0.0),
                        h_info.get("duration", 0.0),
                        h_info.get("quote", ""),
                        h_info.get("reason", ""),
                        h_info.get("method", "gemini"),
                        item.get("rating"),
                        item.get("rated_at"),
                        json.dumps(item, ensure_ascii=False)
                    ))
                    migrated += 1
                conn.commit()
                logger.info(f"Successfully migrated {migrated} hook records from JSON to SQLite.")
            except Exception as e:
                logger.error(f"Error migrating hooks JSON to SQLite: {e}")

        # Check highlights
        cursor.execute("SELECT COUNT(*) as cnt FROM highlights")
        hl_cnt = cursor.fetchone()["cnt"]
        if hl_cnt == 0 and HIGHLIGHTS_JSON_FILE.exists():
            try:
                with open(HIGHLIGHTS_JSON_FILE, "r", encoding="utf-8") as f:
                    hl_data = json.load(f)
                
                migrated = 0
                for item in hl_data:
                    clip_job_id = item.get("clip_job_id")
                    if not clip_job_id:
                        continue
                    hl_info = item.get("highlight_info", {})
                    cursor.execute("""
                        INSERT OR REPLACE INTO highlights (
                            clip_job_id, created_at, title, start_time, end_time, duration,
                            visual_action_score, audio_emotion_score, viral_coefficient,
                            target_platform, has_hardcoded_subs, suggested_cta, reason,
                            hook_start, hook_end, rating, rated_at, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        clip_job_id,
                        item.get("timestamp", datetime.now().isoformat()),
                        hl_info.get("title", ""),
                        hl_info.get("start", 0.0),
                        hl_info.get("end", 0.0),
                        hl_info.get("duration", 0.0),
                        hl_info.get("visual_action_score", 8),
                        hl_info.get("audio_emotion_score", 8),
                        hl_info.get("viral_coefficient", 8.0),
                        hl_info.get("target_platform", "tiktok"),
                        1 if hl_info.get("has_hardcoded_subs") else 0,
                        hl_info.get("suggested_cta", ""),
                        hl_info.get("reason", ""),
                        hl_info.get("hook_start", 0.0),
                        hl_info.get("hook_end", 0.0),
                        item.get("rating"),
                        item.get("rated_at"),
                        json.dumps(item, ensure_ascii=False)
                    ))
                    migrated += 1
                conn.commit()
                logger.info(f"Successfully migrated {migrated} highlight records from JSON to SQLite.")
            except Exception as e:
                logger.error(f"Error migrating highlights JSON to SQLite: {e}")


# --- Users & SaaS Billing Helper Methods ---

def db_get_or_create_user(user_id: int, username: str = "", first_name: str = "", referrer_id: int = None) -> dict:
    """
    Retrieves or registers a new user.
    Progressive referral system:
    - Friend #1: friend gets +1 extra bonus (4 total)
    - Friend #2: friend gets +2 extra bonus (5 total)
    ...
    - Friend #10: friend gets +10 extra bonus (13 total)
    """
    now_str = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
        if row:
            if (username and row["username"] != username) or (first_name and row["first_name"] != first_name):
                cursor.execute("UPDATE users SET username = ?, first_name = ? WHERE user_id = ?", (username, first_name, user_id))
                conn.commit()
            return dict(row)

        ref_valid = referrer_id if (referrer_id and referrer_id != user_id) else None
        
        if ref_valid:
            cursor.execute("SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ?", (ref_valid,))
            friend_number = cursor.fetchone()["cnt"] + 1
            extra_friend_bonus = min(10, friend_number)
            start_credits = 3 + extra_friend_bonus
        else:
            start_credits = 3

        cursor.execute("""
            INSERT INTO users (
                user_id, username, first_name, credits_balance, tier, subscription_expires_at,
                referrer_id, total_spent_stars, created_at
            ) VALUES (?, ?, ?, ?, 'free', NULL, ?, 0, ?)
        """, (user_id, username, first_name, start_credits, ref_valid, now_str))

        if ref_valid:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO referrals (referrer_id, referred_id, reward_given, created_at)
                    VALUES (?, ?, 0, ?)
                """, (ref_valid, user_id, now_str))
            except Exception:
                pass

        conn.commit()
        logger.info(f"Created new user #{user_id} (@{username}) with {start_credits} credits. Referrer: {ref_valid}")

        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return dict(cursor.fetchone())


def db_get_user(user_id: int) -> dict | None:
    """Returns user profile dictionary."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def db_has_active_subscription(user: dict) -> bool:
    """Checks whether user has an active unlimited monthly subscription."""
    if not user:
        return False
    tier = user.get("tier", "free")
    if tier in ("admin", "unlimited"):
        expires = user.get("subscription_expires_at")
        if not expires:
            return True
        try:
            exp_date = datetime.fromisoformat(expires)
            return exp_date > datetime.now()
        except Exception:
            return False
    return False


def db_deduct_credit(user_id: int, is_admin: bool = False) -> tuple[bool, int]:
    """
    Deducts 1 credit from user balance. Admins and active unlimited subscribers are exempt.
    Handles progressive reward to referrer on first video:
    - 1st friend -> +2 credits to referrer
    - 2nd friend -> +3 credits to referrer
    - ...
    - 9th friend -> +10 credits to referrer
    - 10th friend -> 🔥 30 DAYS UNLIMITED SUBSCRIPTION to referrer!
    - >10th friend -> +10 credits to referrer
    """
    if is_admin:
        return True, 9999

    user = db_get_user(user_id)
    if not user:
        user = db_get_or_create_user(user_id)

    if db_has_active_subscription(user):
        return True, 9999

    credits = user.get("credits_balance", 0)
    if credits <= 0:
        return False, 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET credits_balance = credits_balance - 1 WHERE user_id = ?", (user_id,))
        
        # Check if this user was referred and this is their first usage to reward referrer
        if user.get("referrer_id"):
            cursor.execute("SELECT * FROM referrals WHERE referred_id = ? AND reward_given = 0", (user_id,))
            ref_row = cursor.fetchone()
            if ref_row:
                ref_id = ref_row["referrer_id"]
                
                # Count how many active referrals this referrer already has
                cursor.execute("SELECT COUNT(*) as active_cnt FROM referrals WHERE referrer_id = ? AND reward_given = 1", (ref_id,))
                active_before = cursor.fetchone()["active_cnt"]
                current_active_rank = active_before + 1

                if current_active_rank == 10:
                    # 10th Friend -> 30 DAYS UNLIMITED
                    exp_date = (datetime.now() + timedelta(days=30)).isoformat()
                    cursor.execute("""
                        UPDATE users 
                        SET tier = 'unlimited', subscription_expires_at = ?
                        WHERE user_id = ?
                    """, (exp_date, ref_id))
                    logger.info(f"🎉 Referrer #{ref_id} unlocked 30 DAYS UNLIMITED for reaching 10 active referrals!")
                else:
                    reward_bonus = min(10, current_active_rank + 1)
                    cursor.execute("UPDATE users SET credits_balance = credits_balance + ? WHERE user_id = ?", (reward_bonus, ref_id))
                    logger.info(f"Referrer #{ref_id} received +{reward_bonus} credits for active friend #{current_active_rank} (user #{user_id})")

                cursor.execute("UPDATE referrals SET reward_given = 1 WHERE id = ?", (ref_row["id"],))

        conn.commit()

    return True, credits - 1


def db_add_credits(user_id: int, amount: int, spent_stars: int = 0) -> int:
    """Adds credits to user and handles referrer commission bonus."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET credits_balance = credits_balance + ?,
                total_spent_stars = total_spent_stars + ?
            WHERE user_id = ?
        """, (amount, spent_stars, user_id))

        # Reward referrer with 20% bonus credits if applicable
        cursor.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row and row["referrer_id"] and amount >= 10:
            ref_bonus = max(1, int(amount * 0.20))
            cursor.execute("UPDATE users SET credits_balance = credits_balance + ? WHERE user_id = ?", (ref_bonus, row["referrer_id"]))
            logger.info(f"Referrer #{row['referrer_id']} received 20% bonus (+{ref_bonus} credits) from purchase of user #{user_id}")

        conn.commit()
        
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()["credits_balance"]


def db_set_subscription(user_id: int, tier: str = "unlimited", days: int = 30, spent_stars: int = 0) -> str:
    """Activates unlimited monthly subscription for user (100 Stars / 10 referrals)."""
    exp_date = datetime.now() + timedelta(days=days)
    exp_str = exp_date.isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET tier = ?,
                subscription_expires_at = ?,
                total_spent_stars = total_spent_stars + ?
            WHERE user_id = ?
        """, (tier, exp_str, spent_stars, user_id))
        conn.commit()
    return exp_str


def db_get_referral_stats(user_id: int) -> dict:
    """Returns progressive referral stats for user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_invited FROM referrals WHERE referrer_id = ?", (user_id,))
        total_invited = cursor.fetchone()["total_invited"]

        cursor.execute("SELECT COUNT(*) as active_invited FROM referrals WHERE referrer_id = ? AND reward_given = 1", (user_id,))
        active_invited = cursor.fetchone()["active_invited"]

        # Calculate next rewards
        next_rank = active_invited + 1
        if next_rank < 10:
            next_reward_you = f"+{next_rank + 1} відео"
            next_bonus_friend = f"+{next_rank} відео"
        elif next_rank == 10:
            next_reward_you = "🔥 БЕЗЛІМІТ НА 1 МІСЯЦЬ (30 днів)!"
            next_bonus_friend = "+10 відео"
        else:
            next_reward_you = "+10 відео"
            next_bonus_friend = "+10 відео"

        return {
            "total_invited": total_invited,
            "active_invited": active_invited,
            "earned_credits": active_invited * 2,
            "next_rank": next_rank,
            "next_reward_you": next_reward_you,
            "next_bonus_friend": next_bonus_friend,
            "unlocked_unlimited": (active_invited >= 10)
        }


# --- Tracked Videos (View Tracker) Helper Methods ---

def db_add_tracked_video(user_id: int, job_id: str, platform: str, url: str, initial_views: int = 0) -> int:
    """Registers a published video for automated viral views tracking."""
    now_str = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tracked_videos (user_id, job_id, platform, url, initial_views, current_views, last_checked_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, job_id, platform, url, initial_views, initial_views, now_str, now_str))
        conn.commit()
        return cursor.lastrowid


def db_update_tracked_video_views(tracked_id: int, views: int) -> None:
    """Updates latest scraped view count for a tracked video."""
    now_str = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tracked_videos
            SET current_views = ?, last_checked_at = ?
            WHERE id = ?
        """, (views, now_str, tracked_id))
        conn.commit()


def db_get_user_tracked_videos(user_id: int, limit: int = 10) -> list[dict]:
    """Returns list of tracked videos for user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM tracked_videos
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        return [dict(r) for r in cursor.fetchall()]


def db_delete_tracked_video(tracked_id: int, user_id: int) -> bool:
    """Deletes a tracked video from monitoring for a given user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tracked_videos WHERE id = ? AND user_id = ?", (tracked_id, user_id))
        conn.commit()
        return cursor.rowcount > 0


def db_get_viral_tracked_videos_context(min_views: int = 5000) -> str:
    """Fetches high-performing tracked videos to inject into Gemini prompts as proven viral hits."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tv.*, h.title, h.reason, h.viral_coefficient
            FROM tracked_videos tv
            LEFT JOIN highlights h ON tv.job_id = h.clip_job_id
            WHERE tv.current_views >= ?
            ORDER BY tv.current_views DESC
            LIMIT 5
        """, (min_views,))
        rows = cursor.fetchall()
        
        if not rows:
            return ""
        
        lines = ["\n### 🚀 PROVEN REAL-WORLD VIRAL HITS (LEARN FROM LIVE VIEW STATS):"]
        for r in rows:
            lines.append(f"- [{r['platform'].upper()} - {r['current_views']:,} VIEWS] Title: \"{r['title'] or 'Highlight'}\" | Reason: {r['reason'] or 'High engagement'}")
        return "\n".join(lines)


# --- Hooks SQLite Helper Methods ---

def db_save_hook_decision(job_id: str, segments: list[dict], hook_info: dict) -> None:
    """Saves a hook decision awaiting rating."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        raw_dict = {
            "job_id": job_id,
            "timestamp": datetime.now().isoformat(),
            "hook_info": hook_info,
            "sample_segments": segments[:15] if segments else []
        }
        cursor.execute("""
            INSERT OR REPLACE INTO hooks (
                job_id, created_at, start_time, end_time, duration,
                quote, reason, method, rating, rated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
        """, (
            job_id,
            datetime.now().isoformat(),
            hook_info.get("start", 0.0),
            hook_info.get("end", 0.0),
            hook_info.get("duration", 0.0),
            hook_info.get("quote", ""),
            hook_info.get("reason", ""),
            hook_info.get("method", "gemini"),
            json.dumps(raw_dict, ensure_ascii=False)
        ))
        conn.commit()


def db_record_hook_rating(job_id: str, rating: int) -> dict | None:
    """Records user rating (1-10) for a hook."""
    rating = max(1, min(10, int(rating)))
    now_str = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM hooks WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        
        cursor.execute("""
            UPDATE hooks 
            SET rating = ?, rated_at = ?
            WHERE job_id = ?
        """, (rating, now_str, job_id))
        conn.commit()

        return {
            "job_id": row["job_id"],
            "rating": rating,
            "rated_at": now_str,
            "hook_info": {
                "start": row["start_time"],
                "end": row["end_time"],
                "duration": row["duration"],
                "quote": row["quote"],
                "reason": row["reason"],
                "method": row["method"]
            }
        }


def db_get_hook_learning_stats() -> dict:
    """Calculates statistics on hook ratings."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_jobs FROM hooks")
        total_jobs = cursor.fetchone()["total_jobs"]

        cursor.execute("SELECT rating FROM hooks WHERE rating IS NOT NULL")
        rows = cursor.fetchall()
        
        total_rated = len(rows)
        avg_rating = round(sum(r["rating"] for r in rows) / total_rated, 1) if total_rated > 0 else 0.0

        distribution = {i: 0 for i in range(1, 11)}
        for r in rows:
            val = r["rating"]
            if val in distribution:
                distribution[val] += 1

        return {
            "total_rated": total_rated,
            "avg_rating": avg_rating,
            "total_jobs": total_jobs,
            "distribution": distribution
        }


def db_get_all_rated_hooks(limit: int = 15) -> list[dict]:
    """Returns top rated hooks ordered by rating DESC, rated_at DESC."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM hooks 
            WHERE rating IS NOT NULL 
            ORDER BY rating DESC, rated_at DESC 
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        res = []
        for r in rows:
            res.append({
                "job_id": r["job_id"],
                "rating": r["rating"],
                "rated_at": r["rated_at"],
                "hook_info": {
                    "quote": r["quote"],
                    "duration": r["duration"],
                    "reason": r["reason"]
                }
            })
        return res


# --- Highlights SQLite Helper Methods ---

def db_save_highlight_decision(clip_job_id: str, highlight_info: dict, segments: list[dict] = None) -> None:
    """Saves a highlight decision awaiting rating."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        raw_dict = {
            "clip_job_id": clip_job_id,
            "timestamp": datetime.now().isoformat(),
            "highlight_info": highlight_info,
            "sample_segments": segments[:15] if segments else []
        }
        cursor.execute("""
            INSERT OR REPLACE INTO highlights (
                clip_job_id, created_at, title, start_time, end_time, duration,
                visual_action_score, audio_emotion_score, viral_coefficient,
                target_platform, has_hardcoded_subs, suggested_cta, reason,
                hook_start, hook_end, rating, rated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
        """, (
            clip_job_id,
            datetime.now().isoformat(),
            highlight_info.get("title", ""),
            highlight_info.get("start", 0.0),
            highlight_info.get("end", 0.0),
            highlight_info.get("duration", 0.0),
            highlight_info.get("visual_action_score", 8),
            highlight_info.get("audio_emotion_score", 8),
            highlight_info.get("viral_coefficient", 8.0),
            highlight_info.get("target_platform", "tiktok"),
            1 if highlight_info.get("has_hardcoded_subs") else 0,
            highlight_info.get("suggested_cta", ""),
            highlight_info.get("reason", ""),
            highlight_info.get("hook_start", 0.0),
            highlight_info.get("hook_end", 0.0),
            json.dumps(raw_dict, ensure_ascii=False)
        ))
        conn.commit()


def db_record_highlight_rating(clip_job_id: str, rating: int) -> dict | None:
    """Records user rating (1-10) for a highlight clip."""
    rating = max(1, min(10, int(rating)))
    now_str = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM highlights WHERE clip_job_id = ?", (clip_job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        
        cursor.execute("""
            UPDATE highlights 
            SET rating = ?, rated_at = ?
            WHERE clip_job_id = ?
        """, (rating, now_str, clip_job_id))
        conn.commit()

        return {
            "clip_job_id": row["clip_job_id"],
            "rating": rating,
            "rated_at": now_str,
            "highlight_info": {
                "title": row["title"],
                "start": row["start_time"],
                "end": row["end_time"],
                "duration": row["duration"],
                "viral_coefficient": row["viral_coefficient"],
                "reason": row["reason"]
            }
        }


def db_get_highlight_learning_stats() -> dict:
    """Calculates statistics on highlight ratings."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_jobs FROM highlights")
        total_jobs = cursor.fetchone()["total_jobs"]

        cursor.execute("SELECT rating FROM highlights WHERE rating IS NOT NULL")
        rows = cursor.fetchall()
        
        total_rated = len(rows)
        avg_rating = round(sum(r["rating"] for r in rows) / total_rated, 1) if total_rated > 0 else 0.0

        distribution = {i: 0 for i in range(1, 11)}
        for r in rows:
            val = r["rating"]
            if val in distribution:
                distribution[val] += 1

        return {
            "total_rated": total_rated,
            "avg_rating": avg_rating,
            "total_jobs": total_jobs,
            "distribution": distribution
        }


def db_get_all_rated_highlights(limit: int = 15) -> list[dict]:
    """Returns top rated highlights ordered by rating DESC, rated_at DESC."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM highlights 
            WHERE rating IS NOT NULL 
            ORDER BY rating DESC, rated_at DESC 
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        res = []
        for r in rows:
            res.append({
                "clip_job_id": r["clip_job_id"],
                "rating": r["rating"],
                "rated_at": r["rated_at"],
                "highlight_info": {
                    "title": r["title"],
                    "start": r["start_time"],
                    "end": r["end_time"],
                    "duration": r["duration"],
                    "viral_coefficient": r["viral_coefficient"],
                    "reason": r["reason"]
                }
            })
        return res


# --- Jobs Table Helper Methods ---

def db_create_job(job_id: str, user_id: int, media_type: str, title: str, duration_sec: float = 0.0) -> None:
    """Records a new job in the database with status 'QUEUED'."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO jobs (
                job_id, user_id, media_type, title, status, duration_sec, created_at, completed_at, error_message
            ) VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, NULL, NULL)
        """, (
            job_id,
            user_id,
            media_type,
            title,
            duration_sec,
            datetime.now().isoformat()
        ))
        conn.commit()


def db_update_job_status(job_id: str, status: str, error_message: str = None) -> None:
    """Updates the status and completed_at timestamp of a job."""
    now_str = datetime.now().isoformat() if status in ("COMPLETED", "FAILED") else None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE jobs
            SET status = ?, completed_at = COALESCE(?, completed_at), error_message = ?
            WHERE job_id = ?
        """, (status, now_str, error_message, job_id))
        conn.commit()


# Auto-init schema on module import
init_db()
