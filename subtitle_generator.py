import os
import re
import math
import logging
from pathlib import Path
from groq import Groq

logger = logging.getLogger("SubtitleGenerator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")


def format_ass_timestamp(seconds: float) -> str:
    """Formats seconds into ASS timestamp format: H:MM:SS.cs (e.g. 0:00:01.25)."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    cs = int(round((seconds - math.floor(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"


def transcribe_words_with_whisper(audio_path: str, groq_api_key: str) -> list[dict]:
    """Transcribes audio with Groq Whisper-large-v3 and extracts word-level timestamps."""
    client = Groq(api_key=groq_api_key)
    with open(audio_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f.read()),
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"]
        )

    words = []
    raw_words = getattr(transcription, "words", None) or []
    for w in raw_words:
        if isinstance(w, dict):
            w_start = float(w.get("start", 0.0))
            w_end = float(w.get("end", 0.0))
            w_text = str(w.get("word", "")).strip()
        else:
            w_start = float(getattr(w, "start", 0.0))
            w_end = float(getattr(w, "end", 0.0))
            w_text = str(getattr(w, "word", "")).strip()

        if w_text:
            words.append({
                "word": w_text.upper(),
                "start": max(0.0, round(w_start, 2)),
                "end": max(w_start + 0.1, round(w_end, 2))
            })

    logger.info(f"Whisper transcribed {len(words)} words with word-level timestamps.")
    return words


def chunk_words_into_phrases(words: list[dict], max_words: int = 3, max_gap: float = 0.45) -> list[dict]:
    """
    Groups words into short, punchy 2-4 word phrases for high-engagement dynamic subtitles.
    Ensures phrases do not exceed safety line limits.
    """
    if not words:
        return []

    phrases = []
    current_phrase = []
    current_char_count = 0

    for i, w in enumerate(words):
        w_len = len(w["word"])
        if not current_phrase:
            current_phrase.append(w)
            current_char_count = w_len
            continue

        prev_w = current_phrase[-1]
        gap = w["start"] - prev_w["end"]

        # Split phrase if gap is noticeable, max_words reached, chars too long, or punctuation
        should_split = (
            len(current_phrase) >= max_words or
            (current_char_count + w_len + 1) > 28 or
            gap > max_gap or
            prev_w["word"].endswith((".", "!", "?", "...", ":"))
        )

        if should_split:
            phrases.append({
                "start": current_phrase[0]["start"],
                "end": current_phrase[-1]["end"],
                "words": current_phrase
            })
            current_phrase = [w]
            current_char_count = w_len
        else:
            current_phrase.append(w)
            current_char_count += w_len + 1

    if current_phrase:
        phrases.append({
            "start": current_phrase[0]["start"],
            "end": current_phrase[-1]["end"],
            "words": current_phrase
        })

    return phrases


def format_phrase_with_smart_wrap(words_list: list[dict], max_single_line_chars: int = 18) -> str:
    """
    Formats a list of words with karaoke timing tags (\\kf), inserting hard line break (\\N)
    and font size adjustments for extra-long words to guarantee zero text clipping.
    """
    total_chars = sum(len(w["word"]) for w in words_list) + max(0, len(words_list) - 1)
    
    # Calculate best split point if multiple words and long phrase
    split_index = -1
    if len(words_list) >= 2 and total_chars > max_single_line_chars:
        # Find midpoint index to balance line lengths
        mid = len(words_list) // 2
        split_index = mid

    line_parts = []
    for idx, w in enumerate(words_list):
        dur_cs = max(5, int(round((w["end"] - w["start"]) * 100)))
        clean_word = re.sub(r'[{}\\]', '', w["word"])
        
        # Adaptive font scaling for single giant words
        if len(clean_word) >= 14:
            word_tag = f"{{\\fs38\\kf{dur_cs}}}{clean_word}{{\\fs50}}"
        elif len(clean_word) >= 11:
            word_tag = f"{{\\fs44\\kf{dur_cs}}}{clean_word}{{\\fs50}}"
        else:
            word_tag = f"{{\\kf{dur_cs}}}{clean_word}"

        # Insert line break if this is the chosen split point
        if idx == split_index and idx > 0:
            line_parts.append(f"\\N{word_tag}")
        else:
            line_parts.append(word_tag)

    return " ".join(line_parts)


def generate_karaoke_ass(
    words: list[dict],
    output_ass_path: str,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    margin_v: int = 540,
    font_name: str = "Arial Black",
    font_size: int = 50,
    primary_color: str = "&H00FFFFFF",
    highlight_color: str = "&H0000E5FF",
    style_name: str = "mrbeast"
) -> bool:
    """
    Generates an Advanced SubStation Alpha (.ass) subtitle file.
    Supports preset styles:
    - 'mrbeast': Gold / Yellow highlight, bold thick black outline.
    - 'hormozi': Clean white with electric green highlight.
    - 'neon': Cyberpunk Cyan text with Neon Magenta highlight.
    - 'fire': Flame gold with intense red highlight.
    """
    style_presets = {
        "mrbeast": {
            "primary": "&H00FFFFFF",
            "highlight": "&H0000E5FF",  # Gold / Yellow in ASS &HAABBGGRR
            "outline": "&H00000000",
            "outline_w": 6,
            "shadow_w": 3
        },
        "hormozi": {
            "primary": "&H00FFFFFF",
            "highlight": "&H0033FF55",  # Vibrant Green
            "outline": "&H00000000",
            "outline_w": 5,
            "shadow_w": 2
        },
        "neon": {
            "primary": "&H00FFF200",    # Cyan
            "highlight": "&H00FF00D4",  # Neon Pink/Magenta
            "outline": "&H002A1100",
            "outline_w": 5,
            "shadow_w": 5
        },
        "fire": {
            "primary": "&H0000E5FF",    # Yellow
            "highlight": "&H000033FF",  # Flame Red
            "outline": "&H00000044",
            "outline_w": 6,
            "shadow_w": 4
        }
    }

    preset = style_presets.get(style_name.lower(), style_presets["mrbeast"])
    p_color = preset["primary"] if not primary_color or primary_color == "&H00FFFFFF" else primary_color
    h_color = preset["highlight"] if not highlight_color or highlight_color == "&H0000E5FF" else highlight_color
    outline_col = preset["outline"]
    outline_w = preset["outline_w"]
    shadow_w = preset["shadow_w"]

    phrases = chunk_words_into_phrases(words, max_words=3, max_gap=0.45)
    if not phrases:
        logger.warning("No phrases generated for ASS subtitles.")
        return False

    ass_header = f"""[Script Info]
Title: TikTok Viral Karaoke Subtitles ({style_name})
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font_name},{font_size},{p_color},{h_color},{outline_col},&H80000000,-1,0,0,0,100,100,1,0,1,{outline_w},{shadow_w},2,30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for p in phrases:
        start_ts = format_ass_timestamp(p["start"])
        end_ts = format_ass_timestamp(p["end"] + 0.15)
        karaoke_text = format_phrase_with_smart_wrap(p["words"], max_single_line_chars=18)
        events.append(f"Dialogue: 0,{start_ts},{end_ts},Karaoke,,0,0,0,,{karaoke_text}")

    content = ass_header + "\n".join(events) + "\n"

    with open(output_ass_path, "w", encoding="utf-8-sig") as f:
        f.write(content)

    logger.info(f"Generated viral karaoke ASS subtitles ({len(events)} events) with smart wrapping -> {output_ass_path}")
    return True
