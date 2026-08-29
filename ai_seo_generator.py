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

    prompt = f"""Act as an elite viral video producer and TikTok/YouTube Shorts expert specializing in high-CTR covers, viral retention, and clickbait hooks.
Analyze the following dialogue, visual cues, and emotional climax for Clip #{clip_index} (out of {total_clips} clips):

\"\"\"
{transcript_or_title}
\"\"\"

A viral 9:16 vertical thumbnail needs:
1. High emotion or peak action.
2. A clear focal point in the vertical frame.
3. 1-3 highly intriguing, short words as massive text overlay to spark curiosity.
4. An ultra-clickbait neon badge at the top.

TASK:
Analyze the content and generate high-converting, irresistible, clickbait metadata in Russian strictly tailored to THIS SPECIFIC CLIP.

REQUIREMENTS:
1. `thumbnail_text_overlay`: 1-3 ultra-intriguing, high-CTR words for the vertical cover overlay (e.g. "НЕВОЗМОЖНО?!", "ОН В ШОКЕ 😱", "100K IQ МУВ", "ЧТО С НИМ?!", "СЕКРЕТ РАСКРЫТ", "КАК ОН ВЫЖИЛ", "ТАКОГО НЕ ЖДАЛИ").
2. `thumbnail_badge`: Glowing neon badge pill (e.g. "🔥 ШОК-КОНТЕНТ", "😱 100K IQ", "⚡️ 99% НЕ ЗНАЛИ", "👑 ТОП-1 МОМЕНТ", "🎯 1 В 5 КЛАТЧ", "💥 ЭТО РАЗРЫВ").
3. `thumbnail_hook_reason`: 1 sentence explaining the emotional hook of this frame.
4. `thumbnail_timestamp_offset`: Float seconds relative to clip start for extracting the best keyframe (e.g. 1.5, 2.8, 3.5).
5. `viral_title`: High-converting emotional clickbait title in Russian (1 line, 1-2 emojis).
6. `description`: Punchy 1-2 sentence description highlighting the climax with a strong Call-To-Action.
7. `hashtags`: 5-7 high-traffic trending hashtags relevant to the topic.
8. `pinned_comment`: Provocative engagement question to trigger debate in the comments.

RESPOND ONLY WITH A VALID JSON OBJECT:
{{
  "thumbnail_text_overlay": "<string 1-3 words>",
  "thumbnail_badge": "<string 2-3 words>",
  "thumbnail_hook_reason": "<string>",
  "thumbnail_timestamp_offset": 1.5,
  "viral_title": "<string>",
  "description": "<string>",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],
  "pinned_comment": "<string>"
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
                data["thumbnail_badge"] = fb_choice.get("thumbnail_badge", "🔥 ШОК-КОНТЕНТ")
            if not data.get("thumbnail_text_overlay"):
                data["thumbnail_text_overlay"] = data.get("viral_title", "ЭПИЧНЫЙ МОМЕНТ 🔥")
            logger.info(f"Generated unique SEO metadata via {model_name} for Clip #{clip_index}: Title='{data.get('viral_title')}' (Text: '{data.get('thumbnail_text_overlay')}', Badge: '{data.get('thumbnail_badge')}')")
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
