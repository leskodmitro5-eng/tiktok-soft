import os
import sys
import time
import asyncio
import logging
import uuid
import shutil
import re
from pathlib import Path

from telethon import TelegramClient, events, Button
from telethon.tl.types import (
    DocumentAttributeVideo,
    BotCommand,
    BotCommandScopeDefault,
    InputMediaInvoice,
    LabeledPrice,
    Invoice,
    DataJSON,
    UpdateBotPrecheckoutQuery,
    MessageActionPaymentSentMe
)
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.functions.messages import SetBotPrecheckoutResultsRequest
from telethon.sessions import MemorySession

from config import (
    BOT_TOKEN,
    ADMIN_ID,
    ADMIN_IDS,
    BANNER_PATH,
    BAIT_PATH,
    GROQ_API_KEY,
    GEMINI_API_KEY,
    TG_API_ID,
    TG_API_HASH,
    ARCHIVE_CHANNEL_ID,
    BASE_DIR
)
from database import (
    db_create_job,
    db_update_job_status,
    db_get_or_create_user,
    db_get_user,
    db_deduct_credit,
    db_add_credits,
    db_set_subscription,
    db_has_active_subscription,
    db_grant_lifetime_vip,
    db_revoke_vip,
    db_get_all_users,
    db_get_referral_stats,
    db_get_user_tracked_videos,
    db_delete_tracked_video
)
from thumbnail_generator import generate_viral_thumbnail
from view_tracker import register_video_for_tracking, refresh_tracked_videos_for_user, get_video_online_stats
from webapp_server import start_webapp_server

from ai_seo_generator import generate_viral_seo_meta, format_seo_telegram_block
from video_processor import (
    process_video_pipeline_async,
    get_media_info,
    slice_raw_segment_async,
    detect_best_h264_encoder
)
from ai_highlight_cutter import (
    calculate_clips_count,
    extract_compressed_audio,
    transcribe_audio_for_highlights,
    find_viral_highlights
)
from youtube_downloader import (
    extract_youtube_url,
    get_youtube_video_info,
    download_youtube_video,
    download_youtube_section
)
from tiktok_helper import (
    extract_tiktok_url,
    get_tiktok_video_info,
    download_tiktok_video
)
from hook_learner import (
    record_user_rating,
    get_learning_stats,
    get_learning_context,
    RATING_LEVEL_DEFINITIONS
)
from highlight_learner import (
    save_highlight_decision,
    record_highlight_rating,
    get_highlight_learning_stats,
    HIGHLIGHT_RATING_DEFINITIONS
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - [%(name)s] - %(message)s")
logger = logging.getLogger("BotRunner")

SESSION_PATH = str(BASE_DIR / "bot_session")
# Use MemorySession for cloud bots to prevent session file conflicts
client = TelegramClient(MemorySession(), TG_API_ID, TG_API_HASH)

# Multi-Worker Queue Pool configuration
MAX_CONCURRENT_WORKERS = 2
job_queue = asyncio.Queue()
active_workers = []

TEMP_STORAGE_DIR = BASE_DIR / "temp_storage"
TEMP_STORAGE_DIR.mkdir(exist_ok=True)

TARGET_ARCHIVE_CHANNEL = ARCHIVE_CHANNEL_ID
# Memory store for finished clips for 1-click posting & thumbnail generation
completed_clips_meta = {}

# Store pending videos waiting for mode selection
pending_jobs = {}


def format_control_panel_text(
    job_data: dict,
    target_platform: str = "tiktok",
    hook: bool = True,
    banner: bool = True,
    bait: bool = True,
    subs: bool = True
) -> str:
    """Renders a sleek dashboard summary of current video and feature toggles."""
    title = job_data.get("title") or job_data.get("file_name", "Відео")
    dur = job_data.get("approx_duration", 0.0)
    dur_min = dur / 60.0 if dur else 0.0
    is_long = (dur > 180.0)
    clips_count = calculate_clips_count(dur) if is_long else 1

    plat_icon = "🎵" if target_platform == "tiktok" else "🔴"
    plat_name = "TikTok" if target_platform == "tiktok" else "YouTube Shorts"

    if is_long:
        hook_status = "🟢 Увімкнено" if hook else "⚪️ Вимкнено (нарізки мають природний початок без дублювання)"
    else:
        hook_status = "🟢 Увімкнено (AI знайде кульмінацію на 0:00)" if hook else "⚪️ Вимкнено"

    banner_status = "🟢 Увімкнено (стоп-кадр + відео реклами)" if banner else "⚪️ Вимкнено"
    
    if bait:
        if target_platform == "tiktok":
            bait_status = "🟢 Увімкнено (відео-байт «Закинь в сохраненки, чтобы получить этот скин!»)"
        else:
            bait_status = "🟢 Увімкнено (2 байти: підписка ~20с + коментар ~50с)"
    else:
        bait_status = "⚪️ Вимкнено"

    subs_status = "🟢 Увімкнено (караоке-слова MrBeast / Hormozi + Auto-Wrap)" if subs else "⚪️ Вимкнено"

    if is_long and clips_count > 1:
        header = (
            f"🎬 **Довге відео готове до нарізки!**\n"
            f"📌 **Назва:** _{title}_\n"
            f"⏱ **Тривалість:** `{dur_min:.1f} хв` (`{dur:.0f}с`) ➔ ✂️ **{clips_count} вірусних нарізок**\n"
        )
    else:
        dur_line = f"⏱ **Тривалість:** `{dur:.0f} сек`\n" if dur > 0 else ""
        header = (
            f"🎬 **Відео готове до обробки!**\n"
            f"📌 **Назва:** _{title}_\n"
            f"{dur_line}"
        )

    return (
        f"{header}\n"
        f"⚙️ **Панель керування функціями:**\n"
        f"├ 🎯 **Платформа:** {plat_icon} **{plat_name}**\n"
        f"├ 🎣 **AI Хук (0:00):** {hook_status}\n"
        f"├ 📢 **Рекламний банер:** {banner_status}\n"
        f"├ 🪤 **Байт-елемент:** {bait_status}\n"
        f"└ 🔤 **Субтитри (AI):** {subs_status}\n\n"
        f"👇 _Перемикайте потрібні опції кнопками нижче та тисніть **Запустити**:_"
    )


def build_mode_selection_keyboard(
    job_id: str,
    hook: bool = True,
    banner: bool = True,
    bait: bool = True,
    subs: bool = True,
    target_platform: str = "tiktok"
) -> list[list[Button]]:
    """Builds interactive toggle buttons control panel with compact <=64-byte payloads."""
    h_next = 0 if hook else 1
    b_next = 0 if banner else 1
    t_next = 0 if bait else 1
    s_next = 0 if subs else 1

    h_cur = 1 if hook else 0
    b_cur = 1 if banner else 0
    t_cur = 1 if bait else 0
    s_cur = 1 if subs else 0

    plat_code = "tt" if target_platform in ("tiktok", "tt") else "yt"
    is_tt = (plat_code == "tt")

    tt_btn_text = "🎵 TikTok  [ ✅ ]" if is_tt else "🎵 TikTok"
    yt_btn_text = "🔴 YouTube Shorts  [ ✅ ]" if not is_tt else "🔴 YouTube Shorts"

    hook_btn_text = "🎣 AI Хук:  УВІМКНЕНО ✅" if hook else "🎣 AI Хук:  ВИМКНЕНО ❌"
    banner_btn_text = "📢 Банер:  УВІМКНЕНО ✅" if banner else "📢 Банер:  ВИМКНЕНО ❌"
    bait_btn_text = "🪤 Байт:  УВІМКНЕНО ✅" if bait else "🪤 Байт:  ВИМКНЕНО ❌"
    subs_btn_text = "🔤 Субтитри:  УВІМКНЕНО ✅" if subs else "🔤 Субтитри:  ВИМКНЕНО ❌"

    return [
        [
            Button.inline(tt_btn_text, data=f"cfg:{job_id}:{h_cur}:{b_cur}:{t_cur}:{s_cur}:tt"),
            Button.inline(yt_btn_text, data=f"cfg:{job_id}:{h_cur}:{b_cur}:{t_cur}:{s_cur}:yt")
        ],
        [
            Button.inline(hook_btn_text, data=f"cfg:{job_id}:{h_next}:{b_cur}:{t_cur}:{s_cur}:{plat_code}")
        ],
        [
            Button.inline(banner_btn_text, data=f"cfg:{job_id}:{h_cur}:{b_next}:{t_cur}:{s_cur}:{plat_code}")
        ],
        [
            Button.inline(bait_btn_text, data=f"cfg:{job_id}:{h_cur}:{b_cur}:{t_next}:{s_cur}:{plat_code}")
        ],
        [
            Button.inline(subs_btn_text, data=f"cfg:{job_id}:{h_cur}:{b_cur}:{t_cur}:{s_next}:{plat_code}")
        ],
        [
            Button.inline("🚀 ЗАПУСТИТИ ОБРОБКУ ВІДЕО", data=f"proc:{job_id}:{h_cur}:{b_cur}:{t_cur}:{s_cur}:{plat_code}")
        ]
    ]


async def update_progress_message(status_msg, prefix: str, current: int, total: int, last_update: list):
    """Updates Telegram status message throttled to every 3.5 seconds to avoid FloodWait."""
    now = time.time()
    if now - last_update[0] >= 3.5 or current == total:
        last_update[0] = now
        pct = (current / total) * 100 if total > 0 else 0
        curr_mb = current / (1024 * 1024)
        tot_mb = total / (1024 * 1024)
        text = f"{prefix}\n📊 Прогрес: **{pct:.1f}%** ({curr_mb:.1f} / {tot_mb:.1f} МБ)"
        try:
            await status_msg.edit(text)
        except Exception:
            pass


def format_stats_message(hook_stats: dict, highlight_stats: dict) -> str:
    """Formats full 1-10 gradient learning statistics for both Hooks and Highlights."""
    h_total = hook_stats.get("total_rated", 0)
    h_avg = hook_stats.get("avg_rating", 0.0)
    h_dist = hook_stats.get("distribution", {})
    h_stars = "⭐️" * max(1, min(10, int(round(h_avg)))) if h_total > 0 else "—"

    hl_total = highlight_stats.get("total_rated", 0)
    hl_avg = highlight_stats.get("avg_rating", 0.0)
    hl_dist = highlight_stats.get("distribution", {})
    hl_stars = "⭐️" * max(1, min(10, int(round(hl_avg)))) if hl_total > 0 else "—"

    emojis = {
        10: "🌟", 9: "🔥", 8: "✨", 7: "👍", 6: "👌",
        5: "⚖️", 4: "⚠️", 3: "❌", 2: "🚫", 1: "⛔️"
    }

    msg = (
        "🧠 **Матриця безперервного навчання Google Gemini (1 — 10) [SQLite]:**\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✂️ **НАРІЗКИ З ДОВГИХ ВІДЕО (CLIPS CUTTER):**\n"
        f"• Оцінено нарізок: **{hl_total}**\n"
        f"• Середній бал якості нарізок: **{hl_avg}/10** {hl_stars}\n\n"
        "📊 **Розподіл оцінок нарізок:**\n"
    )
    for score in range(10, 0, -1):
        cnt = hl_dist.get(score, 0)
        bar = "█" * cnt if cnt > 0 else "—"
        e = emojis.get(score, "•")
        msg += f"{e} `{score:2d}/10`: {bar} ({cnt})\n"

    msg += (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "🎣 **AI ХУКИ (0:00 HOOK SELECTION):**\n"
        f"• Оцінено хуків: **{h_total}**\n"
        f"• Середній бал точності хуків: **{h_avg}/10** {h_stars}\n\n"
        "📊 **Розподіл оцінок хуків:**\n"
    )
    for score in range(10, 0, -1):
        cnt = h_dist.get(score, 0)
        bar = "█" * cnt if cnt > 0 else "—"
        e = emojis.get(score, "•")
        msg += f"{e} `{score:2d}/10`: {bar} ({cnt})\n"

    msg += "\n💡 _Кожна оцінка від 1 до 10 калібрує нейромережу і підтягується в промпти для нарізки довгих відео та пошуку хуків._"
    return msg


@client.on(events.NewMessage(pattern=r"^/start(?:@\w+)?(?:\s+(.+))?$"))
async def start_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    username = sender.username or ""
    first_name = sender.first_name or ""

    # Parse potential referral argument: /start ref_123456
    ref_arg = event.pattern_match.group(1)
    referrer_id = None
    if ref_arg and ref_arg.startswith("ref_"):
        try:
            referrer_id = int(ref_arg.replace("ref_", ""))
        except ValueError:
            referrer_id = None

    user = db_get_or_create_user(sender_id, username, first_name, referrer_id)
    is_admin = (sender_id in ADMIN_IDS)

    credits_text = "♾ Безліміт (Admin)" if is_admin else f"{user.get('credits_balance', 0)} відео"

    welcome_text = (
        "🎬 **Вітаємо в TikTok Video Processor + Gemini AI Hook & Cutter (Studio v2.0)!**\n\n"
        f"👤 **Ваш акаунт:** `{first_name}` (@{username or 'no_tag'})\n"
        f"💎 **Баланс генерацій:** **{credits_text}**\n\n"
        "⚡️ **Швидкі команди:**\n"
        "• `/profile` — ваш кабінет, баланс та тарифи\n"
        "• `/buy` — поповнення кредитів (Telegram Stars)\n"
        "• `/ref` — партнерська програма (бонуси за друзів)\n"
        "• `/track <посилання>` — моніторинг переглядів відео в TikTok / Shorts\n"
        "• `/myviews` — аналітика ваших опублікованих роликів\n"
        "• `/stats` — матриця навчання Gemini (1-10)\n\n"
        "📥 **Надішліть відео файлом або посиланням на TikTok, щоб розпочати!**\n"
        "💡 _(Для YouTube — завантажте ролик ботом-завантажувачем та перешліть файл сюди)_"

    )

    buttons = [
        [
            Button.inline("💎 Мій профіль & Баланс", data="quick_cmd:profile"),
            Button.inline("💳 Купити тарифи", data="quick_cmd:buy")
        ],
        [
            Button.inline("👥 Реферальна програма", data="quick_cmd:ref"),
            Button.inline("📊 Статистика ШІ (/stats)", data="quick_cmd:stats")
        ]
    ]
    await event.reply(welcome_text, buttons=buttons)


@client.on(events.NewMessage(pattern=r"^/(?:profile|balance)(?:@\w+)?(?:\s+.*)?$"))
async def profile_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    user = db_get_or_create_user(sender_id, sender.username or "", sender.first_name or "")
    is_admin = (sender_id in ADMIN_IDS)
    
    ref_stats = db_get_referral_stats(sender_id)
    credits_val = "♾ Безліміт (Admin VIP)" if is_admin else f"**{user.get('credits_balance', 0)}** генерацій"
    tier_name = "🌟 Admin VIP" if is_admin else ("🚀 Monthly Unlimited" if db_has_active_subscription(user) else "⚡️ Free / Starter")

    earned = ref_stats.get("earned_credits", ref_stats.get("active_invited", 0) * 2)

    msg = (
        "👤 **Особистий кабінет клієнта:**\n\n"
        f"• **ID:** `{sender_id}`\n"
        f"• **Тарифний план:** {tier_name}\n"
        f"• **Залишок балансу:** {credits_val}\n"
        f"• **Витрачено зірочок:** `{user.get('total_spent_stars', 0)}` ⭐️\n\n"
        "👥 **Партнерська статистика:**\n"
        f"• Запрошено друзів: **{ref_stats.get('total_invited', 0)}**\n"
        f"• Активних (створили відео): **{ref_stats.get('active_invited', 0)}**\n"
        f"• Зароблено бонусів: **+{earned}** кредитів\n"
        f"• За наступного друга: **{ref_stats.get('next_reward_you', '+2 відео')}**\n\n"
        "💡 _1 кредит = 1 повністю змонтоване відео з хуком, субтитрами та унікалізацією._"
    )

    buttons = [
        [
            Button.inline("💳 Поповнити баланс (Stars)", data="quick_cmd:buy"),
            Button.inline("👥 Моє реф-посилання", data="quick_cmd:ref")
        ]
    ]
    await event.reply(msg, buttons=buttons)


@client.on(events.NewMessage(pattern=r"^/(?:buy|plans)(?:@\w+)?(?:\s+.*)?$"))
async def buy_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    user = db_get_or_create_user(sender_id, sender.username or "", sender.first_name or "")

    msg = (
        "💎 **Тарифні плани та поповнення через Telegram Stars (⭐️):**\n\n"
        "1. 🌟 **Starter Pack:** `25 ⭐️`\n"
        "• **+10 відео** з повним AI-монтажем (хуки, караоке-субтитри, SEO)\n\n"
        "2. 🔥 **Creator Pro:** `50 ⭐️`\n"
        "• **+25 відео** + пріоритетна черга обробки воркерами (VIP)\n\n"
        "3. 🚀 **Monthly Unlimited:** **`100 ⭐️` (Спец-пропозиція!)**\n"
        "• **Безлімітна кількість відео на 30 днів!**\n\n"
        "👇 _Оберіть тариф нижче для миттєвої оплати Telegram Stars:_"
    )

    buttons = [
        [Button.inline("🌟 Starter Pack (10 відео) — 25 ⭐️", data="buy_plan:starter:25:10")],
        [Button.inline("🔥 Creator Pro (25 відео) — 50 ⭐️", data="buy_plan:pro:50:25")],
        [Button.inline("🚀 Monthly Unlimited (30 днів) — 100 ⭐️", data="buy_plan:unlimited:100:0")],
        [Button.inline("🔙 Назад до кабінету", data="quick_cmd:profile")]
    ]
    await event.reply(msg, buttons=buttons)


@client.on(events.NewMessage(pattern=r"^/(?:ref|partner)(?:@\w+)?(?:\s+.*)?$"))
async def referral_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    bot_me = await client.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{sender_id}"
    ref_stats = db_get_referral_stats(sender_id)

    active = ref_stats.get("active_invited", 0)
    total = ref_stats.get("total_invited", 0)
    
    # Visual Progress Bar up to 10 friends
    bar_len = 10
    filled = min(bar_len, active)
    empty = bar_len - filled
    progress_bar = "█" * filled + "░" * empty

    if active >= 10:
        status_unlimited = "🎉 **ВАМ РОЗБЛОКОВАНО БЕЗЛІМІТНИЙ ДОСТУП НА 1 МІСЯЦЬ!**"
    else:
        status_unlimited = f"🎯 До безліміту залишилося: **{10 - active} активних друзів**"

    msg = (
        "👥 **Прогресивна реферальна програма 2.0:**\n\n"
        "Запрошуйте друзів та розблокуйте **безкоштовний безліміт на 1 місяць!**\n\n"
        "🏆 **Прогресивна шкала винагород:**\n"
        "• 1-й друг: `+1` відео йому, `+2` відео вам\n"
        "• 2-й друг: `+2` відео йому, `+3` відео вам\n"
        "• 3-й друг: `+3` відео йому, `+4` відео вам\n"
        "• ...\n"
        "• 9-й друг: `+9` відео йому, `+10` відео вам\n"
        "• **10-й друг: 🔥 БЕЗЛІМІТНИЙ ПЛАН НА 1 МІСЯЦЬ ДЛЯ ВАС!**\n\n"
        "📊 **Ваш поточний прогрес:**\n"
        f"[{progress_bar}] **{active}/10 друзів**\n"
        f"{status_unlimited}\n\n"
        f"• Всього переходів: **{total}** | Створили відео: **{active}**\n"
        f"• Нагорода за наступного ({active + 1}-го) друга: **{ref_stats.get('next_reward_you', '+2 відео')}** (другу: {ref_stats.get('next_bonus_friend', '+1 відео')})\n\n"
        f"🔗 **Ваше реферальне посилання (клікніть щоб скопіювати):**\n`{ref_link}`"
    )

    buttons = [
        [Button.inline("🔄 Оновити статистику", data="quick_cmd:ref")]
    ]
    await event.reply(msg, buttons=buttons)


@client.on(events.NewMessage(pattern=r"^/track(?:@\w+)?(?:\s+(.+))?$"))
async def track_video_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    url = event.pattern_match.group(1)

    if not url:
        await event.reply(
            "📊 **Відстеження вірусних переглядів відео:**\n\n"
            "Вкажіть посилання на опублікований ролик у TikTok або YouTube Shorts:\n"
            "`/track https://youtube.com/shorts/xxxxxx` або `/track https://vt.tiktok.com/xxxxxx`\n\n"
            "💡 _Бот автоматично зчитає перегляди та лайки й оновить базу навчання Gemini!_"
        )
        return

    url = url.strip()
    status_msg = await event.reply("🔍 **Зчитування переглядів та реєстрація на моніторинг...**")
    
    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, register_video_for_tracking, sender_id, url)
        
        await status_msg.edit(
            f"✅ **Відео успішно додано на моніторинг переглядів!**\n\n"
            f"📌 **Назва:** _{res.get('title', 'Video')}_\n"
            f"🎯 **Платформа:** `{res['platform'].upper()}`\n"
            f"👀 **Поточні перегляди:** **{res['views']:,}**\n"
            f"❤️ **Лайки:** **{res.get('likes', 0):,}**\n\n"
            f"👉 Переглянути список усіх відстежуваних роликів: `/myviews`"
        )
    except Exception as e:
        logger.exception(f"Error tracking {url}: {e}")
        await status_msg.edit(f"❌ **Помилка моніторингу:** `{str(e)}`")


def format_myviews_view(items: list[dict]) -> tuple[str, list[list[Button]] | None]:
    if not items:
        return (
            "📊 **У вас ще немає відстежуваних відео.**\n\n"
            "Щоб додати ролик на моніторинг переглядів, надішліть команду:\n"
            "`/track https://youtube.com/shorts/ваш_ролик` або `/track https://vt.tiktok.com/ваш_ролик`",
            None
        )
    
    msg = "📊 **Аналітика та перегляди ваших роликів (Live Tracker):**\n\n"
    total_views = 0
    total_likes = 0
    buttons = []
    
    for i, item in enumerate(items[:10], 1):
        plat_icon = "🎵" if item["platform"] == "tiktok" else "🔴"
        v = item.get("current_views", 0)
        l = item.get("likes", 0)
        total_views += v
        total_likes += l
        raw_title = item.get("title", "Video") or "Video"
        clean_title = (raw_title[:36] + "...") if len(raw_title) > 36 else raw_title
        
        msg += (
            f"**#{i}** {plat_icon} **[{v:,} 👁 | ❤️ {l:,}]**\n"
            f"📌 _{clean_title}_\n"
            f"🔗 [Відкрити посилання]({item['url']})\n\n"
        )
        buttons.append([Button.inline(f"🗑 Видалити #{i} з моніторингу", data=f"del_track:{item['id']}")])
        
    msg += f"🔥 **Сумарно по всім роликам:** **{total_views:,} переглядів** (❤️ {total_likes:,})\n"
    msg += "💡 _Натисніть кнопку нижче, щоб видалити ролик або оновити лічильник:_"
    
    buttons.append([Button.inline("🔄 Оновити дані переглядів", data="refresh_views")])
    return msg, buttons if buttons else None


@client.on(events.NewMessage(pattern=r"^/myviews(?:@\w+)?(?:\s+.*)?$"))
async def myviews_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    status_msg = await event.reply("🔄 **Оновлення актуальної статистики переглядів...**")
    
    try:
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(None, refresh_tracked_videos_for_user, sender_id)
        msg, buttons = format_myviews_view(items)
        await status_msg.edit(msg, buttons=buttons, link_preview=False)
    except Exception as e:
        logger.exception(f"Error fetching myviews: {e}")
        await status_msg.edit(f"❌ **Помилка оновлення переглядів:** `{str(e)}`")


@client.on(events.CallbackQuery(pattern=r"^del_track:(\d+)$"))
async def delete_tracked_video_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    track_id = int(event.pattern_match.group(1))

    deleted = db_delete_tracked_video(track_id, sender_id)
    if deleted:
        await event.answer("🗑 Відео видалено з моніторингу!", alert=True)
    else:
        await event.answer("⚠️ Відео не знайдено або вже видалено.", alert=True)

    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, db_get_user_tracked_videos, sender_id, 10)
    msg, buttons = format_myviews_view(items)
    try:
        await event.edit(msg, buttons=buttons, link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(pattern=r"^refresh_views$"))
async def refresh_views_callback_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    await event.answer("🔄 Оновлюю перегляди...", alert=False)
    
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, refresh_tracked_videos_for_user, sender_id)
    msg, buttons = format_myviews_view(items)
    try:
        await event.edit(msg, buttons=buttons, link_preview=False)
    except Exception:
        pass



@client.on(events.CallbackQuery(pattern=r"^buy_plan:(starter|pro|unlimited):(\d+):(\d+)$"))
async def buy_plan_callback_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    match = re.match(r"^buy_plan:(starter|pro|unlimited):(\d+):(\d+)$", event.data.decode("utf-8"))
    if not match:
        return

    plan_type = match.group(1)
    stars_cost = int(match.group(2))
    credits_amt = int(match.group(3))

    plan_titles = {
        "starter": "Starter Pack (10 відео)",
        "pro": "Creator Pro (25 відео)",
        "unlimited": "Monthly Unlimited (30 днів)"
    }
    plan_descriptions = {
        "starter": "10 кредитів на AI-монтаж відео з хуками, караоке-субтитрами та SEO",
        "pro": "25 кредитів на AI-монтаж відео + пріоритетна черга обробки VIP",
        "unlimited": "Безлімітна обробка відео протягом 30 днів без лімітів та черг"
    }

    title = plan_titles.get(plan_type, "Тарифний план")
    desc = plan_descriptions.get(plan_type, "Поповнення генерацій")

    await event.answer("⭐️ Формую рахунок Telegram Stars...", alert=False)

    try:
        # Dispatch native Telegram Stars Invoice
        invoice_media = InputMediaInvoice(
            title=title,
            description=desc,
            invoice=Invoice(
                currency="XTR",
                prices=[LabeledPrice(label=title, amount=stars_cost)],
                test=False
            ),
            payload=f"plan:{plan_type}:{stars_cost}:{credits_amt}".encode("utf-8"),
            provider="",
            provider_data=DataJSON(data="{}"),
            start_param=f"plan_{plan_type}"
        )

        await client.send_file(
            event.chat_id,
            file=invoice_media
        )
    except Exception as inv_err:
        logger.warning(f"Native Stars Invoice fallback: {inv_err}")
        # Fallback simulation if direct bot invoice not supported by client
        if plan_type == "unlimited":
            exp = db_set_subscription(sender_id, "unlimited", days=30, spent_stars=stars_cost)
            await event.respond(
                f"🚀 **Вітаємо! Безлімітний тариф активовано на 30 днів (до {exp[:10]})!**\n\n"
                f"Тепер ви можете обробляти необмежену кількість відео без списання кредитів."
            )
        else:
            new_bal = db_add_credits(sender_id, credits_amt, spent_stars=stars_cost)
            await event.respond(
                f"💎 **Нараховано +{credits_amt} генерацій!**\n\n"
                f"📊 Ваш новий баланс: **{new_bal} відео**.\n"
                f"Надсилайте нове відео для монтажу!"
            )


# --- Handle Telegram Stars PreCheckout & Payment Success ---

@client.on(events.Raw(UpdateBotPrecheckoutQuery))
async def pre_checkout_handler(event):
    """Approves native Telegram Stars pre-checkout validation."""
    try:
        await client(SetBotPrecheckoutResultsRequest(
            query_id=event.query_id,
            success=True
        ))
        logger.info(f"PreCheckout approved for query {event.query_id}")
    except Exception as e:
        logger.error(f"Error answering precheckout: {e}")


@client.on(events.NewMessage(func=lambda e: e.action and isinstance(e.action, MessageActionPaymentSentMe)))
async def payment_success_handler(event):
    """Handles confirmed Telegram Stars payments and updates user credits/subscription."""
    action = event.action
    payload_str = action.payload.decode("utf-8") if isinstance(action.payload, bytes) else str(action.payload)
    stars_amount = action.total_amount
    sender_id = event.sender_id

    if "unlimited" in payload_str:
        exp = db_set_subscription(sender_id, "unlimited", days=30, spent_stars=stars_amount)
        await event.reply(
            f"🎉 **Оплата {stars_amount} ⭐️ успішна!**\n\n"
            f"🚀 **Вам активовано БЕЗЛІМІТНИЙ ПЛАН на 30 днів (до {exp[:10]})!**\n"
            f"Обробляйте будь-яку кількість відео без черг та лімітів."
        )
    elif "pro" in payload_str:
        new_bal = db_add_credits(sender_id, 25, spent_stars=stars_amount)
        await event.reply(
            f"🎉 **Оплата {stars_amount} ⭐️ успішна!**\n\n"
            f"💎 Нараховано **+25 відео** (Ваш баланс: **{new_bal} відео**).\n"
            f"Пріоритетна черга активована!"
        )
    else:
        new_bal = db_add_credits(sender_id, 10, spent_stars=stars_amount)
        await event.reply(
            f"🎉 **Оплата {stars_amount} ⭐️ успішна!**\n\n"
            f"💎 Нараховано **+10 відео** (Ваш баланс: **{new_bal} відео**).\n"
            f"Надсилайте нові відео для монтажу!"
        )


@client.on(events.CallbackQuery(pattern=r"^gen_thumb:([a-zA-Z0-9_-]+)$"))
async def generate_thumb_callback_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    clip_job_id = event.pattern_match.group(1)

    meta = completed_clips_meta.get(clip_job_id)
    if not meta or not meta.get("video_file_path") or not os.path.exists(meta["video_file_path"]):
        await event.answer("⚠️ Відео застаріло або видалено з кешу.", alert=True)
        return

    await event.answer("🖼 Створюю клікбейт-обкладинку 9:16...", alert=False)
    status_msg = await event.respond("🎨 **Генерація високоефективної обкладинки (Pillow HD)...**")

    try:
        thumb_out = str(Path(meta["video_file_path"]).with_suffix(".thumb.jpg"))
        title = meta.get("title") or "ЭПИЧНЫЙ МОМЕНТ 🔥"
        
        loop = asyncio.get_running_loop()
        res_thumb = await loop.run_in_executor(
            None,
            generate_viral_thumbnail,
            meta["video_file_path"],
            thumb_out,
            title,
            1.0,
            "🔥 ШОК"
        )

        if res_thumb and os.path.exists(res_thumb):
            await client.send_file(
                event.chat_id,
                file=res_thumb,
                caption=f"🖼 **Клікбейтна обкладинка 9:16 готова!**\n\n📌 **Заголовок:** _{title}_\n💡 _Використовуйте як перший кадр або обкладинку в TikTok / Shorts!_"
            )
            await status_msg.delete()
            try:
                os.remove(res_thumb)
            except Exception:
                pass
        else:
            await status_msg.edit("❌ Не вдалося створити обкладинку.")
    except Exception as err:
        logger.error(f"Error generating thumbnail: {err}")
        await status_msg.edit(f"❌ Помилка: {err}")


@client.on(events.CallbackQuery(pattern=r"^track_prompt:([a-zA-Z0-9_-]+)$"))
async def track_prompt_callback_handler(event):
    await event.answer()
    await event.respond(
        "📊 **Як відстежувати перегляди цього ролика:**\n\n"
        "1. Опублікуйте відео в TikTok або YouTube Shorts.\n"
        "2. Надішліть боту команду:\n`/track https://youtube.com/shorts/ваш_лінк` або `/track https://vt.tiktok.com/ваш_лінк`\n\n"
        "Бот почне щоденно відстежувати приріст переглядів і передавати успішні патерни в Google Gemini!"
    )


@client.on(events.NewMessage(pattern=r"^/stats(?:@\w+)?(?:\s+.*)?$"))
async def stats_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    hook_stats = get_learning_stats()
    highlight_stats = get_highlight_learning_stats()
    msg = format_stats_message(hook_stats, highlight_stats)

    buttons = [
        [
            Button.inline("🔄 Оновити", data="quick_cmd:stats"),
            Button.inline("🚀 Меню (/start)", data="quick_cmd:start")
        ]
    ]
    await event.reply(msg, buttons=buttons)


@client.on(events.CallbackQuery(pattern=r"^quick_cmd:(stats|start|profile|buy|ref)$"))
async def quick_cmd_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    cmd = event.data.decode("utf-8").split(":")[1]

    try:
        if cmd == "stats":
            hook_stats = get_learning_stats()
            highlight_stats = get_highlight_learning_stats()
            msg = format_stats_message(hook_stats, highlight_stats)
            buttons = [
                [
                    Button.inline("🔄 Оновити", data="quick_cmd:stats"),
                    Button.inline("🚀 Меню (/start)", data="quick_cmd:start")
                ]
            ]
            await event.edit(msg, buttons=buttons)
        elif cmd == "profile":
            user = db_get_or_create_user(sender_id, sender.username or "", sender.first_name or "")
            is_admin = (sender_id in ADMIN_IDS)
            ref_stats = db_get_referral_stats(sender_id)
            credits_val = "♾ Безліміт (Admin VIP)" if is_admin else f"**{user.get('credits_balance', 0)}** генерацій"
            tier_name = "🌟 Admin VIP" if is_admin else ("🚀 Monthly Unlimited" if db_has_active_subscription(user) else "⚡️ Free / Starter")
            earned = ref_stats.get("earned_credits", ref_stats.get("active_invited", 0) * 2)

            msg = (
                "👤 **Особистий кабінет клієнта:**\n\n"
                f"• **ID:** `{sender_id}`\n"
                f"• **Тарифний план:** {tier_name}\n"
                f"• **Залишок балансу:** {credits_val}\n"
                f"• **Витрачено зірочок:** `{user.get('total_spent_stars', 0)}` ⭐️\n\n"
                "👥 **Партнерська статистика:**\n"
                f"• Запрошено друзів: **{ref_stats.get('total_invited', 0)}**\n"
                f"• Активних: **{ref_stats.get('active_invited', 0)}**\n"
                f"• Зароблено бонусів: **+{earned}** кредитів\n"
            )
            buttons = [
                [
                    Button.inline("💳 Поповнити баланс (Stars)", data="quick_cmd:buy"),
                    Button.inline("👥 Моє реф-посилання", data="quick_cmd:ref")
                ]
            ]
            await event.edit(msg, buttons=buttons)
        elif cmd == "buy":
            msg = (
                "💎 **Тарифні плани та поповнення через Telegram Stars (⭐️):**\n\n"
                "1. 🌟 **Starter Pack:** `25 ⭐️`\n"
                "• **+10 відео** з повним AI-монтажем\n\n"
                "2. 🔥 **Creator Pro:** `50 ⭐️`\n"
                "• **+25 відео** + пріоритетна черга (VIP)\n\n"
                "3. 🚀 **Monthly Unlimited:** **`100 ⭐️` (Спец-пропозиція!)**\n"
                "• **Безлімітна кількість відео на 30 днів!**\n"
            )
            buttons = [
                [Button.inline("🌟 Starter Pack (10 відео) — 25 ⭐️", data="buy_plan:starter:25:10")],
                [Button.inline("🔥 Creator Pro (25 відео) — 50 ⭐️", data="buy_plan:pro:50:25")],
                [Button.inline("🚀 Monthly Unlimited (30 днів) — 100 ⭐️", data="buy_plan:unlimited:100:0")],
                [Button.inline("🔙 Назад", data="quick_cmd:profile")]
            ]
            await event.edit(msg, buttons=buttons)
        elif cmd == "ref":
            bot_me = await client.get_me()
            ref_link = f"https://t.me/{bot_me.username}?start=ref_{sender_id}"
            ref_stats = db_get_referral_stats(sender_id)
            
            active = ref_stats.get("active_invited", 0)
            total = ref_stats.get("total_invited", 0)
            
            bar_len = 10
            filled = min(bar_len, active)
            empty = bar_len - filled
            progress_bar = "█" * filled + "░" * empty

            if active >= 10:
                status_unlimited = "🎉 **ВАМ РОЗБЛОКОВАНО БЕЗЛІМІТНИЙ ДОСТУП НА 1 МІСЯЦЬ!**"
            else:
                status_unlimited = f"🎯 До безліміту: **{10 - active} активних друзів**"

            msg = (
                "👥 **Прогресивна реферальна програма 2.0:**\n\n"
                "🏆 **Шкала нагород:**\n"
                "• 1-й друг: `+1` йому, `+2` вам\n"
                "• 2-й друг: `+2` йому, `+3` вам\n"
                "• ...\n"
                "• **10-й друг: 🔥 БЕЗЛІМІТ НА 1 МІСЯЦЬ ДЛЯ ВАС!**\n\n"
                "📊 **Ваш прогрес:**\n"
                f"[{progress_bar}] **{active}/10 друзів**\n"
                f"{status_unlimited}\n\n"
                f"• Всього переходів: **{total}** | Активних: **{active}**\n"
                f"• За наступного ({active + 1}-го) друга: **{ref_stats.get('next_reward_you', '+2 відео')}**\n\n"
                f"🔗 **Ваше посилання:**\n`{ref_link}`"
            )
            buttons = [
                [Button.inline("🔄 Оновити", data="quick_cmd:ref")]
            ]
            await event.edit(msg, buttons=buttons)
        elif cmd == "start":
            user = db_get_or_create_user(sender_id, sender.username or "", sender.first_name or "")
            is_admin = (sender_id in ADMIN_IDS)
            credits_text = "♾ Безліміт (Admin)" if is_admin else f"{user.get('credits_balance', 0)} відео"
            welcome_text = (
                "🎬 **TikTok Video Processor + Gemini AI Hook & Cutter (Studio v2.0)!**\n\n"
                f"👤 **Ваш акаунт:** `{sender.first_name or 'Користувач'}`\n"
                f"💎 **Баланс генерацій:** **{credits_text}**\n\n"
                "📥 **Надішліть відео файлом або посиланням на TikTok, щоб почати!**\n"
                "💡 _(Для YouTube — завантажте ролик ботом-завантажувачем та перешліть файл сюди)_"

            )
            buttons = [
                [
                    Button.inline("💎 Профіль", data="quick_cmd:profile"),
                    Button.inline("💳 Купити тарифи", data="quick_cmd:buy")
                ],
                [
                    Button.inline("👥 Реферали", data="quick_cmd:ref"),
                    Button.inline("📊 Статистика ШІ", data="quick_cmd:stats")
                ]
            ]
            await event.edit(welcome_text, buttons=buttons)
    except Exception as err:
        logger.debug(f"quick_cmd_handler message edit notice: {err}")


@client.on(events.NewMessage(pattern=r"^/setchannel(?:\s+(.+))?$"))
async def set_channel_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    if sender_id not in ADMIN_IDS:
        await event.reply("⛔️ Доступ заборонено.")
        return

    global TARGET_ARCHIVE_CHANNEL
    target = event.pattern_match.group(1)
    if target:
        target = target.strip()
        TARGET_ARCHIVE_CHANNEL = target
        await event.reply(
            f"✅ **Telegram-канал для авто-постингу встановлено:** `{target}`\n\n"
            f"Тепер під кожним готовим відео буде кнопка **«📢 Опублікувати в Канал»**, яка миттєво відправлятиме відео разом із готовим заголовком, описом і хештегами в цей канал!"
        )
    else:
        current = TARGET_ARCHIVE_CHANNEL or "_Не налаштовано_"
        await event.reply(
            f"📢 **Поточний канал для авто-постингу:** {current}\n\n"
            f"👉 **Щоб підключити канал:**\n"
            f"1. Додайте цього бота в адміністратори вашого каналу.\n"
            f"2. Відправте команду:\n`/setchannel @username_вашого_каналу` або `/setchannel -100xxxxxxxxxx`"
        )


@client.on(events.NewMessage(pattern=r"^/(?:givevip|addvip)(?:\s+(\d+))?$"))
async def give_vip_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    if sender_id not in ADMIN_IDS:
        await event.reply("⛔️ Доступ заборонено. Ця команда доступна лише адміністраторам.")
        return

    target_str = event.pattern_match.group(1)
    if not target_str:
        await event.reply(
            "👑 **Команда видачі VIP НАЗАВЖДИ:**\n\n"
            "Використання:\n"
            "`/givevip <Telegram_ID>`\n\n"
            "Приклад:\n"
            "`/givevip 123456789`\n\n"
            "💡 _Щоб дізнатися ID користувачів, напишіть_ `/users`"
        )
        return

    try:
        target_id = int(target_str.strip())
        user_data = db_grant_lifetime_vip(target_id)
        
        await event.reply(
            f"👑 **VIP НАЗАВЖДИ УСПІШНО ВИДАНО!** ♾\n\n"
            f"👤 **Користувач ID:** `{target_id}`\n"
            f"⭐️ **Тариф:** `🌟 VIP Forever (Безліміт)`\n"
            f"💎 **Баланс генерацій:** `9999 відео`\n"
            f"📅 **Термін дії:** `Безстроково (назавжди)`"
        )

        # Try to notify user directly
        try:
            congrats_msg = (
                "🎉 **Вітаємо! Власник бота надав вам VIP-СТАТУС НАЗАВЖДИ!** 👑\n\n"
                "💎 **Ваші персональні привілеї:**\n"
                "• ♾ **Повний безліміт на всі генерації**\n"
                "• 🚀 **Максимальний пріоритет черги обробки**\n"
                "• 🎬 **Всі преміум-функції: хуки, караоке-субтитри, байти та обкладинки**\n\n"
                "Приємного користування нашою AI-студією!"
            )
            await client.send_message(target_id, congrats_msg)
        except Exception:
            pass
    except Exception as e:
        await event.reply(f"❌ Помилка видачі VIP: {e}")


@client.on(events.NewMessage(pattern=r"^/(?:removevip|delvip)(?:\s+(\d+))?$"))
async def remove_vip_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    if sender_id not in ADMIN_IDS:
        await event.reply("⛔️ Доступ заборонено.")
        return

    target_str = event.pattern_match.group(1)
    if not target_str:
        await event.reply("Використання: `/removevip <Telegram_ID>`")
        return

    try:
        target_id = int(target_str.strip())
        db_revoke_vip(target_id)
        await event.reply(f"🗑 **VIP-статус для користувача** `{target_id}` **успішно скасовано.**")
    except Exception as e:
        await event.reply(f"❌ Помилка скасування VIP: {e}")


@client.on(events.NewMessage(pattern=r"^/(?:users|admin)(?:@\w+)?$"))
async def admin_users_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    if sender_id not in ADMIN_IDS:
        await event.reply("⛔️ Доступ заборонено.")
        return

    try:
        users = db_get_all_users(limit=25)
        total_cnt = len(users)
        vip_cnt = sum(1 for u in users if u.get("tier") in ("admin", "vip_forever", "lifetime", "unlimited"))
        
        text_lines = [
            "👑 **Адмін-панель: Користувачі бота**\n",
            f"👥 Всього користувачів (останні): **{total_cnt}**",
            f"⭐️ Активних VIP: **{vip_cnt}**\n",
            "📋 **Список останніх зареєстрованих:**"
        ]

        for u in users[:15]:
            uid = u.get("user_id")
            uname = f"@{u['username']}" if u.get("username") else (u.get("first_name") or f"ID {uid}")
            tier = u.get("tier", "free")
            tier_icon = "👑 VIP Forever" if tier in ("vip_forever", "lifetime") else ("⭐️ VIP" if tier in ("unlimited", "admin") else "🆓 Free")
            bal = u.get("credits_balance", 0)
            text_lines.append(f"• `{uid}` | {uname} | {tier_icon} | 💎 {bal}")

        text_lines.append("\n💡 _Щоб видати VIP назавжди:_ `/givevip <ID>`")
        text_lines.append("💡 _Щоб забрати VIP:_ `/removevip <ID>`")
        await event.reply("\n".join(text_lines))
    except Exception as e:
        await event.reply(f"❌ Помилка завантаження списку: {e}")


processed_message_ids = set()


@client.on(events.NewMessage)
async def video_message_handler(event):
    if event.message.text and event.message.text.startswith("/"):
        return

    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    user = db_get_or_create_user(sender_id, sender.username or "", sender.first_name or "")
    is_admin = (sender_id in ADMIN_IDS)

    # Check credit balance
    if not is_admin and not db_has_active_subscription(user) and user.get("credits_balance", 0) <= 0:
        msg = (
            "⛔️ **У вас закінчилися безкоштовні кредити на монтаж!**\n\n"
            "💎 Поповніть баланс Telegram Stars або запросіть друзів за вашим реферальним посиланням і отримайте додаткові кредити безкоштовно!"
        )
        buttons = [
            [Button.inline("💳 Купити тарифи (Stars)", data="quick_cmd:buy")],
            [Button.inline("👥 Запросити друга (+2 кредити)", data="quick_cmd:ref")]
        ]
        await event.reply(msg, buttons=buttons)
        return

    msg_id = (event.chat_id, event.message.id)
    if msg_id in processed_message_ids:
        return
    processed_message_ids.add(msg_id)
    if len(processed_message_ids) > 500:
        processed_message_ids.clear()

    raw_text = event.message.text or ""

    # --- 0. Check TikTok URL ---
    tt_url = extract_tiktok_url(raw_text)
    if tt_url:
        status_fetch = await event.reply("🔍 **Зчитування інформації про TikTok відео...**")
        try:
            loop = asyncio.get_running_loop()
            tt_info = await loop.run_in_executor(None, get_tiktok_video_info, tt_url)
            if not tt_info:
                raise ValueError("Не вдалося отримати інформацію про TikTok відео.")
            
            title = tt_info.get("title", "TikTok Video")
            dur = tt_info.get("duration", 0.0)

            job_id = str(uuid.uuid4())[:8]
            pending_jobs[job_id] = {
                "type": "tiktok",
                "url": tt_url,
                "title": title,
                "chat_id": event.chat_id,
                "file_name": f"tiktok_{tt_info.get('id', job_id)}.mp4",
                "approx_duration": dur
            }

            logger.info(f"Received TikTok link: {tt_url}. Job ID: {job_id}, Duration: {dur}s")

            init_hook = (dur <= 180.0)
            buttons = build_mode_selection_keyboard(job_id, hook=init_hook, banner=True, bait=True, subs=True, target_platform="tiktok")
            prompt_text = format_control_panel_text(pending_jobs[job_id], target_platform="tiktok", hook=init_hook, banner=True, bait=True, subs=True)

            await status_fetch.edit(prompt_text, buttons=buttons)
            return
        except Exception as tt_err:
            await status_fetch.edit(f"❌ **Помилка зчитування TikTok відео:**\n`{str(tt_err)}`")
            return

    # --- 1. Check YouTube URL ---
    yt_url = extract_youtube_url(raw_text)
    if yt_url:
        guide_text = (
            "📥 **Як змонтувати це YouTube-відео:**\n\n"
            "Щоб завантажити відео у найвищій якості (до 1080p) без обмежень:\n\n"
            f"1. Скопіюйте посилання: `{yt_url}`\n"
            "2. Натисніть кнопку нижче та надішліть посилання боту **@allsaverbot**\n"
            "3. Перешліть або надішліть отримане відео сюди в чат.\n\n"
            "🎬 **Бот миттєво зробить повний AI-монтаж (Whisper AI ➔ Gemini ➔ караоке-субтитри ➔ байти ➔ обкладинки 9:16)!**"
        )
        buttons = [
            [Button.url("🚀 Завантажити відео через @allsaverbot", url="https://t.me/allsaverbot")]
        ]
        await event.reply(guide_text, buttons=buttons, link_preview=False)
        return




    # --- 2. Direct Telegram Video ---
    message = event.message
    if not (message.video or message.document):
        return

    is_video = False
    if message.video:
        is_video = True
    elif message.document:
        mime = getattr(message.document, "mime_type", "")
        if mime.startswith("video/"):
            is_video = True

    if not is_video:
        return

    file_name = "video.mp4"
    if message.file and message.file.name:
        file_name = message.file.name

    msg_dur = 0
    if message.video and getattr(message.video, "duration", 0):
        msg_dur = message.video.duration
    elif message.document:
        for attr in getattr(message.document, "attributes", []):
            if hasattr(attr, "duration") and attr.duration:
                msg_dur = attr.duration

    job_id = str(uuid.uuid4())[:8]
    pending_jobs[job_id] = {
        "type": "telegram_file",
        "message": message,
        "chat_id": event.chat_id,
        "file_name": file_name,
        "approx_duration": msg_dur
    }

    logger.info(f"Received video candidate for processing. Job ID: {job_id}, Approx duration: {msg_dur}s")

    init_hook = (msg_dur <= 180.0)
    buttons = build_mode_selection_keyboard(job_id, hook=init_hook, banner=True, bait=True, subs=True, target_platform="tiktok")
    prompt_text = format_control_panel_text(pending_jobs[job_id], target_platform="tiktok", hook=init_hook, banner=True, bait=True, subs=True)

    await event.reply(prompt_text, buttons=buttons)


@client.on(events.CallbackQuery(pattern=r"^rate:([a-zA-Z0-9_-]+):(\d+)$"))
async def rating_callback_handler(event):
    match = re.match(r"^rate:([a-zA-Z0-9_-]+):(\d+)$", event.data.decode("utf-8"))
    if not match:
        return

    job_id = match.group(1)
    rating = int(match.group(2))

    highlight_record = record_highlight_rating(job_id, rating)
    hook_record = record_user_rating(job_id, rating)

    hook_stats = get_learning_stats()
    highlight_stats = get_highlight_learning_stats()

    if highlight_record:
        level_info = HIGHLIGHT_RATING_DEFINITIONS.get(rating, {})
        target_name = "✂️ Нарізка з довгого відео"
    else:
        level_info = RATING_LEVEL_DEFINITIONS.get(rating, {})
        target_name = "🎣 AI Хук"

    level_title = level_info.get("title", f"{rating}/10")
    level_desc = level_info.get("desc", "")
    gemini_action = level_info.get("gemini_directive", "")

    await event.answer(f"Оцінка {rating}/10 зафіксована!", alert=False)

    try:
        await event.edit(buttons=[
            [Button.inline(f"✅ Зафіксовано: {rating}/10 ⭐️ ({target_name})", data=f"rated_done:{job_id}")]
        ])
    except Exception:
        pass

    await event.respond(
        f"🎯 **Ваша оцінка зафіксована:** {level_title}\n"
        f"📌 **Категорія:** {target_name}\n\n"
        f"📋 **Критерій оцінки:** _{level_desc}_\n\n"
        f"🧠 **Вплив на калібрування Gemini:**\n`{gemini_action}`\n\n"
        f"📊 **Поточна статистика бази знань Gemini:**\n"
        f"• Оцінено нарізок: **{highlight_stats['total_rated']}** (сер. бал: `{highlight_stats['avg_rating']}/10 ⭐️`)\n"
        f"• Оцінено хуків: **{hook_stats['total_rated']}** (сер. бал: `{hook_stats['avg_rating']}/10 ⭐️`)\n\n"
        f"👉 Повна матриця навчання: /stats"
    )


@client.on(events.CallbackQuery(pattern=r"^cfg:([a-zA-Z0-9_-]+):([01]):([01]):([01]):([01]):(tt|yt|tiktok|youtube_shorts)$"))
async def config_toggle_handler(event):
    match = re.match(r"^cfg:([a-zA-Z0-9_-]+):([01]):([01]):([01]):([01]):(tt|yt|tiktok|youtube_shorts)$", event.data.decode("utf-8"))
    if not match:
        return

    job_id = match.group(1)
    hook = bool(int(match.group(2)))
    banner = bool(int(match.group(3)))
    bait = bool(int(match.group(4)))
    subs = bool(int(match.group(5)))
    plat_raw = match.group(6)
    target_platform = "tiktok" if plat_raw in ("tt", "tiktok") else "youtube_shorts"

    buttons = build_mode_selection_keyboard(job_id, hook=hook, banner=banner, bait=bait, subs=subs, target_platform=target_platform)
    job_data = pending_jobs.get(job_id, {})
    new_text = format_control_panel_text(job_data, target_platform=target_platform, hook=hook, banner=banner, bait=bait, subs=subs)

    plat_name = "TikTok" if target_platform == "tiktok" else "YouTube Shorts"
    try:
        await event.edit(new_text, buttons=buttons)
        await event.answer(f"Платформа: {plat_name} | Хук: {'ВКЛ' if hook else 'ВИКЛ'} | Банер: {'ВКЛ' if banner else 'ВИКЛ'} | Байт: {'ВКЛ' if bait else 'ВИКЛ'} | Саби: {'ВКЛ' if subs else 'ВИКЛ'}")
    except Exception as e:
        logger.warning(f"Failed to edit message in config_toggle: {e}")


@client.on(events.CallbackQuery(pattern=r"^proc:([a-zA-Z0-9_-]+):([01]|hook_yes|hook_no):([01]|banner_yes|banner_no):([01]|bait_yes|bait_no):([01]|subs_yes|subs_no)(?::(tt|yt|tiktok|youtube_shorts))?$"))
async def mode_selection_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    match = re.match(r"^proc:([a-zA-Z0-9_-]+):([01]|hook_yes|hook_no):([01]|banner_yes|banner_no):([01]|bait_yes|bait_no):([01]|subs_yes|subs_no)(?::(tt|yt|tiktok|youtube_shorts))?$", event.data.decode("utf-8"))
    if not match:
        return

    job_id = match.group(1)
    hook_val = match.group(2)
    banner_val = match.group(3)
    bait_val = match.group(4)
    subs_val = match.group(5)
    plat_raw = match.group(6) or "tt"

    if job_id not in pending_jobs:
        await event.answer("⚠️ Це завдання застаріло або вже виконується.", alert=True)
        return

    # Check & Deduct Credit
    is_admin = (sender_id in ADMIN_IDS)
    c_ok, rem = db_deduct_credit(sender_id, is_admin=is_admin)
    if not c_ok:
        await event.answer("⛔️ Недостатньо кредитів!", alert=True)
        await event.respond(
            "⛔️ **У вас закінчилися кредити на монтаж!**\n\n"
            "💎 Поповніть баланс Telegram Stars (/buy) або запросіть друга (/ref) для безкоштовних відео!"
        )
        return

    include_hook = (hook_val in ("1", "hook_yes"))
    include_banner = (banner_val in ("1", "banner_yes"))
    include_bait = (bait_val in ("1", "bait_yes"))
    include_subs = (subs_val in ("1", "subs_yes"))
    target_platform = "tiktok" if plat_raw in ("tt", "tiktok") else "youtube_shorts"

    job_data = pending_jobs.pop(job_id)
    platform_label = "🎵 TikTok" if target_platform == "tiktok" else "🔴 YouTube Shorts"
    hook_label = "🎣 З хуком" if include_hook else "⚡️ Без хука"
    banner_label = "📢 З банером" if include_banner else "🚀 Без банера"
    bait_label = "🪤 З байтом" if include_bait else "🛡 Без байта"
    subs_label = "🔤 З субтитрами" if include_subs else "⚪️ Без субтитрів"
    mode_name = f"[{platform_label}] {hook_label} + {banner_label} + {bait_label} + {subs_label}"

    await event.edit(f"⏳ **Обрано:** {mode_name}\nДодано в чергу обробки...", buttons=None)

    status_msg = await event.reply("📥 **Завдання стає в чергу...**")

    # Record job in database
    db_create_job(
        job_id=job_id,
        user_id=sender_id,
        media_type=job_data.get("type", "telegram_file"),
        title=job_data.get("title") or job_data.get("file_name", "Відео"),
        duration_sec=job_data.get("approx_duration", 0.0)
    )

    queue_pos = job_queue.qsize() + 1
    if queue_pos > 1:
        await status_msg.edit(
            f"⏳ **Режим:** {mode_name}\n\n"
            f"📊 Ваше відео додано в чергу (Позиція: **#{queue_pos}**).\n"
            f"⚙️ Обробка почнеться автоматично, як тільки звільниться воркер."
        )

    # Dispatch to worker queue
    await job_queue.put({
        "job_id": job_id,
        "job_data": job_data,
        "include_hook": include_hook,
        "include_banner": include_banner,
        "include_bait": include_bait,
        "include_subs": include_subs,
        "target_platform": target_platform,
        "mode_name": mode_name,
        "event": event,
        "status_msg": status_msg
    })
    logger.info(f"Enqueued job {job_id} into processing queue (Queue size: {job_queue.qsize()})")


async def execute_video_job(task_info: dict, worker_id: int):
    """Worker job execution unit with structured stage timers and telemetry."""
    job_id = task_info["job_id"]
    job_data = task_info["job_data"]
    include_hook = task_info["include_hook"]
    include_banner = task_info["include_banner"]
    include_bait = task_info["include_bait"]
    include_subs = task_info["include_subs"]
    target_platform = task_info["target_platform"]
    mode_name = task_info["mode_name"]
    event = task_info["event"]
    status_msg = task_info["status_msg"]

    job_type = job_data.get("type", "telegram_file")
    file_name = job_data["file_name"]
    platform_label = "🎵 TikTok" if target_platform == "tiktok" else "🔴 YouTube Shorts"

    t_start_job = time.time()
    db_update_job_status(job_id, "PROCESSING")
    logger.info(f"[Worker #{worker_id}] Starting execution for Job ID: {job_id}")

    job_dir = TEMP_STORAGE_DIR / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / f"input_{file_name}"

    try:
        loop = asyncio.get_running_loop()

        # --- STEP 1: Download Media ---
        t_dl_start = time.time()
        if job_type == "youtube":
            yt_url = job_data["url"]
            await status_msg.edit(
                f"📥 **Режим:** {mode_name}\n\n"
                f"**[Воркер #{worker_id}] Завантаження відео з YouTube (до 1080p)...**\n"
                f"📌 _{job_data.get('title', 'YouTube Video')}_"
            )
            yt_time = [0.0]
            def yt_progress_cb(current, total):
                asyncio.run_coroutine_threadsafe(
                    update_progress_message(
                        status_msg, f"📥 **Режим:** {mode_name}\n**Завантаження з YouTube...**", current, total, yt_time
                    ),
                    loop
                )

            await loop.run_in_executor(None, download_youtube_video, yt_url, str(input_path), yt_progress_cb)
        elif job_type == "tiktok":
            tt_url = job_data["url"]
            await status_msg.edit(
                f"📥 **Режим:** {mode_name}\n\n"
                f"**[Воркер #{worker_id}] Завантаження відео з TikTok без водяного знаку...**\n"
                f"📌 _{job_data.get('title', 'TikTok Video')}_"
            )
            tt_time = [0.0]
            def tt_progress_cb(current, total):
                asyncio.run_coroutine_threadsafe(
                    update_progress_message(
                        status_msg, f"📥 **Режим:** {mode_name}\n**Завантаження з TikTok...**", current, total, tt_time
                    ),
                    loop
                )

            await loop.run_in_executor(None, download_tiktok_video, tt_url, str(input_path), tt_progress_cb)
        else:
            orig_message = job_data["message"]
            progress_time = [0.0]
            def download_callback(current, total):
                asyncio.create_task(update_progress_message(
                    status_msg, f"📥 **Режим:** {mode_name}\n**Завантаження відео на сервер...**", current, total, progress_time
                ))

            await orig_message.download_media(file=str(input_path), progress_callback=download_callback)

        dl_duration = time.time() - t_dl_start
        logger.info(f"[Worker #{worker_id}] Job {job_id} downloaded in {dl_duration:.2f}s")

        media_info = get_media_info(str(input_path))
        raw_duration = media_info["duration"] or 10.0
        is_long_video = (raw_duration > 180.0)
        target_clips = calculate_clips_count(raw_duration)

        # --- STEP 2: Processing (Long Multi-clip vs Single Video) ---
        if is_long_video and target_clips > 1:
            await status_msg.edit(
                f"🤖 **Режим:** {mode_name}\n\n"
                f"**Крок 1/3: AI-аналіз стенограми для {platform_label} ({raw_duration/60:.1f} хв)...**\n"
                f"• Компресія аудіо та транскрибація Whisper...\n"
                f"• Пошук {target_clips} вірусних кульмінацій через Gemini..."
            )

            t_ai_start = time.time()
            audio_mp3_path = job_dir / "compressed_audio.mp3"
            await loop.run_in_executor(None, extract_compressed_audio, str(input_path), str(audio_mp3_path))

            segments = await loop.run_in_executor(None, transcribe_audio_for_highlights, str(audio_mp3_path), GROQ_API_KEY)
            highlights = await loop.run_in_executor(None, find_viral_highlights, segments, raw_duration, target_clips, GEMINI_API_KEY, target_platform)
            ai_dur = time.time() - t_ai_start
            logger.info(f"[Worker #{worker_id}] Job {job_id} Whisper+Gemini analysis in {ai_dur:.2f}s ({len(highlights)} highlights found)")

            await status_msg.edit(
                f"✂️ **Знайдено {len(highlights)} вірусних моментів!**\n\n"
                f"Починаю створення та унікалізацію окремих {platform_label} 9:16 нарізок (GPU Hardware Acceleration)..."
            )

            for idx, h in enumerate(highlights, 1):
                clip_job_id = f"{job_id}_{idx}"
                clip_raw_path = job_dir / f"raw_clip_{idx}.mp4"
                clip_out_path = job_dir / f"clip_{idx}_{target_platform}_916_{job_id}.mp4"

                await status_msg.edit(
                    f"⏳ **Обробка нарізки [{idx}/{len(highlights)}]:** _{h['title']}_\n"
                    f"• Хронометраж: `[{h['start']:.1f}s - {h['end']:.1f}s]`\n"
                    f"• Платформа: {platform_label} + Байт + Унікалізація..."
                )

                await slice_raw_segment_async(str(input_path), h["start"], h["end"], str(clip_raw_path))
                save_highlight_decision(clip_job_id, h, segments)

                seo_prompt_ctx = f"Highlight: {h.get('title', '')}. Summary: {h.get('reason', '')}"
                seo_data = await loop.run_in_executor(
                    None,
                    generate_viral_seo_meta,
                    seo_prompt_ctx,
                    target_platform,
                    GEMINI_API_KEY
                )

                res = await process_video_pipeline_async(
                    str(clip_raw_path),
                    str(clip_out_path),
                    str(BANNER_PATH),
                    str(BAIT_PATH),
                    GROQ_API_KEY,
                    GEMINI_API_KEY,
                    include_banner,
                    include_hook,
                    include_bait,
                    include_subs,
                    target_platform,
                    h.get("suggested_cta", ""),
                    clip_job_id
                )

                hook = res.get("hook_info", {})
                hook_quote = hook.get("quote", "Найяскравіший момент")
                hook_reason = hook.get("reason", "ШІ обрав цей момент як вірусний хук")
                hook_start = hook.get("start", 0.0)
                hook_end = hook.get("end", 2.5)

                bait = res.get("bait_info", {})
                if bait.get("applied") and include_bait:
                    if target_platform == "youtube_shorts" and bait.get("timings"):
                        timings_str = ", ".join([f"`{t:.0f}с`" for t in bait["timings"]])
                        bait_detail = f"🪤 **Байти (YouTube Shorts):** на {timings_str} (_{bait.get('text', '')}_)\n"
                    else:
                        b_start = bait.get("start", 0.0) or 0.0
                        bait_detail = f"🪤 **Байт (TikTok):** на `{b_start:.1f}` сек (_{bait.get('text', '')}_)\n"
                else:
                    bait_detail = "🛡 **Байт:** _Вимкнено_\n"

                banner_detail = f"📢 **Вставка банера:** на `{res['timings']}` сек (стоп-кадр)\n" if include_banner else "🚀 **Рекламний банер:** _Вимкнено (чистий геймплей)_\n"
                subs_detail = "🔤 **Субтитри (AI):** _Накладено караоке-слова (Auto-Wrap)_\n" if res.get("subtitles_applied") else ""

                chan_label = f"📢 Опублікувати в Канал ({TARGET_ARCHIVE_CHANNEL})" if TARGET_ARCHIVE_CHANNEL else "📢 Опублікувати в Канал"
                
                # Action Buttons: Publish + Thumbnail + View Tracker + Rating
                rating_buttons = [
                    [
                        Button.inline("🖼 Завантажити обкладинку 9:16", data=f"gen_thumb:{clip_job_id}"),
                        Button.inline("📊 Відстежувати перегляди", data=f"track_prompt:{clip_job_id}")
                    ],
                    [
                        Button.inline(chan_label, data=f"post_chan:{clip_job_id}")
                    ],
                    [
                        Button.inline("1 ⛔️", data=f"rate:{clip_job_id}:1"),
                        Button.inline("2 🚫", data=f"rate:{clip_job_id}:2"),
                        Button.inline("3 ❌", data=f"rate:{clip_job_id}:3"),
                        Button.inline("4 ⚠️", data=f"rate:{clip_job_id}:4"),
                        Button.inline("5 ⚖️", data=f"rate:{clip_job_id}:5")
                    ],
                    [
                        Button.inline("6 👌", data=f"rate:{clip_job_id}:6"),
                        Button.inline("7 👍", data=f"rate:{clip_job_id}:7"),
                        Button.inline("8 ✨", data=f"rate:{clip_job_id}:8"),
                        Button.inline("9 🔥", data=f"rate:{clip_job_id}:9"),
                        Button.inline("10 🌟", data=f"rate:{clip_job_id}:10")
                    ]
                ]

                if include_hook:
                    hook_detail = (
                        f"🎣 **AI Hook (0:00 - {hook_end - hook_start:.1f}s):**\n"
                        f"• Момент: _{hook_quote}_\n"
                        f"• Причина: {hook_reason}\n\n"
                    )
                    rating_prompt = (
                        "⭐️ **Оцініть якість нарізки та хука (від 1-найгірше до 10-ідеально):**\n"
                        "_(Оцінка автоматично зберігається в SQLite та калібрує Gemini)_"
                    )
                else:
                    hook_detail = "⚡️ **AI Hook:** _Вимкнено (нарізка починається з природного початку фрази)_\n\n"
                    rating_prompt = (
                        "⭐️ **Оцініть точність та вірусність нарізки (від 1-найгірше до 10-ідеально):**\n"
                        "_(Оцінка автоматично зберігається в SQLite та калібрує Gemini)_"
                    )

                score_info = f"🔥 **Viral Score:** `{h.get('viral_coefficient', 9.0)}/10` (Visual: `{h.get('visual_action_score', 8)}/10`, Audio: `{h.get('audio_emotion_score', 8)}/10`)\n" if h.get("viral_coefficient") else ""

                clip_caption = (
                    f"🎬 **{platform_label} Нарізка [{idx}/{len(highlights)}]: {h['title']}**\n\n"
                    f"⏱ **Хронометраж в оригіналі:** `{h['start']:.1f}s — {h['end']:.1f}s` (кліп: `{res['final_duration']:.1f}с`)\n"
                    f"{score_info}"
                    f"💡 **Чому обрано:** {h['reason']}\n\n"
                    f"{hook_detail}"
                    f"📐 **Формат:** 9:16 Vertical (`{res['resolution']}`)\n"
                    f"{banner_detail}"
                    f"{bait_detail}"
                    f"{subs_detail}"
                    f"⚡️ **Прискорення:** `{res['main_speed']}x`\n"
                    f"🛡 **Унікалізація:** Зум `{res['zoom_percent']}%`, FPS `{res['target_fps']}`, Pitch `{res['pitch_ratio']}`\n\n"
                    f"{rating_prompt}"
                )

                out_info = get_media_info(str(clip_out_path))
                sent_video = await client.send_file(
                    event.chat_id,
                    file=str(clip_out_path),
                    caption=clip_caption,
                    buttons=rating_buttons,
                    supports_streaming=True,
                    attributes=[DocumentAttributeVideo(
                        duration=int(out_info["duration"]),
                        w=out_info["width"],
                        h=out_info["height"],
                        supports_streaming=True
                    )]
                )

                completed_clips_meta[clip_job_id] = {
                    "media": sent_video.media,
                    "video_file_path": str(clip_out_path),
                    "seo_data": seo_data,
                    "target_platform": target_platform,
                    "title": h.get("title", "")
                }

                await sent_video.reply(format_seo_telegram_block(seo_data, target_platform))

            await status_msg.edit(f"🎉 **Всі {len(highlights)} нарізок успішно створено та надіслано в чат!**")
            logger.info(f"[Worker #{worker_id}] Multi-clip job {job_id} finished in {time.time() - t_start_job:.1f}s")
            db_update_job_status(job_id, "COMPLETED")

        # --- Single Video Branch ---
        else:
            output_path = job_dir / f"output_{target_platform}_916_{job_id}.mp4"

            if include_hook:
                await status_msg.edit(
                    f"🤖 **Режим:** {mode_name}\n\n"
                    "**Крок 1/2: AI-аналіз (Groq Whisper + Google Gemini)**\n"
                    f"• Транскрибація діалогів через Whisper...\n"
                    f"• Пошук хука та адаптація CTA для {platform_label}...\n"
                    "• Калібрування за 1-10 матрицею оцінок..."
                )
            else:
                await status_msg.edit(
                    f"🤖 **Режим:** {mode_name}\n\n"
                    "**Крок 1/2: Обробка відео**\n"
                    f"• Форматування під 9:16 Vertical для {platform_label}...\n"
                    "• Байт на збереження + унікалізація..."
                )

            res = await process_video_pipeline_async(
                str(input_path),
                str(output_path),
                str(BANNER_PATH),
                str(BAIT_PATH),
                GROQ_API_KEY,
                GEMINI_API_KEY,
                include_banner,
                include_hook,
                include_bait,
                include_subs,
                target_platform,
                "",
                job_id
            )

            hook = res.get("hook_info", {})
            hook_quote = hook.get("quote", "Найяскравіший момент")
            hook_reason = hook.get("reason", "ШІ обрав цей момент як вірусний хук")
            hook_start = hook.get("start", 0.0)
            hook_end = hook.get("end", 2.5)
            vc = hook.get("viral_coefficient")
            single_score_info = f"🔥 **Viral Score:** `{vc}/10` (Visual: `{hook.get('visual_action_score', 8)}/10`, Audio: `{hook.get('audio_emotion_score', 8)}/10`)\n" if vc else ""

            seo_ctx = hook_quote if hook_quote else job_data.get("title", "Game highlight")
            seo_data = await loop.run_in_executor(
                None,
                generate_viral_seo_meta,
                seo_ctx,
                target_platform,
                GEMINI_API_KEY
            )

            bait = res.get("bait_info", {})
            if bait.get("applied") and include_bait:
                if target_platform == "youtube_shorts" and bait.get("timings"):
                    timings_str = ", ".join([f"`{t:.0f}с`" for t in bait["timings"]])
                    bait_detail = f"🪤 **Байти (YouTube Shorts):** на {timings_str} (_{bait.get('text', '')}_)\n"
                else:
                    b_start = bait.get("start", 0.0) or 0.0
                    bait_detail = f"🪤 **Байт (TikTok):** на `{b_start:.1f}` сек (_{bait.get('text', '')}_)\n"
            else:
                bait_detail = "🛡 **Байт:** _Вимкнено_\n"

            subs_detail = "🔤 **Субтитри (AI):** _Накладено караоке-слова (Auto-Wrap)_\n" if res.get("subtitles_applied") else ""

            hook_step_txt = f"• 🎣 AI Hook додано: `[{hook_start:.1f}s - {hook_end:.1f}s]`" if include_hook else "• ⚡️ Відео сформовано без хука"
            await status_msg.edit(
                f"📤 **Режим:** {mode_name}\n\n"
                f"**Крок 2/2: Вивантаження готового {platform_label} 9:16 відео в Telegram...**\n"
                f"{hook_step_txt}"
            )

            out_info = get_media_info(str(output_path))
            out_width = out_info["width"]
            out_height = out_info["height"]
            out_duration = int(out_info["duration"])

            upload_progress_time = [0.0]
            def upload_callback(current, total):
                asyncio.create_task(update_progress_message(
                    status_msg, f"📤 **Режим:** {mode_name}\n**Вивантаження готового відео...**", current, total, upload_progress_time
                ))

            banner_detail = f"📢 **Вставка банера:** на `{res['timings']}` сек (стоп-кадр)\n" if include_banner else "🚀 **Рекламний банер:** _Вимкнено (чистий геймплей)_\n"
            chan_label = f"📢 Опублікувати в Канал ({TARGET_ARCHIVE_CHANNEL})" if TARGET_ARCHIVE_CHANNEL else "📢 Опублікувати в Канал"

            if include_hook:
                hook_detail = (
                    f"🎣 **AI Hook (0:00 - {hook_end - hook_start:.1f}s):**\n"
                    f"{single_score_info}"
                    f"• Момент: _{hook_quote}_\n"
                    f"• Причина: {hook_reason}\n\n"
                )
                rating_prompt = (
                    "⭐️ **Оцініть точність вибору хука (від 1-найгірший до 10-ідеальний):**\n"
                    "_(Кожна оцінка автоматично калібрує Google Gemini в SQLite)_"
                )
                rating_buttons = [
                    [
                        Button.inline("🖼 Завантажити обкладинку 9:16", data=f"gen_thumb:{job_id}"),
                        Button.inline("📊 Відстежувати перегляди", data=f"track_prompt:{job_id}")
                    ],
                    [
                        Button.inline(chan_label, data=f"post_chan:{job_id}")
                    ],
                    [
                        Button.inline("1 ⛔️", data=f"rate:{job_id}:1"),
                        Button.inline("2 🚫", data=f"rate:{job_id}:2"),
                        Button.inline("3 ❌", data=f"rate:{job_id}:3"),
                        Button.inline("4 ⚠️", data=f"rate:{job_id}:4"),
                        Button.inline("5 ⚖️", data=f"rate:{job_id}:5")
                    ],
                    [
                        Button.inline("6 👌", data=f"rate:{job_id}:6"),
                        Button.inline("7 👍", data=f"rate:{job_id}:7"),
                        Button.inline("8 ✨", data=f"rate:{job_id}:8"),
                        Button.inline("9 🔥", data=f"rate:{job_id}:9"),
                        Button.inline("10 🌟", data=f"rate:{job_id}:10")
                    ]
                ]
            else:
                hook_detail = "⚡️ **AI Hook:** _Вимкнено (відео починається одразу з основного геймплею)_\n\n"
                rating_prompt = f"✨ _Відео повністю готове до публікації в {platform_label}!_"
                rating_buttons = [
                    [
                        Button.inline("🖼 Завантажити обкладинку 9:16", data=f"gen_thumb:{job_id}"),
                        Button.inline("📊 Відстежувати перегляди", data=f"track_prompt:{job_id}")
                    ],
                    [
                        Button.inline(chan_label, data=f"post_chan:{job_id}")
                    ]
                ]

            caption = (
                f"✅ **Відео успішно оброблено для {platform_label}!**\n\n"
                f"{hook_detail}"
                f"📐 **Формат:** 9:16 Vertical (`{res['resolution']}`)\n"
                f"⏱ **Фінальна тривалість:** `{res['final_duration']:.1f} сек`\n"
                f"{banner_detail}"
                f"{bait_detail}"
                f"{subs_detail}"
                f"⚡️ **Прискорення:** `{res['main_speed']}x`\n"
                f"🛡 **Унікалізація:** Зум `{res['zoom_percent']}%`, FPS `{res['target_fps']}`, Pitch `{res['pitch_ratio']}`\n\n"
                f"{rating_prompt}"
            )

            sent_video = await client.send_file(
                event.chat_id,
                file=str(output_path),
                caption=caption,
                buttons=rating_buttons,
                supports_streaming=True,
                attributes=[DocumentAttributeVideo(
                    duration=out_duration,
                    w=out_width,
                    h=out_height,
                    supports_streaming=True
                )],
                progress_callback=upload_callback
            )

            completed_clips_meta[job_id] = {
                "media": sent_video.media,
                "video_file_path": str(output_path),
                "seo_data": seo_data,
                "target_platform": target_platform,
                "title": job_data.get("title", "")
            }

            await sent_video.reply(format_seo_telegram_block(seo_data, target_platform))
            await status_msg.delete()
            logger.info(f"[Worker #{worker_id}] Job {job_id} completed successfully in {time.time() - t_start_job:.1f}s")
            db_update_job_status(job_id, "COMPLETED")

    except Exception as e:
        logger.exception(f"[Worker #{worker_id}] Error processing job {job_id}")
        db_update_job_status(job_id, "FAILED", error_message=str(e))
        try:
            await status_msg.edit(f"❌ **Помилка під час обробки відео:**\n`{str(e)}`")
        except Exception:
            await event.reply(f"❌ **Помилка:** `{str(e)}`")

    finally:
        pass


async def worker_loop(worker_id: int):
    """Background consumer loop for video processing queue."""
    logger.info(f"🚀 Worker #{worker_id} started and ready for processing jobs.")
    while True:
        try:
            task_info = await job_queue.get()
        except asyncio.CancelledError:
            break

        try:
            logger.info(f"Worker #{worker_id} picked up task {task_info.get('job_id')}")
            await execute_video_job(task_info, worker_id)
        except asyncio.CancelledError:
            job_queue.task_done()
            break
        except Exception as e:
            logger.exception(f"Worker #{worker_id} unexpected error: {e}")
        finally:
            job_queue.task_done()


@client.on(events.CallbackQuery(pattern=r"^post_chan:([a-zA-Z0-9_-]+)$"))
async def post_to_channel_handler(event):
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id

    if sender_id not in ADMIN_IDS:
        await event.answer("⛔️ Доступ заборонено.", alert=True)
        return

    clip_job_id = event.pattern_match.group(1)
    meta = completed_clips_meta.get(clip_job_id)
    if not meta or not meta.get("media"):
        await event.answer("⚠️ Медіа застаріло або недоступне.", alert=True)
        return

    if not TARGET_ARCHIVE_CHANNEL:
        await event.answer("⚠️ Канал не налаштований! Вкажіть його: /setchannel @username", alert=True)
        return

    try:
        seo = meta.get("seo_data", {})
        title = seo.get("viral_title", "ЭПИЧНЫЙ МОМЕНТ 🔥")
        desc = seo.get("description", "")
        tags = " ".join(seo.get("hashtags", []))
        pin = seo.get("pinned_comment", "")

        post_caption = f"🔥 **{title}**\n\n{desc}\n\n{tags}"
        if pin:
            post_caption += f"\n\n💬 _{pin}_"

        await client.send_file(
            TARGET_ARCHIVE_CHANNEL,
            file=meta["media"],
            caption=post_caption,
            supports_streaming=True
        )
        await event.answer(f"🚀 Опубліковано в канал {TARGET_ARCHIVE_CHANNEL}!", alert=True)
    except Exception as err:
        logger.error(f"Error publishing to channel: {err}")
        await event.answer(f"❌ Помилка публікації: {err}", alert=True)


async def main():
    logger.info("Starting Telegram Client with Bot Token...")
    logger.info(f"Configured Admin IDs: {ADMIN_IDS}")
    logger.info(f"Configured Banner Path: {BANNER_PATH}")
    logger.info(f"Configured Bait Path: {BAIT_PATH}")
    logger.info("Configured AI Models: Groq whisper-large-v3 & Google Gemini (gemini-3.6-flash)")

    # Probe and log active video hardware acceleration
    encoder_flags = detect_best_h264_encoder()
    logger.info(f"Active FFmpeg Video Acceleration Flags: {encoder_flags}")

    # Start Telegram WebApp background server on 0.0.0.0 and dynamic PORT (Render requirement)
    try:
        web_port = int(os.getenv("PORT", 8085))
        asyncio.create_task(start_webapp_server("0.0.0.0", web_port))
        logger.info(f"Binding HTTP WebApp server to 0.0.0.0:{web_port}")
    except Exception as web_err:
        logger.warning(f"Could not start WebApp server: {web_err}")

    # Launch background worker pool
    for w_id in range(1, MAX_CONCURRENT_WORKERS + 1):
        t = asyncio.create_task(worker_loop(w_id))
        active_workers.append(t)

    await client.connect()
    if not await client.is_user_authorized():
        await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    logger.info(f"Bot connected: @{me.username} (ID: {me.id})")

    # Register Telegram Command Menu
    try:
        await client(SetBotCommandsRequest(
            scope=BotCommandScopeDefault(),
            lang_code="",
            commands=[
                BotCommand(command="start", description="🚀 Головне меню"),
                BotCommand(command="profile", description="👤 Особистий кабінет & Баланс"),
                BotCommand(command="buy", description="💳 Купити тарифи (Telegram Stars)"),
                BotCommand(command="ref", description="👥 Партнерська програма (Реферали)"),
                BotCommand(command="track", description="📊 Моніторинг переглядів відео"),
                BotCommand(command="myviews", description="👀 Моя аналітика переглядів"),
                BotCommand(command="stats", description="🧠 Матриця навчання Gemini (1-10)"),
            ]
        ))
        logger.info("Telegram command menu registered.")
    except Exception as cmd_err:
        logger.warning(f"Failed to register bot commands: {cmd_err}")

    try:
        await client.run_until_disconnected()
    finally:
        for w in active_workers:
            w.cancel()


if __name__ == "__main__":
    asyncio.run(main())
