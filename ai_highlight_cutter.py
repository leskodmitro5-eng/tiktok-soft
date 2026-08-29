import os
import re
import json
import logging
import subprocess
from pathlib import Path
from groq import Groq
from google import genai
from google.genai import types
import imageio_ffmpeg

logger = logging.getLogger("HighlightCutter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")


def calculate_clips_count(duration_sec: float) -> int:
    """
    Calculates target clips count based on source duration:
    - <= 180s (3 min): 1 clip (standard pipeline)
    - 180s - 300s (3-5 min): 3 clips
    - 300s - 480s (5-8 min): 4-5 clips
    - 480s - 900s (8-15 min): 6-7 clips
    - > 900s (15+ min): 8-10 clips
    """
    if duration_sec <= 180.0:
        return 1
    elif duration_sec <= 300.0:
        return 3
    elif duration_sec <= 480.0:
        return 4
    elif duration_sec <= 900.0:
        return 6
    else:
        calculated = int(duration_sec // 120)
        return min(10, max(7, calculated))


def extract_compressed_audio(video_path: str, output_mp3_path: str) -> str:
    """Extracts lightweight mono 32kbps MP3 audio to stay well within Groq 25MB file size limit."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "32k",
        output_mp3_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    logger.info(f"Extracted compressed audio -> {output_mp3_path} (size: {os.path.getsize(output_mp3_path)/1024:.1f} KB)")
    return output_mp3_path


def transcribe_audio_for_highlights(audio_path: str, groq_api_key: str) -> list[dict]:
    """Transcribes audio using Groq whisper-large-v3 and returns timestamped speech segments."""
    client = Groq(api_key=groq_api_key)
    with open(audio_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=(Path(audio_path).name, f.read()),
            model="whisper-large-v3",
            response_format="verbose_json",
            temperature=0.0
        )

    segments_data = getattr(transcription, "segments", [])
    result = []
    for s in segments_data:
        s_dict = s if isinstance(s, dict) else s.__dict__
        result.append({
            "start": round(float(s_dict.get("start", 0.0)), 2),
            "end": round(float(s_dict.get("end", 0.0)), 2),
            "text": str(s_dict.get("text", "")).strip()
        })
    logger.info(f"Groq Whisper transcribed {len(result)} speech segments.")
    return result


from hook_learner import get_high_and_low_scores_context
from highlight_learner import get_high_and_low_highlight_scores_context


def find_viral_highlights(
    segments: list[dict],
    total_duration: float,
    target_clips_count: int,
    gemini_api_key: str,
    target_platform: str = "tiktok"
) -> list[dict]:
    """
    Uses Google Gemini (gemini-3.6-flash) to analyze speech timeline and find target_clips_count
    viral highlight slices (duration ~30-60s each) with logical dialogue boundaries, emotional peaks,
    multimodal awareness, and platform-specific CTA directives.
    """
    default_cta = "Хочешь также? Пиши в комменты 👇" if target_platform == "youtube_shorts" else "Закинь в сохраненки, чтобы получить этот скин!"
    
    if target_clips_count <= 1:
        return [{
            "index": 1,
            "start": 0.0,
            "end": round(total_duration, 2),
            "start_time": 0.0,
            "end_time": round(total_duration, 2),
            "title": "Повне відео",
            "hook_start": 0.0,
            "hook_end": min(2.5, total_duration),
            "visual_action_score": 8,
            "audio_emotion_score": 8,
            "viral_coefficient": 8.0,
            "has_hardcoded_subs": False,
            "suggested_cta": default_cta,
            "target_platform": target_platform,
            "reason": "Стандартна обробка"
        }]

    # Format transcript for prompt
    transcript_lines = []
    for s in segments:
        if s["text"]:
            transcript_lines.append(f"[{s['start']:.1f}s - {s['end']:.1f}s]: {s['text']}")
    
    transcript_text = "\n".join(transcript_lines)
    if not transcript_text:
        transcript_text = f"[0.0s - {total_duration:.1f}s]: Геймплей без розпізнаної мови."

    # Retrieve user correction ledgers for both Highlights and Hooks
    clip_high_scores, clip_low_scores = get_high_and_low_highlight_scores_context()
    hook_high_scores, hook_low_scores = get_high_and_low_scores_context()

    prompt = f"""Ти — топ-продюсер вірусного контенту для {target_platform}.
Перед тобою повна стенограма відео тривалістю {total_duration:.1f} секунд ({total_duration/60:.1f} хв).

### 🎯 TARGET PLATFORM: {target_platform}

### 🔤 SUBTITLE DETECTION DIRECTIVE (OCR) - [APPLIES TO ALL PLATFORMS]:
Visually analyze the video frames of your selected clip. Check if there are ALREADY prominent, hardcoded subtitles or text transcribing the speech on the screen.
- If you see existing subtitles baked into the video, set `has_hardcoded_subs: true`.
- If the screen is mostly free of transcription text, set `has_hardcoded_subs: false`.

### 🧲 DYNAMIC CTA (BAIT) DIRECTIVE:
Generate a Russian Call to Action (CTA) tailored strictly to the specified TARGET PLATFORM.

IF TARGET PLATFORM IS "youtube_shorts":
- Goal: Maximize Subscriptions and Comments via two separate timed badges.
- NO karambit video is used for YouTube Shorts.
- Timing 1 (~20s / 33% of video): Subscribe bait (e.g. "Улыбнулся? С тебя подписка!").
- Timing 2 (~50s / 80% of video): Comment bait (e.g. "А что думаешь ты? Пиши в комменты!").
- DO NOT use emojis in CTA text so typography stays crisp without missing font glyphs.

IF TARGET PLATFORM IS "tiktok":
- Goal: Maximize Saves / Bookmarks (Сохраненки).
- Animation points directly to the TikTok bookmark icon with a skin video overlay.
- STRICT MANDATORY TEXT: ALWAYS set `suggested_cta` strictly to: "Закинь в сохраненки, чтобы получить этот скин!" (Do not use any other phrase for TikTok).

### 1. MULTIMODAL & WHISPER FIX (CRITICAL)
The provided text transcript was generated by Whisper AI. WARNING: Whisper often ignores or deletes raw screams, desk smashes, and hysterical laughter, mistaking them for background noise. DO NOT rely solely on the text to find the emotional peak! You MUST actively listen to the raw audio track of the video. If you hear a massive rage scream or laugh that is MISSING from the transcript, USE IT as the hook anyway.

### 2. SCORING & COEFFICIENT LOGIC
Evaluate moments using a `viral_coefficient` (Scale 1.0 - 10.0). Balance the score using the following weights:
- Visual Action Score (Weight: 0.6): High-impact on-screen events (flicks, clutches, visual bugs, sudden deaths).
- Audio Emotion Score (Weight: 0.4): Extreme audio peaks (rage, screams, toxic reactions, hysterical laughter).

### 3. THE 1-10 GRADING MATRIX FOR CLIPS & HOOKS
The user strictly grades your highlight and hook selection from 1 to 10. Calibrate your choices according to this exact feedback matrix:
10 🌟: Exact energy and explosion. Perfect sync of on-screen action, punchline, and off-screen rage.
9 🔥: Excellent choice. High emotional peaks, continuous engagement.
8 ✨: Good choice, emulate this style.
7 👍: Acceptable, but try to find even more explosive timing.
6 👌: Needs more emotional intensity or stronger climax.
5 ⚖️: Too plain. Ordinary dialogue without punchline.
4 ⚠️: Lacks viral energy. Avoid calm dialogue.
3 ❌: Weak/Failed. Incomplete sentence or cut mid-word.
2 🚫: Monotonous dialogue. Strictly avoid.
1 ⛔️: CRITICAL FAILURE. NEVER repeat this. If speech is calm, fallback immediately to loudest audio/visual energy.

### 4. DYNAMIC MEMORY MODULE (USER RATING LEDGER)
Below is the history of the user's ratings for HIGHLIGHTS and HOOKS. LEARN FROM THESE to avoid past mistakes and replicate successes:
{clip_high_scores}
{clip_low_scores}
{hook_high_scores}
{hook_low_scores}

ЗАВДАННЯ:
Знайди рівно {target_clips_count} НАЙКРАЩИХ, найбільш вірусних та завершених за змістом фрагментів (нарізок) для окремих {target_platform} відео!

ПРАВИЛА ДЛЯ КОЖНОЇ НАРІЗКИ:
1. Тривалість кожної нарізки (end_time - start_time) повинна бути в межах 30 – 65 секунд (ідеально 35-50 сек).
2. Нарізка має починатися з логічного початку фрази/моменту і закінчуватися розв'язкою/панчлайном (без обриву слів на півслові).
3. Нарізки НЕ повинні перетинатися між собою (між кліпами має бути хоча б 3-5 секунд різниці або вони з різних частин відео).
4. Обирай моменти з найвищою концентрацією емоцій: крики, сміх, клатчі, фейли, суперечки, кульмінації.
5. Для кожної нарізки також вкажи `hook_start` та `hook_end` (найяскравіший мікро-момент strictly 1.5 - 3.0s duration max всередині цієї нарізки, який можна поставити на початок кліпу).
6. Для кожної нарізки адаптуй `suggested_cta` під платформу {target_platform}.

СТЕНОГРАМА ВІДЕО:
{transcript_text}

ВІДПОВІДЬ НАДАЙ СТРОГО У ФОРМАТІ JSON ЗА СХЕМОЮ:
{{
  "highlights": [
    {{
      "index": <int>,
      "start_time": <float>,
      "end_time": <float>,
      "title": "<string>",
      "hook_start": <float, strictly 1.5-3.0s duration max>,
      "hook_end": <float>,
      "visual_action_score": <int 1-10>,
      "audio_emotion_score": <int 1-10>,
      "viral_coefficient": <float 1.0-10.0>,
      "has_hardcoded_subs": <boolean>,
      "suggested_cta": "<string, Russian CTA adapted to target platform>",
      "reason": "<string>"
    }}
  ]
}}
"""

    client = genai.Client(api_key=gemini_api_key)
    highlights = []
    models_to_try = [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-3.6-flash"
    ]
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    system_instruction=f"You are an elite viral video editor and content producer for {target_platform}, calibrated to the user's grading matrix and CTA rules. Always respond in valid JSON matching the requested schema.",
                    temperature=0.3
                )
            )
            data = json.loads(response.text)
            highlights = data.get("highlights", [])
            if highlights:
                logger.info(f"Successfully generated {len(highlights)} highlights using model '{model_name}'")
                break
        except Exception as e:
            logger.warning(f"Highlight cutter model {model_name} failed: {e}")
            continue

    if highlights:
        valid_highlights = []
        for i, h in enumerate(highlights, 1):
            s_t = float(h.get("start_time", h.get("start", 0.0)))
            e_t = float(h.get("end_time", h.get("end", s_t + 40.0)))
            s_t = max(0.0, s_t)
            e_t = min(total_duration, e_t)
            if e_t - s_t < 15.0:
                e_t = min(total_duration, s_t + 35.0)

            h_start = float(h.get("hook_start", s_t))
            h_end = float(h.get("hook_end", h_start + 2.5))
            if h_start < s_t or h_start >= e_t:
                h_start = s_t
                h_end = min(e_t, s_t + 2.5)

            # Ensure hook is strictly 1.5 - 3.0s duration
            if h_end - h_start > 3.0:
                h_end = h_start + 3.0
            elif h_end - h_start < 1.5:
                h_end = min(e_t, h_start + 2.5)

            vas = int(h.get("visual_action_score", 8))
            aes = int(h.get("audio_emotion_score", 8))
            vc = float(h.get("viral_coefficient", round(0.6 * vas + 0.4 * aes, 1)))
            has_subs = bool(h.get("has_hardcoded_subs", False))
            s_cta = str(h.get("suggested_cta", default_cta)).strip()

            valid_highlights.append({
                "index": int(h.get("index", i)),
                "start": round(s_t, 2),
                "end": round(e_t, 2),
                "start_time": round(s_t, 2),
                "end_time": round(e_t, 2),
                "duration": round(e_t - s_t, 2),
                "title": str(h.get("title", f"Момент #{i}")),
                "hook_start": round(h_start, 2),
                "hook_end": round(h_end, 2),
                "visual_action_score": vas,
                "audio_emotion_score": aes,
                "viral_coefficient": round(vc, 1),
                "has_hardcoded_subs": has_subs,
                "suggested_cta": s_cta,
                "target_platform": target_platform,
                "reason": str(h.get("reason", "ШІ обрав як вірусний момент"))
            })

        if valid_highlights:
            logger.info(f"Gemini successfully selected {len(valid_highlights)} highlights for {target_platform}.")
            return valid_highlights

    # Fallback partition
    fallback_clips = []
    clip_dur = min(50.0, max(30.0, total_duration / (target_clips_count + 1)))
    step = (total_duration - clip_dur) / target_clips_count if target_clips_count > 0 else 30.0
    for i in range(target_clips_count):
        st = round(i * step, 2)
        et = round(min(total_duration, st + clip_dur), 2)
        fallback_clips.append({
            "index": i + 1,
            "start": st,
            "end": et,
            "start_time": st,
            "end_time": et,
            "duration": round(et - st, 2),
            "title": f"Частина #{i + 1}",
            "hook_start": st,
            "hook_end": round(min(et, st + 2.5), 2),
            "visual_action_score": 7,
            "audio_emotion_score": 7,
            "viral_coefficient": 7.0,
            "reason": "Автоматичний розподіл хронометражу"
        })

    return fallback_clips
