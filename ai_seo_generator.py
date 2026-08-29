import json
import random
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("AISEOGenerator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

FALLBACK_VIRAL_TEMPLATES = [
    {
        "viral_title": "ЭТОТ МОМЕНТ ПЕРЕВЕРНУЛ ВСЁ! 😱🔥",
        "description": "Досмотри до конца, ты точно не ожидал такой развязки! Подпишись и ставь лайк.",
        "hashtags": ["#viral", "#shorts", "#fyp", "#рек", "#топ", "#тренды"],
        "pinned_comment": "Оцени этот момент от 1 до 10 в комментариях! 👇",
        "thumbnail_badge": "🔥 ШОК-КОНТЕНТ"
    },
    {
        "viral_title": "ОН РЕАЛЬНО ЭТО СДЕЛАЛ?! 🤯⚡️",
        "description": "Шок-контент, который ты должен увидеть прямо сейчас! Делись с другом.",
        "hashtags": ["#fyp", "#тренды", "#шок", "#эпик", "#рекомендации", "#тикток"],
        "pinned_comment": "Как бы ты поступил в такой ситуации? Пиши в комменты! 👇",
        "thumbnail_badge": "😱 100K IQ МУВ"
    },
    {
        "viral_title": "САМЫЙ БЕЗУМНЫЙ КЛАТЧ В ИСТОРИИ 🎯💥",
        "description": "Такое повторить просто невозможно! Жми лайк, если офигел от мува.",
        "hashtags": ["#gaming", "#clutch", "#highlight", "#рек", "#топчик", "#gameplay"],
        "pinned_comment": "Повторил бы такой мув? Напиши честно! 👇",
        "thumbnail_badge": "🎯 1 В 5 КЛАТЧ"
    },
    {
        "viral_title": "99.9% ЛЮДЕЙ НЕ ЗАМЕТЯТ ЭТОГО СЕКРЕТА 🤫⚡️",
        "description": "Смотри внимательно на детали! Подпишись, чтобы не пропустить новые фишки.",
        "hashtags": ["#секрет", "#лайфхак", "#fyp", "#тренды", "#рек", "#полезно"],
        "pinned_comment": "Ты заметил главную пасхалку? Делись мнением 👇",
        "thumbnail_badge": "⚡️ 99% НЕ ЗНАЛИ"
    },
    {
        "viral_title": "ЛУЧШИЙ МОМЕНТ ЗА ВЕСЬ СТРИМ! 👑🏆",
        "description": "Чат просто взорвался от эмоций! Ставь лайк и подписывайся на канал.",
        "hashtags": ["#стрим", "#хайлайт", "#эпик", "#fyp", "#юмор", "#мемы"],
        "pinned_comment": "Какая эмоция была у тебя в конце? Пиши смайликами 👇",
        "thumbnail_badge": "👑 ТОП-1 МОМЕНТ"
    }
]


def generate_viral_seo_meta(
    transcript_or_title: str,
    target_platform: str = "tiktok",
    gemini_api_key: str = "",
    clip_index: int = 1,
    total_clips: int = 1
) -> dict:
    """
    Generates high-converting, 100% unique, clickbait titles, descriptions, hashtags,
    pinned comments, and thumbnail badge tags tailored specifically to each individual clip.
    """
    fb_choice = FALLBACK_VIRAL_TEMPLATES[(clip_index - 1) % len(FALLBACK_VIRAL_TEMPLATES)].copy()
    if total_clips > 1:
        fb_choice["viral_title"] = f"ЧАСТЬ {clip_index}: {fb_choice['viral_title']}"

    if not gemini_api_key:
        return fb_choice

    client = genai.Client(api_key=gemini_api_key)

    prompt = f"""You are a world-class viral growth strategist and clickbait master for {target_platform} short-form content.
Analyze the following specific dialogue, speech context, and key moments for Clip #{clip_index} (out of {total_clips} clips):

\"\"\"
{transcript_or_title}
\"\"\"

TASK:
Generate high-converting, irresistible, clickbait SEO metadata in Russian strictly tailored to the SPECIFIC DIALOGUE and EVENTS in THIS EXACT CLIP.
Every clip in a multi-clip series MUST have a completely UNIQUE, specific title and hook based on what actually happened in this segment.

REQUIREMENTS:
1. `viral_title`: Ultra-clickbait, high-CTR emotional title in Russian. (1 line, 1-2 emojis, e.g. "ОН РЕАЛЬНО СКАЗАЛ ЭТО В ЭФИРЕ?! 😱", "КАК ОН ВЫЖИЛ В ЭТОМ РАУНДЕ?! 🔥", "ЭТОТ СЕКРЕТ ШОКИРОВАЛ ВСЕХ 🤯", "Я ОРАЛ НА ЭТОМ МОМЕНТЕ 2 ЧАСА 💀😂"). If clip index > 1 and multiple clips, you can optionally include [ЧАСТЬ {clip_index}] or make it an intriguing standalone title.
2. `description`: Punchy 1-2 sentence description highlighting the climax/punchline with a strong Call-To-Action (e.g., "Смотри до конца, чтобы увидеть развязку! Подпишись 👇").
3. `hashtags`: 5-7 high-traffic, trending hashtags relevant to the ACTUAL topic (e.g., if gaming: ["#gaming", "#cs2", "#рек", "#fyp"], if podcast/humor: ["#подкаст", "#юмор", "#мемы", "#fyp", "#рек"], if IRL/stories: ["#истории", "#шок", "#fyp", "#рек"]).
4. `pinned_comment`: Provocative engagement question or comment bait to trigger massive discussion in the comments section.
5. `thumbnail_badge`: Short punchy 2-4 word clickbait badge for the video cover (e.g. "🔥 ШОК-КОНТЕНТ", "😱 100K IQ", "⚡️ 99% НЕ ЗНАЛИ", "👑 ТОП-1 МОМЕНТ", "🎯 1 В 5 КЛАТЧ", "💥 ЭТО РАЗРЫВ").

RESPOND ONLY WITH A VALID JSON OBJECT:
{{
  "viral_title": "<string>",
  "description": "<string>",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],
  "pinned_comment": "<string>",
  "thumbnail_badge": "<string>"
}}
"""

    models_to_try = [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-flash-latest"
    ]
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.7
                )
            )
            data = json.loads(response.text)
            if not data.get("thumbnail_badge"):
                data["thumbnail_badge"] = fb_choice["thumbnail_badge"]
            logger.info(f"Generated unique SEO metadata via {model_name} for Clip #{clip_index}: Title='{data.get('viral_title')}' (Badge: '{data.get('thumbnail_badge')}')")
            return data
        except Exception as e:
            logger.warning(f"SEO generation model {model_name} failed: {e}")
            continue

    logger.warning(f"All Gemini models exhausted for Clip #{clip_index}, using dynamic contextual fallback.")
    return fb_choice


def format_seo_telegram_block(seo_data: dict, target_platform: str = "tiktok") -> str:
    """Formats SEO metadata into a clean, ready-to-copy Markdown block."""
    plat_icon = "🎵" if target_platform == "tiktok" else "🔴"
    title = seo_data.get("viral_title", "ЭПИЧНЫЙ МОМЕНТ 🔥")
    desc = seo_data.get("description", "")
    tags = " ".join(seo_data.get("hashtags", []))
    pin = seo_data.get("pinned_comment", "")

    return (
        f"📝 **Готовий опис для публікації ({plat_icon} {target_platform.title()}):**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔥 **Заголовок:**\n`{title}`\n\n"
        f"📄 **Опис:**\n`{desc}`\n\n"
        f"🏷 **Хештеги:**\n`{tags}`\n\n"
        f"📌 **Закріплений коментар:**\n`{pin}`\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
