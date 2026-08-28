import os
import subprocess
import logging
from pathlib import Path
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger("ThumbnailGen")

BASE_DIR = Path(__file__).resolve().parent
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# Fallback fonts available on Windows
FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/seguiemj.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf"
]


def _get_font(size: int):
    """Loads best available bold font or defaults to PIL default."""
    for font_path in FONT_CANDIDATES:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def extract_keyframe_from_video(video_path: str, out_img_path: str, timestamp_sec: float = 1.0) -> bool:
    """Extracts a sharp 1080x1920 frame from video using FFmpeg."""
    try:
        cmd = [
            FFMPEG_PATH, "-y",
            "-ss", str(max(0.1, timestamp_sec)),
            "-i", str(video_path),
            "-vframes", "1",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-q:v", "2",
            str(out_img_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return os.path.exists(out_img_path)
    except Exception as e:
        logger.error(f"Error extracting frame with ffmpeg: {e}")
        return False


def generate_viral_thumbnail(
    video_path: str,
    output_image_path: str,
    title: str = "ЭПИЧНЫЙ МОМЕНТ 🔥",
    timestamp_sec: float = 1.0,
    badge_text: str = "🔥 ШОК"
) -> str | None:
    """
    Generates a high-conversion 1080x1920 vertical thumbnail cover.
    Features:
    - Cinematic gradient darkeners
    - Styled neon badge box
    - Heavy stroked, high-contrast viral title with drop shadow
    """
    temp_raw = str(Path(output_image_path).with_suffix(".raw.jpg"))
    
    if not extract_keyframe_from_video(video_path, temp_raw, timestamp_sec):
        logger.warning("Failed to extract raw frame, creating clean gradient background...")
        img = Image.new("RGBA", (1080, 1920), (20, 20, 25, 255))
    else:
        try:
            img = Image.open(temp_raw).convert("RGBA")
            if img.size != (1080, 1920):
                img = img.resize((1080, 1920), Image.Resampling.LANCZOS)
        except Exception:
            img = Image.new("RGBA", (1080, 1920), (20, 20, 25, 255))
        finally:
            if os.path.exists(temp_raw):
                try:
                    os.remove(temp_raw)
                except Exception:
                    pass

    # 1. Overlay dark cinematic gradients (top & bottom)
    gradient_overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(gradient_overlay)
    
    # Top gradient
    for y in range(400):
        alpha = int(180 * (1 - (y / 400)))
        g_draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
        
    # Bottom gradient (strong contrast for title)
    for y in range(1200, 1920):
        alpha = int(220 * ((y - 1200) / 720))
        g_draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))

    img = Image.alpha_composite(img, gradient_overlay)
    draw = ImageDraw.Draw(img)

    # 2. Draw styled Badge Tag (e.g., "🔥 ШОК" or "😱 100K IQ")
    if badge_text:
        badge_font = _get_font(42)
        badge_w = 320
        badge_h = 70
        bx0 = 80
        by0 = 240
        
        # Outer glow / border
        draw.rounded_rectangle([bx0 - 3, by0 - 3, bx0 + badge_w + 3, by0 + badge_h + 3], radius=18, fill=(255, 60, 60, 255))
        # Inner badge
        draw.rounded_rectangle([bx0, by0, bx0 + badge_w, by0 + badge_h], radius=15, fill=(20, 20, 25, 240))
        
        # Center badge text
        draw.text((bx0 + 35, by0 + 10), badge_text, font=badge_font, fill=(255, 230, 0, 255))

    # 3. Format and Draw Viral Title Text (word-wrapped)
    clean_title = (title or "ЭПИЧНЫЙ МОМЕНТ").upper()
    title_font = _get_font(68)
    
    words = clean_title.split()
    lines = []
    curr_line = []
    
    for w in words:
        curr_line.append(w)
        test_line = " ".join(curr_line)
        bbox = draw.textbbox((0, 0), test_line, font=title_font)
        if (bbox[2] - bbox[0]) > 920 and len(curr_line) > 1:
            curr_line.pop()
            lines.append(" ".join(curr_line))
            curr_line = [w]
    if curr_line:
        lines.append(" ".join(curr_line))

    # Render up to 3 lines near bottom
    start_y = 1500 - (len(lines) * 85)
    
    for i, line_text in enumerate(lines[:3]):
        bbox = draw.textbbox((0, 0), line_text, font=title_font)
        line_w = bbox[2] - bbox[0]
        tx = (1080 - line_w) // 2
        ty = start_y + (i * 95)
        
        # Text color alternates: Yellow for line 0/2, White for line 1
        text_color = (255, 220, 0, 255) if (i % 2 == 0) else (255, 255, 255, 255)
        
        # Heavy black stroke + drop shadow
        draw.text((tx + 5, ty + 5), line_text, font=title_font, fill=(0, 0, 0, 230), stroke_width=8, stroke_fill=(0, 0, 0, 255))
        draw.text((tx, ty), line_text, font=title_font, fill=text_color, stroke_width=7, stroke_fill=(10, 10, 10, 255))

    # Convert back to RGB & save
    final_rgb = img.convert("RGB")
    final_rgb.save(output_image_path, "JPEG", quality=95)
    logger.info(f"Generated viral thumbnail at {output_image_path}")
    return output_image_path
