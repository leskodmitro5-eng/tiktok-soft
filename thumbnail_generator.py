import os
import subprocess
import logging
from pathlib import Path
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageStat

logger = logging.getLogger("ThumbnailGen")

BASE_DIR = Path(__file__).resolve().parent
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# Font candidates for Windows, Linux (Docker/Render/Debian), and macOS
FONT_CANDIDATES = [
    # Linux / Docker container fonts
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
    # Windows standard fonts
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/seguiemj.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    # macOS standard fonts
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf"
]


def _get_font(size: int):
    """Loads best available bold font across Windows, Linux/Docker, and macOS."""
    for font_path in FONT_CANDIDATES:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    for font_name in ["LiberationSans-Bold", "DejaVuSans-Bold", "Arial-Bold", "arialbd", "impact", "arial"]:
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def extract_best_keyframe_from_video(video_path: str, out_img_path: str, timestamp_sec: float = 1.0) -> bool:
    """
    Extracts a high-quality vertical 1080x1920 frame from video.
    Tests candidate timestamps to avoid black/empty transition frames.
    """
    candidates = [
        max(0.2, timestamp_sec),
        max(0.5, timestamp_sec + 1.2),
        max(0.8, timestamp_sec + 2.5),
        2.0
    ]

    best_frame = None
    best_score = -1.0

    for ts in candidates:
        temp_candidate = str(Path(out_img_path).with_suffix(f".cand_{ts:.1f}.jpg"))
        try:
            cmd = [
                FFMPEG_PATH, "-y",
                "-ss", f"{ts:.2f}",
                "-i", str(video_path),
                "-vframes", "1",
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                "-q:v", "2",
                temp_candidate
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and os.path.exists(temp_candidate):
                try:
                    with Image.open(temp_candidate) as img:
                        stat = ImageStat.Stat(img.convert("L"))
                        brightness = stat.mean[0]
                        variance = stat.var[0]
                        # Score combines brightness and contrast variance to skip solid black frames
                        score = brightness * 0.4 + (variance ** 0.5) * 0.6
                        if score > best_score and brightness > 15:
                            best_score = score
                            if best_frame and os.path.exists(best_frame):
                                os.remove(best_frame)
                            best_frame = temp_candidate
                        else:
                            os.remove(temp_candidate)
                except Exception:
                    if os.path.exists(temp_candidate):
                        os.remove(temp_candidate)
        except Exception as e:
            logger.debug(f"Candidate frame extraction skipped at {ts}s: {e}")

    if best_frame and os.path.exists(best_frame):
        try:
            if os.path.exists(out_img_path):
                os.remove(out_img_path)
            os.rename(best_frame, out_img_path)
            return True
        except Exception:
            pass

    # Fallback to single frame if candidate check failed
    try:
        cmd = [
            FFMPEG_PATH, "-y",
            "-ss", str(max(0.2, timestamp_sec)),
            "-i", str(video_path),
            "-vframes", "1",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-q:v", "2",
            str(out_img_path)
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return os.path.exists(out_img_path)
    except Exception as e:
        logger.error(f"Error extracting keyframe: {e}")
        return False


def enhance_frame_for_viral_impact(img: Image.Image) -> Image.Image:
    """Enhances image color saturation, contrast, and sharpness for maximum click-through rate."""
    rgb = img.convert("RGB")
    try:
        # Boost color vibrancy (Saturation)
        rgb = ImageEnhance.Color(rgb).enhance(1.30)
        # Boost contrast
        rgb = ImageEnhance.Contrast(rgb).enhance(1.20)
        # Boost sharpness
        rgb = ImageEnhance.Sharpness(rgb).enhance(1.25)
    except Exception as e:
        logger.warning(f"Image enhancement error: {e}")
    return rgb.convert("RGBA")


def draw_clickbait_badge(draw: ImageDraw.ImageDraw, badge_text: str, badge_font: ImageFont.ImageFont, y_pos: int = 220):
    """Renders a glowing, high-contrast neon badge at the top of the cover."""
    clean_badge = badge_text.strip()
    if not clean_badge:
        clean_badge = "🔥 ШОК-КОНТЕНТ"

    bbox = draw.textbbox((0, 0), clean_badge, font=badge_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    padding_x = 36
    padding_y = 16
    badge_w = text_w + padding_x * 2
    badge_h = text_h + padding_y * 2

    bx0 = (1080 - badge_w) // 2
    by0 = y_pos
    bx1 = bx0 + badge_w
    by1 = by0 + badge_h

    # 1. Outer Neon Glow Layer
    draw.rounded_rectangle(
        [bx0 - 6, by0 - 6, bx1 + 6, by1 + 6],
        radius=26,
        fill=(255, 42, 109, 140)
    )
    # 2. Glowing Golden / Red Border
    draw.rounded_rectangle(
        [bx0 - 3, by0 - 3, bx1 + 3, by1 + 3],
        radius=24,
        fill=(255, 215, 0, 255)
    )
    # 3. Dark Semi-transparent Glass Pill
    draw.rounded_rectangle(
        [bx0, by0, bx1, by1],
        radius=22,
        fill=(15, 17, 26, 245)
    )

    # 4. Centered Badge Text with soft shadow
    tx = bx0 + padding_x
    ty = by0 + padding_y - 2
    draw.text((tx + 2, ty + 2), clean_badge, font=badge_font, fill=(0, 0, 0, 220))
    draw.text((tx, ty), clean_badge, font=badge_font, fill=(255, 235, 59, 255))


def generate_viral_thumbnail(
    video_path: str,
    output_image_path: str,
    title: str = "ЭПИЧНЫЙ МОМЕНТ 🔥",
    timestamp_sec: float = 1.0,
    badge_text: str = "🔥 ШОК-КОНТЕНТ"
) -> str | None:
    """
    Generates an ultra-clickable, high-conversion 1080x1920 vertical thumbnail cover (TikTok / Shorts).
    Features:
    - Smart keyframe selection (avoids black/blurry frames)
    - High-saturation & high-contrast color grading
    - Cinematic gradient darkening masks for 100% legibility
    - Top glowing clickbait neon badge
    - MrBeast / Top-YouTuber 3-layer bold typography with thick black strokes & dynamic color accents
    """
    temp_raw = str(Path(output_image_path).with_suffix(".raw_keyframe.jpg"))

    if not extract_best_keyframe_from_video(video_path, temp_raw, timestamp_sec):
        logger.warning("Failed to extract raw frame, creating dark cyber background...")
        img = Image.new("RGBA", (1080, 1920), (16, 18, 27, 255))
    else:
        try:
            raw_img = Image.open(temp_raw)
            if raw_img.size != (1080, 1920):
                raw_img = raw_img.resize((1080, 1920), Image.Resampling.LANCZOS)
            img = enhance_frame_for_viral_impact(raw_img)
        except Exception as e:
            logger.warning(f"Error loading keyframe image: {e}")
            img = Image.new("RGBA", (1080, 1920), (16, 18, 27, 255))
        finally:
            if os.path.exists(temp_raw):
                try:
                    os.remove(temp_raw)
                except Exception:
                    pass

    # 1. Overlay Cinematic Gradients (Top & Bottom Masks)
    gradient_overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(gradient_overlay)

    # Top Darkening Gradient (protects Badge area)
    for y in range(480):
        alpha = int(210 * ((1 - (y / 480)) ** 1.3))
        g_draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))

    # Bottom Darkening Gradient (protects Viral Title area)
    for y in range(1100, 1920):
        progress = (y - 1100) / 820
        alpha = int(245 * (progress ** 1.2))
        g_draw.line([(0, y), (1080, y)], fill=(5, 5, 10, alpha))

    img = Image.alpha_composite(img, gradient_overlay)
    draw = ImageDraw.Draw(img)

    # 2. Draw Top Clickbait Badge
    badge_font = _get_font(46)
    draw_clickbait_badge(draw, badge_text or "🔥 ШОК-КОНТЕНТ", badge_font, y_pos=220)

    # 3. Format and Draw Multi-line Clickbait Title
    clean_title = (title or "ЭПИЧНЫЙ МОМЕНТ В ИГРЕ 🔥").upper().strip()
    title_font = _get_font(74)

    words = clean_title.split()
    lines = []
    curr_line = []

    for w in words:
        curr_line.append(w)
        test_line = " ".join(curr_line)
        bbox = draw.textbbox((0, 0), test_line, font=title_font)
        # Limit max line width to 920px
        if (bbox[2] - bbox[0]) > 920 and len(curr_line) > 1:
            curr_line.pop()
            lines.append(" ".join(curr_line))
            curr_line = [w]
    if curr_line:
        lines.append(" ".join(curr_line))

    # Clamp to at most 3 punchy lines
    render_lines = lines[:3]
    line_height = 102
    start_y = 1520 - (len(render_lines) * line_height)

    # 4. Render 3-Layer Clickbait Text (Glow -> Heavy Black Outline -> Vivid Color Fill)
    for i, line_text in enumerate(render_lines):
        bbox = draw.textbbox((0, 0), line_text, font=title_font)
        line_w = bbox[2] - bbox[0]
        tx = (1080 - line_w) // 2
        ty = start_y + (i * line_height)

        # Alternating high-CTR color palette:
        # Line 0: Cyber Yellow (#FFE600)
        # Line 1: Pure White (#FFFFFF)
        # Line 2: Neon Red/Coral (#FF3366)
        if i == 0:
            fill_color = (255, 230, 0, 255)
            glow_color = (255, 180, 0, 160)
        elif i == 1:
            fill_color = (255, 255, 255, 255)
            glow_color = (0, 210, 255, 160)
        else:
            fill_color = (255, 51, 102, 255)
            glow_color = (255, 0, 80, 160)

        # Layer A: Soft Colored Neon Glow / Drop Shadow behind text
        for ox, oy in [(-3, -3), (3, -3), (-3, 3), (3, 3), (0, 6)]:
            draw.text(
                (tx + ox, ty + oy),
                line_text,
                font=title_font,
                fill=glow_color,
                stroke_width=14,
                stroke_fill=(0, 0, 0, 255)
            )

        # Layer B: Heavy Jet-Black Outline (11px)
        draw.text(
            (tx, ty),
            line_text,
            font=title_font,
            fill=(0, 0, 0, 255),
            stroke_width=11,
            stroke_fill=(0, 0, 0, 255)
        )

        # Layer C: Crisp Color Fill
        draw.text(
            (tx, ty),
            line_text,
            font=title_font,
            fill=fill_color,
            stroke_width=2,
            stroke_fill=(20, 20, 20, 255)
        )

    # 5. Convert to RGB & Save High-Quality JPEG
    final_rgb = img.convert("RGB")
    final_rgb.save(output_image_path, "JPEG", quality=95, optimize=True)
    logger.info(f"Successfully generated viral clickbait thumbnail at {output_image_path}")
    return output_image_path
