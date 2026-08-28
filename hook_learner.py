import os
import logging
from datetime import datetime
from pathlib import Path

from database import (
    db_save_hook_decision,
    db_record_hook_rating,
    db_get_hook_learning_stats,
    db_get_all_rated_hooks
)

logger = logging.getLogger("HookLearner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

BASE_DIR = Path(__file__).resolve().parent

# Complete 1 to 10 Gradient Scale Definitions
RATING_LEVEL_DEFINITIONS = {
    10: {
        "title": "🌟 10/10 — ІДЕАЛЬНИЙ ЕТАЛОН",
        "desc": "Абсолютний пік емоцій (дикий вереск/крик, вибух сміху, шалений угар). СТАВИТИ ТАКІ ХУКИ ЗАВЖДИ!",
        "gemini_directive": "GOLD STANDARD. Always seek and prioritize hooks with this exact energy and explosion."
    },
    9: {
        "title": "🔥 9/10 — ТОПОВИЙ ВІРУСНИЙ ХУК",
        "desc": "Дуже сильний емоційний сплеск, яскравий крик чи сміх, максимальне утримання уваги.",
        "gemini_directive": "HIGH TARGET. Excellent hook choice, prioritize moments like this."
    },
    8: {
        "title": "✨ 8/10 — ДУЖЕ ВДАЛИЙ ХУК",
        "desc": "Якісний емоційний момент або чітка вірусна фраза, що чіпляє з 0 секунди.",
        "gemini_directive": "POSITIVE TARGET. Good choice, emulate this style."
    },
    7: {
        "title": "👍 7/10 — ХОРОШИЙ, АЛЕ НЕ ТОП",
        "desc": "Непоганий момент, але був потенціал знайти ще гучніший або точніший відрізок.",
        "gemini_directive": "DECENT. Acceptable, but try to find even more explosive timing."
    },
    6: {
        "title": "👌 6/10 — ВИЩЕ СЕРЕДНЬОГО",
        "desc": "Є легка емоція чи подія, але не вистачає драйву та вибуховості.",
        "gemini_directive": "MODERATE. Needs more emotional intensity and punch."
    },
    5: {
        "title": "⚖️ 5/10 — СЕРЕДНІЙ / ПОСЕРЕДНІЙ",
        "desc": "Звичайна репліка, не викликає сильного бажання додивитись відео далі.",
        "gemini_directive": "MEDIOCRE. Too plain. Do not settle for ordinary dialogue."
    },
    4: {
        "title": "⚠️ 4/10 — НИЖЧЕ СЕРЕДНЬОГО",
        "desc": "Спокійна балаканина або натягнутий момент, бракує вірусності.",
        "gemini_directive": "POOR. Lacks viral hook energy. Avoid similar calm dialogue."
    },
    3: {
        "title": "❌ 3/10 — СЛАБКИЙ / НЕВДАЛИЙ",
        "desc": "Нудний фрагмент, невдалий таймінг або повний промах повз суть.",
        "gemini_directive": "WEAK/FAILED. User disliked this choice. Significantly increase threshold."
    },
    2: {
        "title": "🚫 2/10 — ДУЖЕ ПОГАНИЙ",
        "desc": "Спокійна монотонна мова, нуль емоцій, глядач відразу свайпне відео.",
        "gemini_directive": "VERY BAD. Monotonous/calm dialogue. Strictly avoid."
    },
    1: {
        "title": "⛔️ 1/10 — КАТАСТРОФІЧНО ПОГАНО / ПОВНИЙ ПРОВАЛ",
        "desc": "Банальні слова ні про що ('ага', 'ну', звичайні спокійні фрази). СУВОРО ЗАБОРОНЕНО!",
        "gemini_directive": "CRITICAL FAILURE. NEVER repeat this mistake. If speech is only calm/banal, fallback immediately to loudest audio energy."
    }
}


def save_hook_decision(job_id: str, segments: list[dict], hook_info: dict) -> None:
    """Saves a newly generated hook decision into SQLite awaiting user rating."""
    db_save_hook_decision(job_id, segments, hook_info)
    logger.info(f"Recorded hook decision for job {job_id} in SQLite")


def record_user_rating(job_id: str, rating: int) -> dict | None:
    """Records the 1-10 rating from the user in SQLite for a specific job."""
    res = db_record_hook_rating(job_id, rating)
    if res:
        logger.info(f"Updated rating for job {job_id} -> {rating}/10 in SQLite")
    return res


def get_learning_context() -> str:
    """
    Constructs a comprehensive 1-to-10 gradient continuous learning context for Google Gemini from SQLite.
    """
    rated_items = db_get_all_rated_hooks(limit=15)
    
    lines = [
        "\n### 🎯 FULL GRADIENT USER PREFERENCE LEARNING MATRIX (SCALE 1 TO 10):",
        "The user rates hook quality on a continuous 1-10 scale. You MUST calibrate your hook selection according to this exact rating scale:",
        "- Scores 8-10: EXTREMELY HIGH VALUE. Explosive screams, loud shouting, sudden hysterical laughter, chaotic panic yells.",
        "- Scores 6-7: MODERATE VALUE. Good dialogue, but user prefers more explosive emotional peaks.",
        "- Scores 1-5: SEVERE PENALTY / UNACCEPTABLE. Calm chatting, mundane phrases ('ага', 'ну тут можно', ordinary callouts).",
        "CRITICAL DIRECTIVE: If the audio ONLY contains calm talk or mundane phrases, DO NOT select a calm phrase as a hook (which gets rated 1/10). In such cases, select the loudest shout/laugh or let the system find the audio peak!"
    ]

    if rated_items:
        lines.append("\n### 📜 EXACT HISTORICAL EVALUATIONS RATED BY THE USER (LEARN FROM THIS CONTINUOUS SPECTRUM):")
        for item in rated_items:
            score = item["rating"]
            h = item.get("hook_info", {})
            quote = h.get("quote", "Unknown")
            dur = h.get("duration", 2.0)
            reason = h.get("reason", "")
            definition = RATING_LEVEL_DEFINITIONS.get(score, {})
            directive = definition.get("gemini_directive", "")
            
            tag = "🌟 IDEAL (10/10)" if score == 10 else \
                  "🔥 TOP (9/10)" if score == 9 else \
                  "✨ GOOD (8/10)" if score == 8 else \
                  "👍 DECENT (7/10)" if score == 7 else \
                  "👌 MODERATE (6/10)" if score == 6 else \
                  "⚖️ MEDIOCRE (5/10)" if score == 5 else \
                  "⚠️ POOR (4/10)" if score == 4 else \
                  "❌ WEAK (3/10)" if score == 3 else \
                  "🚫 BAD (2/10)" if score == 2 else \
                  "⛔️ CRITICAL FAIL (1/10)"

            lines.append(f"- [{tag}] Quote: \"{quote}\" ({dur}s) | Score: {score}/10 | AI Reason was: \"{reason}\" -> [RULE: {directive}]")

        lines.append("\nALWAYS maximize the predicted user rating towards 10/10. Strictly reject choices that resemble 1-5 scores.\n")

    return "\n".join(lines)


def get_high_and_low_scores_context() -> tuple[str, str]:
    """
    Retrieves high-score and low-score ratings from SQLite for prompt ledger.
    """
    rated_items = db_get_all_rated_hooks(limit=30)

    high_items = [item for item in rated_items if item.get("rating") in (8, 9, 10)]
    low_items = [item for item in rated_items if item.get("rating") in (1, 2, 3, 4)]

    if high_items:
        high_lines = ["HISTORICAL HIGH SCORES (8-10 STARS) - REPLICATE THIS PATTERN:"]
        for item in high_items[:6]:
            h = item.get("hook_info", {})
            quote = h.get("quote", "Unknown")
            reason = h.get("reason", "")
            score = item.get("rating", 10)
            high_lines.append(f"- [{score}/10 🌟] Quote/Event: \"{quote}\" | Reason: {reason}")
        historical_high_scores = "\n".join(high_lines)
    else:
        historical_high_scores = "HISTORICAL HIGH SCORES: (No ratings recorded yet. Target maximum explosive screams/laughter and visual clutches)."

    if low_items:
        low_lines = ["HISTORICAL LOW SCORES (1-4 STARS) - MISTAKES TO AVOID:"]
        for item in low_items[:6]:
            h = item.get("hook_info", {})
            quote = h.get("quote", "Unknown")
            reason = h.get("reason", "")
            score = item.get("rating", 1)
            low_lines.append(f"- [{score}/10 ⛔️] Quote/Event: \"{quote}\" | Reason: {reason} -> (AVOID: Calm, boring dialogue)")
        historical_low_scores = "\n".join(low_lines)
    else:
        historical_low_scores = "HISTORICAL LOW SCORES: (No low ratings recorded yet. Strictly avoid calm chatting or monotone background dialogue)."

    return historical_high_scores, historical_low_scores


def get_learning_stats() -> dict:
    """Returns comprehensive statistics on hook ratings from SQLite."""
    return db_get_hook_learning_stats()
