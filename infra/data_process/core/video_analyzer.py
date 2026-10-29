"""视频理解核心 — ffmpeg 分段采样 → 图片视觉模型逐段识别 → 时间轴聚合

让只支持图片的视觉模型（VISION_*）也能"看"视频：
  1. ffprobe 探测时长/分辨率
  2. 按 「每段时长 × 每段帧数」的采样率均匀抽帧（单次 ffmpeg 均匀 fps 布尔效率最高）
  3. 帧按时间归属分段；每段帧用 PIL 拼成带时间戳烙印的网格图
  4. 每段一次 ImageAnalyzer 调用（复用现有图片视觉后端，敏感不加新依赖）
  5. 段描述按时间轴聚合为一段可用于注入对话上下文的文本

参数（可在设置页调整，持久化到 ~/.cortex/settings.json）：
  VIDEO_ANALYSIS_ENABLED / VIDEO_SEGMENT_SECONDS / VIDEO_FRAMES_PER_SEGMENT /
  VIDEO_MAX_DURATION_SECONDS / VIDEO_MAX_SIZE_MB / VIDEO_MAX_TOTAL_FRAMES /
  VIDEO_FRAME_WIDTH / VIDEO_JPEG_QUALITY / VIDEO_DESC_MAX_CHARS / VIDEO_FFMPEG_PATH

测试友好：subprocess 与视觉调用全部经模块级函数引用（_run_ffmpeg / _run_ffprobe /
_analyze_image_bundle），单测可 monkeypatch；不依赖真实 ffmpeg 即可测逻辑。
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import setup_logger

logger = setup_logger("video_analyzer")


# ── ffmpeg/ffprobe 路径解析：显式配置 > shutil.which > 常见安装目录回退 ──
#
# 桌面应用从 Finder/双击启动时 PATH 通常极简，单靠 shutil.which 会把已装的 brew ffmpeg
# 误判成"未安装"，故必须带目录回退。
_COMMON_TOOL_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/opt/anaconda3/bin",
    "/opt/miniconda3/bin",
    "/snap/bin",
)
_EXTRA_TOOL_DIRS: List[str] = []   # 运行时可追加（测试/嵌入环境）


def bundled_tool_dirs() -> List[str]:
    """应用内自带 ffmpeg 的候选目录（打包态 _MEIPASS/ffmpeg；开发态项目 vendor/ffmpeg）。

    spec 把 ffmpeg 作为 datas 收集 → `${_MEIPASS}/ffmpeg/`（.app 内 = Contents/Resources/ffmpeg）。
    """
    dirs: List[str] = []
    base = (getattr(sys, "_MEIPASS", "") or "").strip()
    if base:
        dirs.append(os.path.join(base, "ffmpeg"))
    vendor = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vendor", "ffmpeg"
    )
    dirs.append(vendor)   # 不存在无害，isfile 会过滤
    return dirs


def find_ffmpeg_tool(tool: str) -> Optional[str]:
    """定位 ffmpeg/ffprobe 可执行文件：配置目录 → 应用内自带 → PATH → 常见目录。

    配置优先级：settings.VIDEO_FFMPEG_PATH > 环境变量 VIDEO_FFMPEG_PATH。
    配置值为 ffmpeg 所在目录（如 /opt/homebrew/bin）。
    """
    try:
        from config.settings import settings as _cfg
        custom_dirs: List[str] = []
        cfg = (getattr(_cfg, "VIDEO_FFMPEG_PATH", "") or "").strip()
        if cfg:
            custom_dirs.append(cfg)
    except Exception:
        custom_dirs = []
    env_dir = (os.environ.get("VIDEO_FFMPEG_PATH", "") or "").strip()
    if env_dir:
        custom_dirs.append(env_dir)
    all_dirs = custom_dirs + bundled_tool_dirs() + list(_EXTRA_TOOL_DIRS)
    for d in all_dirs:
        p = os.path.join(d, tool)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which(tool)
    if found:
        return found
    for d in _COMMON_TOOL_DIRS:
        p = os.path.join(d, tool)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def ffmpeg_available() -> bool:
    return bool(find_ffmpeg_tool("ffmpeg") and find_ffmpeg_tool("ffprobe"))


# ── 可 monkeypatch 的系统调用出口 ──

def _run_ffprobe(args: List[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _run_ffmpeg(args: List[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {proc.stderr.strip()[:200]}")


async def _analyze_image_bundle(image_bytes: bytes, prompt: str) -> Dict[str, Any]:
    """调现有图片视觉后端（段拼图）。独立函数便于单测替换。"""
    from infra.data_process.core.image_analyzer import ImageAnalyzer
    return await ImageAnalyzer().analyze(image_bytes, prompt)


# ── 数据结构 ──

@dataclass
class SamplePlan:
    """采样计划：由设置参数与视频元数据推得"""
    duration: float                 # 处理时长（≤ 设置上限）
    sample_fps: float               # 均匀采样率（帧/秒）= frames_per_segment / segment_seconds
    total_frames: int               # 实际抽帳总数
    segment_seconds: int            # 每段时长
    frames_per_segment: int         # 每段目标帧数
    segments: List[Tuple[float, float]] = field(default_factory=list)  # [(t0,t1)...]
    truncated: bool = False         # 原视频超出 max_duration 被截断


def _video_settings():
    from config.settings import settings
    return (
        max(1, int(getattr(settings, "VIDEO_SEGMENT_SECONDS", 30) or 30)),
        max(1, int(getattr(settings, "VIDEO_FRAMES_PER_SEGMENT", 4) or 4)),
        max(1, int(getattr(settings, "VIDEO_MAX_DURATION_SECONDS", 600) or 600)),
        max(1, int(getattr(settings, "VIDEO_MAX_TOTAL_FRAMES", 60) or 60)),
        max(64, int(getattr(settings, "VIDEO_FRAME_WIDTH", 768) or 768)),
        min(5, max(1, int(getattr(settings, "VIDEO_JPEG_QUALITY", 3) or 3))),
        max(200, int(getattr(settings, "VIDEO_DESC_MAX_CHARS", 4000) or 4000)),
    )


def compute_sampling_plan(duration: float) -> SamplePlan:
    """按设置参数推导采样计划（纯函数，单测核心）"""
    seg_s, fns, max_dur, max_frames, _w, _q, _d = _video_settings()
    duration = max(0.1, float(duration or 0.1))
    truncated = duration > max_dur
    duration = min(duration, max_dur)

    sample_fps = fns / seg_s                     # 均匀采样率（帧/秒）
    total = int(duration * sample_fps)
    total = max(1, min(total, max_frames))
    # 总帧数被上限压低时，等比下调采样率保持均匀分布
    effective_fps = total / duration
    if effective_fps > sample_fps:
        effective_fps = sample_fps

    # 时间轴分段：每 seg_s 一段（末段不足 seg_s 也算一段）
    segments = []
    t = 0.0
    while t < duration - 1e-6:
        segments.append((t, min(t + seg_s, duration)))
        t += seg_s
    return SamplePlan(
        duration=duration,
        sample_fps=effective_fps,
        total_frames=total,
        segment_seconds=seg_s,
        frames_per_segment=fns,
        segments=segments,
        truncated=truncated,
    )


def probe_video(path: str) -> Dict[str, Any]:
    """ffprobe 探测视频元数据：时长/宽高/fps"""
    ffprobe = find_ffmpeg_tool("ffprobe")
    if not ffprobe:
        raise RuntimeError("未找到 ffprobe（需安装 ffmpeg 并加入 PATH）")
    raw = _run_ffprobe([
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ])
    meta = json.loads(raw)
    fmt = meta.get("format") or {}
    duration = float(fmt.get("duration") or 0.0)
    width = height = 0
    fps = 0.0
    for st in meta.get("streams") or []:
        if st.get("codec_type") == "video":
            width = int(st.get("width") or 0)
            height = int(st.get("height") or 0)
            rate = str(st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/0")
            try:
                num, den = rate.split("/")
                if float(den) > 0:
                    fps = float(num) / float(den)
            except Exception:
                pass
            break
    if duration <= 0:
        raise RuntimeError("无法解析视频时长（文件可能损坏或不是有效视频）")
    return {"duration": duration, "width": width, "height": height, "fps": fps}


def extract_frames(path: str, plan: SamplePlan, out_dir: str) -> List[Tuple[float, str]]:
    """按计划均匀抽帧 → [(时间戳, jpg路径)]（时间戳 = 帧序号/采样率）"""
    ffmpeg = find_ffmpeg_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg（需安装并加入 PATH）")
    _seg, _fns, _md, total, width, quality, _d = _video_settings()
    pattern = os.path.join(out_dir, "frame_%04d.jpg")
    _run_ffmpeg([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", path,
        "-vf", f"fps={plan.sample_fps:.6f},scale={width}:-2",
        "-q:v", str(quality),
        "-frames:v", str(plan.total_frames),
        "-start_number", "0",
        pattern,
    ])
    frames: List[Tuple[float, str]] = []
    for i in range(plan.total_frames):
        p = os.path.join(out_dir, f"frame_{i:04d}.jpg")
        if os.path.exists(p):
            frames.append((i / plan.sample_fps, p))
    return frames


def compose_segment_grid(frames: List[Tuple[float, str]], max_cols: int = 2) -> Optional[bytes]:
    """把一段的帧拼成带时间戳烙印的网格图 → JPEG bytes

    时间戳烙印（如 00:01:05.2）让视觉模型天然感知时间轴，无需额外元数据说明。
    """
    if not frames:
        return None
    from PIL import Image, ImageDraw

    def _fmt_ts(t: float) -> str:
        m, s = divmod(t, 60)
        h, m = divmod(int(m), 60)
        return f"{h:02d}:{m:02d}:{s:04.1f}"

    imgs = [(t, Image.open(p).convert("RGB")) for t, p in frames]
    cell_w = min(im.width for _, im in imgs)
    scaled = []
    for t, im in imgs:
        h = max(1, round(im.height * cell_w / im.width))
        scaled.append((t, im.resize((cell_w, h))))
    rows = (len(scaled) + max_cols - 1) // max_cols
    row_h = [max(im.height for _, im in scaled[r * max_cols:(r + 1) * max_cols]) for r in range(rows)]

    pad = 4
    label_h = 16
    W = max_cols * cell_w + (max_cols + 1) * pad
    H = sum(row_h) + rows * (label_h + pad) + pad
    canvas = Image.new("RGB", (W, H), (16, 16, 16))
    draw = ImageDraw.Draw(canvas)
    idx = 0
    for r in range(rows):
        y = pad + r * (label_h + pad + row_h[r])
        for c in range(max_cols):
            if idx >= len(scaled):
                break
            t, im = scaled[idx]
            x = pad + c * (cell_w + pad)
            canvas.paste(im, (x, y + label_h))
            draw.text((x + 2, y + 2), f"t={_fmt_ts(t)}", fill=(255, 210, 0))
            idx += 1

    import io
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _group_frames_by_segment(
    plan: SamplePlan, frames: List[Tuple[float, str]]
) -> List[Tuple[float, float, List[Tuple[float, str]]]]:
    """帧按时间归属分段（帧时间戳落在 [t0,t1) 即归该段）"""
    grouped: List[Tuple[float, float, List[Tuple[float, str]]]] = []
    for t0, t1 in plan.segments:
        seg_frames = [f for f in frames if t0 - 1e-6 <= f[0] < t1 - 1e-6]
        if seg_frames:
            grouped.append((t0, t1, seg_frames))
    return grouped


def _fmt_ts(t: float) -> str:
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:04.1f}"


class VideoAnalyzer:
    """视频分析器：ffmpeg 采样 → 图片视觉模型 → 时间轴聚合"""

    async def analyze_video_bytes(
        self, video_bytes: bytes, name: str = "", vision_prompt: str = ""
    ) -> Dict[str, Any]:
        """视频字节 → 结构化识别结果

        Returns:
            {ok, description, duration, frame_count, segment_count, segments:[{start,end,description}], truncated}
            失败时 {ok: False, error}
        """
        from config.settings import settings
        if not getattr(settings, "VIDEO_ANALYSIS_ENABLED", True):
            return {"ok": False, "error": "视频识别未启用（设置页可开启）"}

        max_mb = max(1, int(getattr(settings, "VIDEO_MAX_SIZE_MB", 50) or 50))
        if len(video_bytes) > max_mb * 1024 * 1024:
            return {"ok": False, "error": f"视频过大（>{max_mb}MB），不支持识别"}

        if not ffmpeg_available():
            return {"ok": False, "error": "需要 ffmpeg/ffprobe（请安装并加入 PATH）"}

        suffix = Path(name or "video.mp4").suffix or ".mp4"
        tmp_root = None
        try:
            tmp_root = tempfile.mkdtemp(prefix="cortex_video_")
            video_path = os.path.join(tmp_root, "input" + suffix)
            with open(video_path, "wb") as f:
                f.write(video_bytes)

            meta = await _run_probe(video_path)
            plan = subprocess_free_plan(meta["duration"])
            frames_dir = os.path.join(tmp_root, "frames")
            os.makedirs(frames_dir, exist_ok=True)
            frames = await _run_extract(video_path, plan, frames_dir)
            if not frames:
                return {"ok": False, "error": "未抽取到任何帧（视频可能损坏）"}

            segs = _group_frames_by_segment(plan, frames)
            seg_text_parts: List[str] = []
            segments_out = []
            for i, (t0, t1, seg_frames) in enumerate(segs):
                grid = compose_segment_grid(seg_frames)
                if grid is None:
                    continue
                prompt = vision_prompt or (
                    f"这是视频（时长 {plan.duration:.1f} 秒）第 {i + 1}/{len(segs)} 段 "
                    f"（时间 {_fmt_ts(t0)} - {_fmt_ts(t1)}）内均匀采样的帧拼图，"
                    "每格左上角有该帧的时间戳标签。"
                    "请描述：画面主要对象/人物/文字（OCR）、场景，以及各帧之间的动作与变化节奏。简洁。"
                )
                res = await _analyze_image_bundle(grid, prompt)
                desc = str(res.get("description") or res.get("error") or "").strip()
                if desc:
                    seg_text_parts.append(f"[{_fmt_ts(t0)}-{_fmt_ts(t1)}] {desc}")
                    segments_out.append({
                        "start": round(t0, 2), "end": round(t1, 2),
                        "description": desc[:800],
                    })

            _d_max = max(200, int(getattr(settings, "VIDEO_DESC_MAX_CHARS", 4000) or 4000))
            description = "\n".join(seg_text_parts)[:_d_max]
            return {
                "ok": True,
                "description": description,
                "duration": round(plan.duration, 2),
                "frame_count": len(frames),
                "segment_count": len(seg_text_parts),
                "segments": segments_out,
                "truncated": plan.truncated,
            }
        except Exception as e:
            logger.warning(f"[VideoAnalyzer] 识别失败: {type(e).__name__}: {e}")
            return {"ok": False, "error": f"视频识别失败: {e}"}
        finally:
            if tmp_root and os.path.isdir(tmp_root):
                shutil.rmtree(tmp_root, ignore_errors=True)


# ── 线程外包装：Blocking 调用丢线程池，避免卡 event loop ──

async def _run_probe(video_path: str) -> Dict[str, Any]:
    import asyncio
    return await asyncio.to_thread(probe_video, video_path)


def subprocess_free_plan(duration: float) -> SamplePlan:
    """计划推导 — 名字保留纯函数语义（不触碰 subprocess）"""
    return compute_sampling_plan(duration)


async def _run_extract(video_path: str, plan: SamplePlan, out_dir: str) -> List[Tuple[float, str]]:
    import asyncio
    return await asyncio.to_thread(extract_frames, video_path, plan, out_dir)


# ── 单例入口 ──

_analyzer: Optional[VideoAnalyzer] = None


def get_video_analyzer() -> VideoAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = VideoAnalyzer()
    return _analyzer
