import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("AISEOGenerator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")


def generate_viral_seo_meta(
    transcript_or_title: str,
    target_platform: str = "tiktok",
    gemini_api_key: str = ""
) -> dict:
    """
    Generates high-CTR titles, descriptions, hashtags, and pinned comment baits using Gemini.
    """
    if not gemini_api_key:
        return {
            "viral_title": "ЭПИЧНЫЙ МОМЕНТ В ИГРЕ! 🔥",
            "description": "Смотри до конца, это было нереально! Подпишись и ставь лайк.",
            "hashtags": ["#cs2", "#кс2", "#gaming", "#fyp", "#рек", "#рекtiktok"],
            "pinned_comment": "Напиши в комменты, как бы ты сыграл в этом моменте? 👇"
        }

    client = genai.Client(api_key=gemini_api_key)

    prompt = f"""You are a top viral social media strategist for {target_platform} gaming highlights.
Given the following context/dialogue of a short viral video clip:
\"\"\"{transcript_or_title}\"\"\"

Generate high-converting SEO metadata in Russian tailored specifically for {target_platform}.

Requirements:
1. `viral_title`: Clickbait, high-CTR, emotional title (1 line, 1-2 emojis, e.g. "ОН РЕАЛЬНО ЗАБРАЛ ЭТОТ РАУНД?! 😱🔥" or "ШОК! 1В4 КЛАТЧ В CS2").
2. `description`: Punchy 1-2 sentence description with call-to-action.
3. `hashtags`: 5-7 trending high-traffic hashtags (e.g. ["#cs2", "#кс2", "#gaming", "#рек", "#fyp"]).
4. `pinned_comment`: Provocative question or engagement bait to pin in comments (e.g. "Оцени момент от 1 до 10 в комментариях! 👇").

Respond ONLY with a valid JSON object:
{{
  "viral_title": "<string>",
  "description": "<string>",
  "hashtags": ["#tag1", "#tag2", ...],
  "pinned_comment": "<string>"
}}
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        data = json.loads(response.text)
        logger.info(f"Generated SEO metadata: Title='{data.get('viral_title')}'")
        return data
    except Exception as e:
        logger.error(f"Error generating SEO meta with Gemini: {e}")
        return {
            "viral_title": "ЭПИЧНЫЙ МОМЕНТ В ИГРЕ! 🔥",
            "description": "Смотри до конца, это было нереально!",
            "hashtags": ["#cs2", "#кс2", "#gaming", "#fyp", "#рек"],
            "pinned_comment": "Оцени мув от 1 до 10 👇"
        }


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
