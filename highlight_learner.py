import os
import logging
from datetime import datetime
from pathlib import Path

from database import (
    db_save_highlight_decision,
    db_record_highlight_rating,
    db_get_highlight_learning_stats,
    db_get_all_rated_highlights
)

logger = logging.getLogger("HighlightLearner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

BASE_DIR = Path(__file__).resolve().parent

# Complete 1 to 10 Scale Definitions for Long Video Highlights / Clips
HIGHLIGHT_RATING_DEFINITIONS = {
    10: {
        "title": "🌟 10/10 — ІДЕАЛЬНА НАРІЗКА",
        "desc": "Ідеальний вибір кульмінації: чіткий початок, вибухова динаміка/емоція, логічне завершення панчлайном. Еталон для нарізок!",
        "gemini_directive": "GOLD STANDARD HIGHLIGHT. Target this exact pacing, climax payoff, and completed dialogue."
    },
    9: {
        "title": "🔥 9/10 — ТОПОВА ВІРУСНА НАРІЗКА",
        "desc": "Дуже висока динаміка, сильний сюжетний або емоційний сплеск, відмінне утримання уваги.",
        "gemini_directive": "HIGH TARGET HIGHLIGHT. Replicate this segment selection and emotional density."
    },
    8: {
        "title": "✨ 8/10 — ДУЖЕ ВДАЛИЙ КЛІП",
        "desc": "Цікавий момент, змістовний діалог/геймплей, гарне завершення думки.",
        "gemini_directive": "POSITIVE TARGET. Good highlight choice, maintain this style."
    },
    7: {
        "title": "👍 7/10 — ХОРОША, АЛЕ НЕ ТОП",
        "desc": "Непоганий фрагмент, але був потенціал обрати більш динамічний таймінг або точніші межі.",
        "gemini_directive": "DECENT. Acceptable, but tighten the start/end boundaries to increase momentum."
    },
    6: {
        "title": "👌 6/10 — ВИЩЕ СЕРЕДНЬОГО",
        "desc": "Нормальний шматок відео, але бракує сильної кульмінації чи гостроти.",
        "gemini_directive": "MODERATE. Needs stronger climax or more dramatic emotional punchline."
    },
    5: {
        "title": "⚖️ 5/10 — СЕРЕДНІЙ / ПОСЕРЕДНІЙ",
        "desc": "Звичайний геймплей або монотонний діалог, слабка вірусність.",
        "gemini_directive": "MEDIOCRE. Too plain. Filter out ordinary calm dialogue."
    },
    4: {
        "title": "⚠️ 4/10 — НИЖЧЕ СЕРЕДНЬОГО",
        "desc": "Затягнутий початок або нудна середина, глядач втратить інтерес.",
        "gemini_directive": "POOR. Lacks dynamic hooks or events. Avoid slow-paced segments."
    },
    3: {
        "title": "❌ 3/10 — СЛАБКИЙ КЛІП / ОБРИВ",
        "desc": "Невдало обраний момент або обрив фрази/думки на півслові.",
        "gemini_directive": "WEAK. Avoid cutting mid-sentence. Ensure clear setup and resolution."
    },
    2: {
        "title": "🚫 2/10 — ДУЖЕ ПОГАНИЙ",
        "desc": "Спокійна рутина без подій, нуль інтересу, обірваний зміст.",
        "gemini_directive": "VERY BAD. Routine or uninteresting segment. Strictly avoid."
    },
    1: {
        "title": "⛔️ 1/10 — КАТАСТРОФІЧНИЙ ПРОВАЛ",
        "desc": "Повний промах: монотонна балаканина ні про що, нульова вірусність. СУВОРО ЗАБОРОНЕНО!",
        "gemini_directive": "CRITICAL FAILURE. NEVER pick such boring or fragmented pieces. Prioritize highest action/emotion peaks."
    }
}


def save_highlight_decision(clip_job_id: str, highlight_info: dict, sample_segments: list[dict] = None) -> None:
    """Saves a newly generated highlight slice decision into SQLite awaiting user rating."""
    db_save_highlight_decision(clip_job_id, highlight_info, sample_segments)
    logger.info(f"Recorded highlight decision for clip {clip_job_id} ({highlight_info.get('title')}) in SQLite")


def record_highlight_rating(clip_job_id: str, rating: int) -> dict | None:
    """Records the 1-10 rating from the user in SQLite for a specific highlight clip."""
    res = db_record_highlight_rating(clip_job_id, rating)
    if res:
        logger.info(f"Updated highlight rating for clip {clip_job_id} -> {rating}/10 in SQLite")
    return res


def get_high_and_low_highlight_scores_context() -> tuple[str, str]:
    """
    Retrieves the latest high-score (8-10 STARS) and low-score (1-4 STARS) highlight clip ratings
    from SQLite and formats them for Gemini dynamic prompt injection.
    """
    rated_items = db_get_all_rated_highlights(limit=30)

    high_items = [item for item in rated_items if item.get("rating") in (8, 9, 10)]
    low_items = [item for item in rated_items if item.get("rating") in (1, 2, 3, 4)]

    if high_items:
        high_lines = ["HISTORICAL HIGH SCORES FOR CLIPS (8-10 STARS) - REPLICATE THESE HIGHLIGHT PATTERNS:"]
        for item in high_items[:6]:
            h = item.get("highlight_info", {})
            title = h.get("title", "Highlight")
            reason = h.get("reason", "")
            dur = h.get("duration", 40.0)
            score = item.get("rating", 10)
            high_lines.append(f"- [{score}/10 🌟] Clip '{title}' ({dur:.1f}s) | Reason: {reason}")
        historical_high_scores = "\n".join(high_lines)
    else:
        historical_high_scores = "HISTORICAL HIGH SCORES FOR CLIPS: (Target high action/emotion peaks, completed storyline/punchline, 30-55s duration)."

    if low_items:
        low_lines = ["HISTORICAL LOW SCORES FOR CLIPS (1-4 STARS) - AVOID THESE HIGHLIGHT MISTAKES:"]
        for item in low_items[:6]:
            h = item.get("highlight_info", {})
            title = h.get("title", "Highlight")
            reason = h.get("reason", "")
            dur = h.get("duration", 40.0)
            score = item.get("rating", 1)
            low_lines.append(f"- [{score}/10 ⛔️] Clip '{title}' ({dur:.1f}s) | AI Reason was: {reason} -> (AVOID: Boring talk, incomplete sentence, low energy)")
        historical_low_scores = "\n".join(low_lines)
    else:
        historical_low_scores = "HISTORICAL LOW SCORES FOR CLIPS: (Strictly avoid calm chatting, routine gameplay without climax, or cutting mid-word)."

    return historical_high_scores, historical_low_scores


def get_highlight_learning_stats() -> dict:
    """Returns comprehensive statistics on highlight ratings from SQLite."""
    return db_get_highlight_learning_stats()
