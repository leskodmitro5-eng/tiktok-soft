import os
import sys
import re
import math
import random
import shutil
import tempfile
import asyncio
import subprocess
import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import imageio_ffmpeg

from ai_hook_finder import find_best_hook
from subtitle_generator import transcribe_words_with_whisper, generate_karaoke_ass

logger = logging.getLogger("VideoProcessor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
PIPELINE_FPS = 30

_CACHED_ENCODER_FLAGS = None


def get_ffmpeg_exe() -> str:
    """Returns absolute path to the bundled FFmpeg executable."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def detect_best_h264_encoder() -> list[str]:
    """
    Probes available hardware encoders (NVENC, QSV, AMF, MediaFoundation) on the current system,
    falling back to libx264 CPU encoding if no hardware accelerator is available.
    """
    global _CACHED_ENCODER_FLAGS
    if _CACHED_ENCODER_FLAGS is not None:
        return _CACHED_ENCODER_FLAGS

    ffmpeg_exe = get_ffmpeg_exe()
    candidates = [
        ("h264_nvenc (NVIDIA GPU)", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19"]),
        ("h264_qsv (Intel QuickSync)", ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "19"]),
        ("h264_amf (AMD GPU)", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "19", "-qp_p", "19"]),
        ("h264_mf (Windows MediaFoundation)", ["-c:v", "h264_mf", "-b:v", "6500k"]),
        ("libx264 (CPU Software)", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-threads", "2"])
    ]

    for name, flags in candidates:
        try:
            cmd = [ffmpeg_exe, "-y", "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1", *flags, "-f", "null", "-"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                logger.info(f"Hardware video acceleration active: using '{name}'")
                _CACHED_ENCODER_FLAGS = flags
                return _CACHED_ENCODER_FLAGS
        except Exception:
            pass

    _CACHED_ENCODER_FLAGS = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-threads", "2"]
    logger.info("Using default software CPU encoder libx264")
    return _CACHED_ENCODER_FLAGS


def run_ffmpeg_cmd(cmd: list[str]) -> None:
    """Executes an FFmpeg command synchronously and raises RuntimeError on non-zero exit."""
    logger.info("Running FFmpeg: %s ...", " ".join(cmd[:8]))
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        logger.error("FFmpeg error: %s", res.stderr[-1200:])
        raise RuntimeError(f"FFmpeg failed with code {res.returncode}:\n{res.stderr[-1000:]}")


async def run_ffmpeg_cmd_async(cmd: list[str]) -> None:
    """Asynchronously executes an FFmpeg command without blocking the main event loop."""
    logger.info("Async FFmpeg: %s ...", " ".join(cmd[:8]))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace")[-1200:]
        logger.error("Async FFmpeg error: %s", err_text)
        raise RuntimeError(f"FFmpeg failed with code {proc.returncode}:\n{err_text}")


def get_media_info(file_path: str) -> dict:
    """Extracts duration, width, height, fps, and audio status using FFmpeg."""
    ffmpeg_exe = get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-i", file_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = res.stderr

    duration = 0.0
    width = CANVAS_WIDTH
    height = CANVAS_HEIGHT
    fps = 30.0
    has_audio = False

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", output)
    if dur_match:
        h, m, s = dur_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    vid_match = re.search(r"Stream.*Video:.*,\s*(\d{2,5})x(\d{2,5})", output)
    if vid_match:
        width = int(vid_match.group(1))
        height = int(vid_match.group(2))

    fps_match = re.search(r"(\d+\.?\d*)\s*fps", output)
    if fps_match:
        fps = float(fps_match.group(1))

    if "Stream" in output and "Audio:" in output:
        has_audio = True

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": has_audio
    }


def calculate_insertion_timings(duration_sec: float) -> list[float]:
    """
    Calculates banner insertion timestamps:
    - < 60s: 1 banner strictly in center (T / 2)
    - 60s - 119s: 1 banner strictly in center (T / 2)
    - >= 120s: 1 banner per full 60 seconds at (i * 60 - 30) seconds (0:30, 1:30, 2:30...)
    """
    if duration_sec < 120.0:
        return [round(duration_sec / 2.0, 2)]

    n_minutes = int(duration_sec // 60)
    timings = []
    for i in range(1, n_minutes + 1):
        t = i * 60.0 - 30.0
        if t < duration_sec:
            timings.append(round(t, 2))
    return timings


def calculate_bait_timing(
    total_duration: float,
    hook_duration: float,
    banner_intervals: list[tuple[float, float]],
    bait_duration: float = 4.0
) -> float:
    """Calculates a safe active-phase window for the Engagement Bait overlay."""
    blocked_zones = [(0.0, hook_duration + 1.2)]
    for b_start, b_end in banner_intervals:
        blocked_zones.append((max(0.0, b_start - 1.5), min(total_duration, b_end + 1.5)))
    
    if total_duration > 8.0:
        blocked_zones.append((max(0.0, total_duration - 3.5), total_duration))

    candidate_ranges = [
        (0.25 * total_duration, 0.40 * total_duration),
        (0.65 * total_duration, 0.80 * total_duration),
        (0.40 * total_duration, 0.60 * total_duration),
        (hook_duration + 1.5, max(hook_duration + 2.0, total_duration - bait_duration - 2.0))
    ]

    for start_range, end_range in candidate_ranges:
        if end_range <= start_range:
            continue
        steps = 10
        step_size = (end_range - start_range) / steps if steps > 0 else 1.0
        for s in range(steps + 1):
            cand_start = round(start_range + s * step_size, 2)
            cand_end = round(cand_start + bait_duration, 2)
            
            if cand_end > (total_duration - 2.0):
                continue
            if cand_start < (hook_duration + 0.8):
                continue

            collision = False
            for b_z_start, b_z_end in blocked_zones:
                if not (cand_end <= b_z_start or cand_start >= b_z_end):
                    collision = True
                    break

            if not collision:
                logger.info(f"Calculated safe bait timing at {cand_start:.2f}s")
                return cand_start

    # Fallback scan
    curr = hook_duration + 1.0
    scan_end = max(curr, total_duration - bait_duration - 1.5)
    while curr <= scan_end:
        cand_start = round(curr, 2)
        cand_end = round(cand_start + bait_duration, 2)
        collision = any(not (cand_end <= b_z_start or cand_start >= b_z_end) for b_z_start, b_z_end in blocked_zones)
        if not collision:
            return cand_start
        curr += 0.5

    safe_t = max(hook_duration + 0.8, round(total_duration * 0.35, 2))
    return safe_t


def calculate_shorts_dual_bait_timings(
    total_duration: float,
    hook_duration: float,
    banner_intervals: list[tuple[float, float]],
    bait_duration: float = 3.5
) -> tuple[float | None, float | None]:
    """Calculates 2 non-overlapping, banner-safe timings for YouTube Shorts."""
    blocked_zones = []
    if hook_duration > 0:
        blocked_zones.append((0.0, round(hook_duration + 0.8, 2)))
    for b_s, b_e in banner_intervals:
        blocked_zones.append((round(max(0.0, b_s - 0.8), 2), round(b_e + 0.8, 2)))

    t1_ideal = round(total_duration * 0.33, 2)
    t1_min = round(hook_duration + 1.0, 2)
    t1_max = round(total_duration * 0.55, 2)
    
    t1_val = None
    for cand in [t1_ideal, t1_ideal - 2.0, t1_ideal + 2.0, t1_ideal - 4.0, t1_ideal + 4.0]:
        if t1_min <= cand <= t1_max:
            c_s = round(cand, 2)
            c_e = round(c_s + bait_duration, 2)
            if not any(not (c_e <= b_s or c_s >= b_e) for b_s, b_e in blocked_zones):
                t1_val = c_s
                break
    
    if t1_val is None:
        curr = t1_min
        while curr <= t1_max:
            c_s = round(curr, 2)
            c_e = round(c_s + bait_duration, 2)
            if not any(not (c_e <= b_s or c_s >= b_e) for b_s, b_e in blocked_zones):
                t1_val = c_s
                break
            curr += 0.5

    if t1_val is None:
        t1_val = round(max(t1_min, total_duration * 0.3), 2)

    t2_ideal = round(total_duration * 0.82, 2)
    t2_min = round(t1_val + bait_duration + 4.0, 2)
    t2_max = round(total_duration - bait_duration - 0.8, 2)

    t2_val = None
    if t2_min <= t2_max:
        for cand in [t2_ideal, t2_ideal - 2.0, t2_ideal + 2.0, t2_ideal - 4.0, t2_ideal + 4.0]:
            if t2_min <= cand <= t2_max:
                c_s = round(cand, 2)
                c_e = round(c_s + bait_duration, 2)
                if not any(not (c_e <= b_s or c_s >= b_e) for b_s, b_e in blocked_zones):
                    t2_val = c_s
                    break

        if t2_val is None:
            curr = t2_max
            while curr >= t2_min:
                c_s = round(curr, 2)
                c_e = round(c_s + bait_duration, 2)
                if not any(not (c_e <= b_s or c_s >= b_e) for b_s, b_e in blocked_zones):
                    t2_val = c_s
                    break
                curr -= 0.5

    return t1_val, t2_val


def clean_text_for_rendering(text: str) -> str:
    """Removes unsupported emoji unicode sequences."""
    clean = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27BF\uFE00-\uFE0F]', '', text)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def generate_bait_text_overlay_image(output_png_path: str, text: str = "Закинь в сохраненки, чтобы получить этот скин!", width: int = 1080, height: int = 1920, target_y: int = 1265) -> str:
    """Renders high-resolution Cyrillic text overlay as transparent RGBA PNG."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    display_text = clean_text_for_rendering(text)

    font_candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/seguiemj.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf"
    ]
    font_path = None
    for fc in font_candidates:
        if os.path.exists(fc):
            font_path = fc
            break

    font_size = 36
    font = ImageFont.load_default()
    if font_path:
        while font_size >= 22:
            try:
                f_test = ImageFont.truetype(font_path, font_size)
                bb = draw.textbbox((0, 0), display_text, font=f_test, stroke_width=3)
                if (bb[2] - bb[0]) <= (width - 120):
                    font = f_test
                    break
            except Exception:
                pass
            font_size -= 2

    bbox = draw.textbbox((0, 0), display_text, font=font, stroke_width=3)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    cx = width // 2
    cy = target_y
    pad_x = 24
    pad_y = 12

    rect = [cx - tw // 2 - pad_x, cy - th // 2 - pad_y, cx + tw // 2 + pad_x, cy + th // 2 + pad_y]
    draw.rounded_rectangle(rect, radius=18, fill=(15, 15, 20, 215), outline=(255, 230, 0, 240), width=2)
    draw.text(
        (cx - tw // 2, cy - th // 2),
        display_text,
        font=font,
        fill=(255, 240, 30, 255),
        stroke_width=3,
        stroke_fill=(0, 0, 0, 255)
    )

    img.save(output_png_path, "PNG")
    return output_png_path


def generate_feather_alpha_mask(output_mask_path: str, width: int = 380, height: int = 214, margin: int = 12, blur_radius: int = 12) -> str:
    """Generates a feathered alpha mask with Gaussian blur around edges."""
    mask = Image.new("L", (width, height), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle([margin, margin, width - margin, height - margin], radius=20, fill=255)
    mask_blurred = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    mask_blurred.save(output_mask_path, "PNG")
    return output_mask_path


async def slice_raw_segment_async(input_video_path: str, start_sec: float, end_sec: float, output_path: str) -> str:
    """Asynchronously extracts sub-clip [start_sec, end_sec] with frame-accurate hardware/software encoding."""
    ffmpeg_exe = get_ffmpeg_exe()
    enc_flags = detect_best_h264_encoder()
    cmd = [
        ffmpeg_exe, "-y",
        "-ss", str(round(start_sec, 2)),
        "-to", str(round(end_sec, 2)),
        "-i", input_video_path,
        *enc_flags, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        output_path
    ]
    await run_ffmpeg_cmd_async(cmd)
    return output_path


def slice_raw_segment(input_video_path: str, start_sec: float, end_sec: float, output_path: str) -> str:
    """Synchronous wrapper for slice_raw_segment_async."""
    return asyncio.run(slice_raw_segment_async(input_video_path, start_sec, end_sec, output_path))


async def process_video_pipeline_async(
    input_path: str,
    output_path: str,
    banner_path: str = None,
    bait_path: str = None,
    groq_api_key: str = None,
    gemini_api_key: str = None,
    include_banner: bool = True,
    include_hook: bool = True,
    include_bait: bool = True,
    include_subtitles: bool = True,
    target_platform: str = "tiktok",
    suggested_cta: str = "",
    job_id: str = "",
    subtitle_style: str = "mrbeast"
) -> dict:
    """
    High-performance Asynchronous Processing Pipeline:
    1. AI Hook selection via Whisper + Gemini.
    2. Conversion to 9:16 vertical canvas with hardware encoder acceleration.
    3. Multi-layer unique-ification (noise, zoom, color grading, pitch shift).
    4. Banner and dual/single platform-adapted bait overlays.
    5. Smart multi-line karaoke ASS subtitles.
    6. Guaranteed temporary directory cleanup in try...finally block.
    """
    ffmpeg_exe = get_ffmpeg_exe()
    enc_flags = detect_best_h264_encoder()

    with tempfile.TemporaryDirectory(prefix="tt_proc_") as temp_dir:
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")
        
        # Check banner availability safely
        if include_banner and (not banner_path or not os.path.exists(banner_path) or os.path.getsize(banner_path) < 100):
            logger.warning(f"Banner file not found or empty ({banner_path}), skipping banner overlay.")
            include_banner = False

        # Check bait availability safely
        if include_bait and (not bait_path or not os.path.exists(bait_path) or os.path.getsize(bait_path) < 100):
            logger.warning(f"Bait file not found or empty ({bait_path}), skipping bait overlay.")
            include_bait = False

        main_info = get_media_info(input_path)
        logger.info(f"Input video metadata: {main_info}")
        raw_duration = main_info["duration"] or 10.0

        # --- STEP 1: AI Hook Detection ---
        hook_info = {"start": 0.0, "end": 0.0, "quote": "", "reason": "Вимкнено", "method": "disabled", "target_platform": target_platform}
        if include_hook:
            if groq_api_key and gemini_api_key:
                try:
                    hook_info = find_best_hook(input_path, raw_duration, groq_api_key, gemini_api_key, job_id=job_id, target_platform=target_platform)
                    logger.info(f"AI Hook Selected: {hook_info}")
                except Exception as e:
                    logger.warning(f"Hook extraction failed: {e}. Using default start hook.")
                    hook_info = {"start": 0.0, "end": min(2.5, raw_duration), "quote": "Початок відео", "reason": "Авто-вибір", "method": "fallback", "target_platform": target_platform}
            else:
                hook_info = {"start": 0.0, "end": min(2.5, raw_duration), "quote": "Початок відео", "reason": "Авто-вибір", "method": "fallback", "target_platform": target_platform}

        # Unique-ification parameters
        main_speed = round(random.uniform(1.01, 1.04), 4)
        banner_speed = round(random.uniform(1.10, 1.40), 3) if include_banner else 1.0
        zoom_factor = round(random.uniform(1.005, 1.010), 4)
        bright_val = round(random.uniform(-0.03, 0.03), 4)
        contrast_val = round(random.uniform(0.96, 1.04), 4)
        saturation_val = round(random.uniform(0.95, 1.05), 4)
        hue_rad = round(random.uniform(-0.03, 0.03), 4)
        grain_noise = random.randint(3, 5)
        target_fps = random.choice([29.97, 30.0, 31.0, 59.94, 60.0])
        pitch_ratio = round(random.uniform(0.997, 1.003), 4)

        W = CANVAS_WIDTH   # 1080
        H = CANVAS_HEIGHT  # 1920

        # --- STEP 2: Convert Main Video to 9:16 Canvas ---
        speed_main_path = os.path.join(temp_dir, "speed_main.mp4")

        video_916_filter = (
            f"[0:v]setpts=PTS/{main_speed},fps={PIPELINE_FPS},split=2[v_bg][v_fg];"
            f"[v_bg]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=6:1,scale={W}:{H}[bg_blur];"
            f"[v_fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2[v]"
        )

        if main_info["has_audio"]:
            audio_filter = f"[0:a]atempo={main_speed},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a]"
            filter_str = f"{video_916_filter};{audio_filter}"
            map_args = ["-map", "[v]", "-map", "[a]"]
        else:
            filter_str = f"{video_916_filter};aevalsrc=0:d=1:s=44100:c=stereo[a]"
            map_args = ["-map", "[v]", "-map", "[a]"]

        await run_ffmpeg_cmd_async([
            ffmpeg_exe, "-y",
            "-i", input_path,
            "-filter_complex", filter_str,
            *map_args,
            *enc_flags, "-pix_fmt", "yuv420p", "-r", str(PIPELINE_FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            speed_main_path
        ])

        main_proc_info = get_media_info(speed_main_path)
        t_duration = main_proc_info["duration"] or (raw_duration / main_speed)

        # --- STEP 3: Process AI Hook Segment ---
        d_hook = 0.0
        hook_proc_path = os.path.join(temp_dir, "hook_proc.mp4")
        if include_hook:
            h_start = hook_info["start"]
            h_end = hook_info["end"]
            h_raw_dur = max(0.5, h_end - h_start)
            h_speed = round(random.uniform(1.02, 1.05), 4)
            d_hook = round(h_raw_dur / h_speed, 2)

            hook_v_filter = (
                f"[0:v]setpts=PTS/{h_speed},fps={PIPELINE_FPS},split=2[hv_bg][hv_fg];"
                f"[hv_bg]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=6:1,scale={W}:{H}[hbg_blur];"
                f"[hv_fg]scale={W}:{H}:force_original_aspect_ratio=decrease[hfg_scaled];"
                f"[hbg_blur][hfg_scaled]overlay=(W-w)/2:(H-h)/2[hv]"
            )
            if main_info["has_audio"]:
                hook_a_filter = f"[0:a]atempo={h_speed},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[ha]"
                hook_filter_str = f"{hook_v_filter};{hook_a_filter}"
                hook_map_args = ["-map", "[hv]", "-map", "[ha]"]
            else:
                hook_filter_str = f"{hook_v_filter};aevalsrc=0:d=1:s=44100:c=stereo[ha]"
                hook_map_args = ["-map", "[hv]", "-map", "[ha]"]

            await run_ffmpeg_cmd_async([
                ffmpeg_exe, "-y",
                "-ss", str(h_start),
                "-to", str(h_end),
                "-i", input_path,
                "-filter_complex", hook_filter_str,
                *hook_map_args,
                *enc_flags, "-pix_fmt", "yuv420p", "-r", str(PIPELINE_FPS),
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                hook_proc_path
            ])

        # --- STEP 4: Process Banner Video (if enabled) ---
        timings = []
        d_banner = 0.0
        banner_proc_path = os.path.join(temp_dir, "banner_proc.mp4")

        if include_banner:
            timings = calculate_insertion_timings(t_duration)
            banner_info = get_media_info(banner_path)
            raw_b_dur = banner_info["duration"] or 3.0
            d_banner = round(raw_b_dur / banner_speed, 2)

            banner_target_w = int(W * 0.90)
            if banner_target_w % 2 != 0:
                banner_target_w -= 1

            await run_ffmpeg_cmd_async([
                ffmpeg_exe, "-y",
                "-i", banner_path,
                "-filter_complex", (
                    f"[0:v]setpts=PTS/{banner_speed},scale={banner_target_w}:-2,fps={PIPELINE_FPS}[v];"
                    f"[0:a]atempo={banner_speed},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a]"
                ),
                "-map", "[v]",
                "-map", "[a]",
                *enc_flags, "-pix_fmt", "yuv420p", "-r", str(PIPELINE_FPS),
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                banner_proc_path
            ])

        # --- STEP 5: Assemble Timeline Segments ---
        segment_files = []
        banner_intervals = []

        if include_hook and os.path.exists(hook_proc_path):
            segment_files.append(hook_proc_path)

        if include_banner and len(timings) > 0:
            split_points = [0.0] + timings + [t_duration]
            for i in range(len(split_points) - 1):
                t_start = split_points[i]
                t_end = split_points[i + 1]

                game_seg_path = os.path.join(temp_dir, f"seg_game_{i}.mp4")
                await run_ffmpeg_cmd_async([
                    ffmpeg_exe, "-y",
                    "-ss", str(t_start),
                    "-to", str(t_end),
                    "-i", speed_main_path,
                    "-c", "copy",
                    game_seg_path
                ])
                segment_files.append(game_seg_path)

                if i < len(timings):
                    insert_t = timings[i]
                    frame_img_path = os.path.join(temp_dir, f"frame_{i}.png")
                    await run_ffmpeg_cmd_async([
                        ffmpeg_exe, "-y",
                        "-ss", str(insert_t),
                        "-i", speed_main_path,
                        "-frames:v", "1",
                        "-q:v", "2",
                        frame_img_path
                    ])

                    freeze_seg_path = os.path.join(temp_dir, f"seg_freeze_{i}.mp4")
                    await run_ffmpeg_cmd_async([
                        ffmpeg_exe, "-y",
                        "-loop", "1",
                        "-i", frame_img_path,
                        "-i", banner_proc_path,
                        "-filter_complex", (
                            f"[0:v]scale={W}:{H},fps={PIPELINE_FPS},setpts=PTS-STARTPTS[bg];"
                            f"[1:v]fps={PIPELINE_FPS},setpts=PTS-STARTPTS[fg];"
                            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v]"
                        ),
                        "-map", "[v]",
                        "-map", "1:a",
                        *enc_flags,
                        "-pix_fmt", "yuv420p",
                        "-r", str(PIPELINE_FPS),
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-ar", "44100",
                        "-ac", "2",
                        "-shortest",
                        freeze_seg_path
                    ])
                    segment_files.append(freeze_seg_path)
                    
                    b_start_time = d_hook + insert_t + (i * d_banner)
                    banner_intervals.append((round(b_start_time, 2), round(b_start_time + d_banner, 2)))
        else:
            segment_files.append(speed_main_path)

        # --- STEP 6: Concatenate All Segments ---
        concat_list_file = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for seg in segment_files:
                seg_esc = seg.replace("\\", "/")
                f.write(f"file '{seg_esc}'\n")

        merged_raw_path = os.path.join(temp_dir, "merged_raw.mp4")
        await run_ffmpeg_cmd_async([
            ffmpeg_exe, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_file,
            "-c", "copy",
            merged_raw_path
        ])

        merged_info = get_media_info(merged_raw_path)
        total_duration = merged_info["duration"] or (t_duration + d_hook + len(timings) * d_banner)

        # --- STEP 7: Engagement Bait Overlay ---
        bait_applied = False
        bait_timing_start = None
        bait_duration = 4.0
        cta_text = ""
        bait_info_dict = {"applied": False, "enabled": include_bait, "platform": target_platform}

        BAIT_W = 380
        BAIT_H = 214
        BAIT_X = 650
        BAIT_Y = 1345
        TEXT_Y = 1265

        if target_platform == "youtube_shorts" and include_bait:
            shorts_bait_dur = 3.5
            t_s1, t_s2 = calculate_shorts_dual_bait_timings(total_duration, d_hook, banner_intervals, bait_duration=shorts_bait_dur)
            
            if t_s1 is not None:
                bait_applied = True
                text_1 = "Улыбнулся? С тебя подписка!"
                text_2 = "А что думаешь ты? Пиши в комменты!"
                
                bait_text_1_path = os.path.join(temp_dir, "bait_text_shorts_1.png")
                generate_bait_text_overlay_image(bait_text_1_path, text=text_1, width=W, height=H, target_y=1280)

                has_t2 = (t_s2 is not None)
                bait_text_2_path = os.path.join(temp_dir, "bait_text_shorts_2.png")
                if has_t2:
                    generate_bait_text_overlay_image(bait_text_2_path, text=text_2, width=W, height=H, target_y=1280)

                cta_text = f"{text_1} (на {t_s1:.0f}с) + {text_2} (на {t_s2:.0f}с)" if has_t2 else f"{text_1} (на {t_s1:.0f}с)"
                bait_info_dict = {
                    "applied": True,
                    "timings": [t_s1, t_s2] if has_t2 else [t_s1],
                    "texts": [text_1, text_2] if has_t2 else [text_1],
                    "duration": shorts_bait_dur,
                    "text": cta_text,
                    "enabled": True,
                    "platform": "youtube_shorts"
                }

        elif target_platform == "tiktok" and include_bait and bait_path and os.path.exists(bait_path):
            bait_info = get_media_info(bait_path)
            bait_duration = min(4.0, max(2.5, bait_info["duration"] or 4.0))
            bait_timing_start = calculate_bait_timing(total_duration, d_hook, banner_intervals, bait_duration)
            if bait_timing_start is not None:
                bait_applied = True
                cta_text = "Закинь в сохраненки, чтобы получить этот скин!"
                bait_mask_path = os.path.join(temp_dir, "bait_feather_mask.png")
                bait_text_path = os.path.join(temp_dir, "bait_text_overlay.png")
                
                generate_feather_alpha_mask(bait_mask_path, width=BAIT_W, height=BAIT_H, margin=12, blur_radius=12)
                generate_bait_text_overlay_image(bait_text_path, text=cta_text, width=W, height=H, target_y=TEXT_Y)

                bait_info_dict = {
                    "applied": True,
                    "start": bait_timing_start,
                    "duration": bait_duration,
                    "text": cta_text,
                    "enabled": True,
                    "platform": "tiktok"
                }

        # --- STEP 8: Karaoke Subtitles Generation ---
        subtitles_applied = False
        ass_path = os.path.join(temp_dir, "karaoke_subs.ass")
        if include_subtitles and groq_api_key:
            if not hook_info.get("has_hardcoded_subs", False):
                try:
                    sub_audio = os.path.join(temp_dir, "sub_audio.mp3")
                    await run_ffmpeg_cmd_async([
                        ffmpeg_exe, "-y",
                        "-i", merged_raw_path,
                        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
                        sub_audio
                    ])
                    if os.path.exists(sub_audio):
                        sub_words = transcribe_words_with_whisper(sub_audio, groq_api_key)
                        if sub_words:
                            ok_ass = generate_karaoke_ass(
                                sub_words,
                                ass_path,
                                play_res_x=W,
                                play_res_y=H,
                                margin_v=540,
                                style_name=subtitle_style
                            )
                            if ok_ass and os.path.exists(ass_path):
                                subtitles_applied = True
                except Exception as sub_err:
                    logger.warning(f"Subtitle generation skipped due to: {sub_err}")

        ass_filter = ""
        if subtitles_applied and os.path.exists(ass_path):
            ass_escaped = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
            ass_filter = f",ass='{ass_escaped}'"

        # Unique-ification filters for base video
        base_video_filters = (
            f"crop=in_w/{zoom_factor}:in_h/{zoom_factor}:(in_w-in_w/{zoom_factor})/2:(in_h-in_h/{zoom_factor})/2,"
            f"scale={W}:{H},"
            f"eq=brightness={bright_val}:contrast={contrast_val}:saturation={saturation_val},"
            f"hue=h={hue_rad},"
            f"noise=c0s={grain_noise}:c1s={grain_noise}:c2s={grain_noise}:allf=t+u,"
            f"fps=fps={target_fps}"
        )

        final_inputs = ["-i", merged_raw_path]

        if bait_applied and target_platform == "youtube_shorts":
            fade_dur = 0.35
            t_s1 = bait_info_dict["timings"][0]
            t_e1 = round(t_s1 + shorts_bait_dur, 2)
            
            if len(bait_info_dict["timings"]) > 1:
                t_s2 = bait_info_dict["timings"][1]
                t_e2 = round(t_s2 + shorts_bait_dur, 2)
                final_inputs.extend([
                    "-loop", "1", "-t", str(round(shorts_bait_dur, 2)), "-i", bait_text_1_path,
                    "-loop", "1", "-t", str(round(shorts_bait_dur, 2)), "-i", bait_text_2_path
                ])
                shorts_v_filter = (
                    f"[1:v]fade=t=in:st=0:d={fade_dur}:alpha=1,fade=t=out:st={round(shorts_bait_dur - fade_dur, 2)}:d={fade_dur}:alpha=1,setpts=PTS-STARTPTS+{t_s1}/TB[txt1_fg];"
                    f"[2:v]fade=t=in:st=0:d={fade_dur}:alpha=1,fade=t=out:st={round(shorts_bait_dur - fade_dur, 2)}:d={fade_dur}:alpha=1,setpts=PTS-STARTPTS+{t_s2}/TB[txt2_fg];"
                    f"[0:v]{base_video_filters}[v_base];"
                    f"[v_base][txt1_fg]overlay=x=0:y=0:enable='between(t,{t_s1},{t_e1})'[v_mid];"
                    f"[v_mid][txt2_fg]overlay=x=0:y=0:enable='between(t,{t_s2},{t_e2})'{ass_filter}[v_out]"
                )
            else:
                final_inputs.extend([
                    "-loop", "1", "-t", str(round(shorts_bait_dur, 2)), "-i", bait_text_1_path
                ])
                shorts_v_filter = (
                    f"[1:v]fade=t=in:st=0:d={fade_dur}:alpha=1,fade=t=out:st={round(shorts_bait_dur - fade_dur, 2)}:d={fade_dur}:alpha=1,setpts=PTS-STARTPTS+{t_s1}/TB[txt1_fg];"
                    f"[0:v]{base_video_filters}[v_base];"
                    f"[v_base][txt1_fg]overlay=x=0:y=0:enable='between(t,{t_s1},{t_e1})'{ass_filter}[v_out]"
                )
            
            audio_filter_graph = (
                f"[0:a]asetrate=44100*{pitch_ratio},aresample=44100[a_pitched];"
                f"anoisesrc=d={total_duration + 5:.2f}:c=pink:a=0.0007[pnoise];"
                f"[a_pitched][pnoise]amix=inputs=2:duration=first:dropout_transition=0[a_out]"
            )
            filter_complex_final = f"{shorts_v_filter};{audio_filter_graph}"

        elif bait_applied and target_platform == "tiktok":
            final_inputs.extend([
                "-i", bait_path,
                "-loop", "1", "-t", str(round(bait_duration, 2)), "-i", bait_mask_path,
                "-loop", "1", "-t", str(round(bait_duration, 2)), "-i", bait_text_path
            ])
            t_s = bait_timing_start
            t_e = round(t_s + bait_duration, 2)
            fade_dur = 0.4
            
            bait_v_filter = (
                f"[1:v]scale={BAIT_W}:{BAIT_H}[bait_s];"
                f"[2:v]scale={BAIT_W}:{BAIT_H},format=gray[mask_s];"
                f"[bait_s][mask_s]alphamerge,"
                f"fade=t=in:st=0:d={fade_dur}:alpha=1,"
                f"fade=t=out:st={round(bait_duration - fade_dur, 2)}:d={fade_dur}:alpha=1,"
                f"setpts=PTS-STARTPTS+{t_s}/TB[bait_fg];"
                f"[3:v]fade=t=in:st=0:d={fade_dur}:alpha=1,"
                f"fade=t=out:st={round(bait_duration - fade_dur, 2)}:d={fade_dur}:alpha=1,"
                f"setpts=PTS-STARTPTS+{t_s}/TB[txt_fg];"
                f"[0:v]{base_video_filters}[v_base];"
                f"[v_base][bait_fg]overlay=x={BAIT_X}:y={BAIT_Y}:enable='between(t,{t_s},{t_e})'[v_mid];"
                f"[v_mid][txt_fg]overlay=x=0:y=0:enable='between(t,{t_s},{t_e})'{ass_filter}[v_out]"
            )

            bait_has_audio = get_media_info(bait_path).get("has_audio", False)
            if bait_has_audio:
                bait_delay_ms = int(t_s * 1000)
                audio_filter_graph = (
                    f"[0:a]asetrate=44100*{pitch_ratio},aresample=44100[a_pitched];"
                    f"[1:a]volume=0.7,adelay={bait_delay_ms}|{bait_delay_ms}[a_bait_del];"
                    f"anoisesrc=d={total_duration + 5:.2f}:c=pink:a=0.0007[pnoise];"
                    f"[a_pitched][a_bait_del][pnoise]amix=inputs=3:duration=first:dropout_transition=0[a_out]"
                )
            else:
                audio_filter_graph = (
                    f"[0:a]asetrate=44100*{pitch_ratio},aresample=44100[a_pitched];"
                    f"anoisesrc=d={total_duration + 5:.2f}:c=pink:a=0.0007[pnoise];"
                    f"[a_pitched][pnoise]amix=inputs=2:duration=first:dropout_transition=0[a_out]"
                )

            filter_complex_final = f"{bait_v_filter};{audio_filter_graph}"
        else:
            filter_complex_final = (
                f"[0:v]{base_video_filters}{ass_filter}[v_out];"
                f"[0:a]asetrate=44100*{pitch_ratio},aresample=44100[a_pitched];"
                f"anoisesrc=d={total_duration + 5:.2f}:c=pink:a=0.0007[pnoise];"
                f"[a_pitched][pnoise]amix=inputs=2:duration=first:dropout_transition=0[a_out]"
            )

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        await run_ffmpeg_cmd_async([
            ffmpeg_exe, "-y",
            *final_inputs,
            "-filter_complex", filter_complex_final,
            "-map", "[v_out]",
            "-map", "[a_out]",
            "-t", str(round(total_duration, 2)),
            *enc_flags,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map_metadata", "-1",
            "-movflags", "+faststart",
            output_path
        ])

        final_info = get_media_info(output_path)
        logger.info(f"Final video rendered -> {output_path} ({final_info['duration']:.2f}s, {final_info['width']}x{final_info['height']})")

        return {
            "main_speed": main_speed,
            "banner_speed": banner_speed if include_banner else 0.0,
            "zoom_percent": round((zoom_factor - 1.0) * 100, 1),
            "brightness_percent": round(bright_val * 100, 1),
            "contrast_percent": round((contrast_val - 1.0) * 100, 1),
            "saturation_percent": round((saturation_val - 1.0) * 100, 1),
            "target_fps": target_fps,
            "pitch_ratio": pitch_ratio,
            "timings": timings if include_banner else [],
            "final_duration": final_info["duration"] or total_duration,
            "banner_duration": d_banner if include_banner else 0.0,
            "resolution": f"{final_info['width']}x{final_info['height']}",
            "hook_info": hook_info,
            "include_hook": include_hook,
            "include_banner": include_banner,
            "include_bait": include_bait,
            "include_subtitles": include_subtitles,
            "subtitles_applied": subtitles_applied,
            "target_platform": target_platform,
            "has_hardcoded_subs": hook_info.get("has_hardcoded_subs", False),
            "suggested_cta": cta_text if bait_applied else (suggested_cta or hook_info.get("suggested_cta", "")),
            "bait_info": bait_info_dict,
            "mode": ("with_banner" if include_banner else "no_banner") + ("_with_hook" if include_hook else "_no_hook") + ("_with_bait" if include_bait else "_no_bait") + f"_{target_platform}"
        }


def process_video_pipeline(
    input_path: str,
    output_path: str,
    banner_path: str = None,
    bait_path: str = None,
    groq_api_key: str = None,
    gemini_api_key: str = None,
    include_banner: bool = True,
    include_hook: bool = True,
    include_bait: bool = True,
    include_subtitles: bool = True,
    target_platform: str = "tiktok",
    suggested_cta: str = "",
    job_id: str = ""
) -> dict:
    """Synchronous entry point that runs the async pipeline in an event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    process_video_pipeline_async(
                        input_path, output_path, banner_path, bait_path,
                        groq_api_key, gemini_api_key, include_banner,
                        include_hook, include_bait, include_subtitles,
                        target_platform, suggested_cta, job_id
                    )
                )
                return future.result()
        else:
            return loop.run_until_complete(
                process_video_pipeline_async(
                    input_path, output_path, banner_path, bait_path,
                    groq_api_key, gemini_api_key, include_banner,
                    include_hook, include_bait, include_subtitles,
                    target_platform, suggested_cta, job_id
                )
            )
    except RuntimeError:
        return asyncio.run(
            process_video_pipeline_async(
                input_path, output_path, banner_path, bait_path,
                groq_api_key, gemini_api_key, include_banner,
                include_hook, include_bait, include_subtitles,
                target_platform, suggested_cta, job_id
            )
        )
