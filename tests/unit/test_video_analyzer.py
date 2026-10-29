# -*- coding: utf-8 -*-
"""视频识别模块单测 — 不依赖真实 ffmpeg（系统调用/视觉调用全部 monkeypatch）

覆盖：
- compute_sampling_plan 数学（采样率/总帧上限/截断标记/分段余量）
- _group_frames_by_segment 时间归属
- compose_segment_grid 时间戳烙印拼图（真实 PIL，无 ffmpeg）
- analyze_video_bytes 全流程（mock 探测/抽帧/视觉）
- attachment_handler 三模式（describe/direct/disabled）视频路由
- VIDEO_* 参数可经 PUT /config/{key} 修改（_MODIFIABLE_FIELDS 包含）
"""
import asyncio
import base64
import json
import os

import pytest

from infra.data_process.core.video_analyzer import (
    _group_frames_by_segment,
    compose_segment_grid,
    compute_sampling_plan,
    get_video_analyzer,
)


def _mk_jpg(w=64, h=48) -> bytes:
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 200)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def sample_settings(monkeypatch):
    monkeypatch.setattr(settings, "VIDEO_SEGMENT_SECONDS", 30, raising=True)
    monkeypatch.setattr(settings, "VIDEO_FRAMES_PER_SEGMENT", 4, raising=True)
    monkeypatch.setattr(settings, "VIDEO_MAX_DURATION_SECONDS", 600, raising=True)
    monkeypatch.setattr(settings, "VIDEO_MAX_TOTAL_FRAMES", 60, raising=True)


def _run(coro):
    return asyncio.run(coro)


# 导入风格注意：config/__init__.py 把包属性 config.settings 遮蔽为 Settings 实例，
# `import config.settings as m` 拿到的是实例而非模块 —— 必须用 from-import 取模块级单例。
from config.settings import settings  # noqa: E402


# ── 采样计划（纯函数） ──

class TestSamplingPlan:
    def test_short_video_uses_full_rate(self, sample_settings):
        plan = compute_sampling_plan(120.0)
        assert plan.sample_fps == pytest.approx(4 / 30)
        assert plan.total_frames == 16          # 120s * (4/30)
        assert plan.truncated is False
        assert len(plan.segments) == 4          # 30s x4

    def test_segment_remainder_becomes_last_segment(self, sample_settings):
        plan = compute_sampling_plan(65.0)
        assert len(plan.segments) == 3          # 2x30 + 5s 末段
        assert plan.segments[-1] == (60.0, 65.0)

    def test_long_video_truncated_and_flagged(self, sample_settings):
        plan = compute_sampling_plan(1800.0)
        assert plan.duration == 600.0
        assert plan.truncated is True
        assert plan.total_frames == 60          # 80 帧 → 上限 60

    def test_total_frames_cap_keeps_uniform(self, sample_settings):
        plan = compute_sampling_plan(600.0)
        assert plan.total_frames == 60
        assert plan.sample_fps == pytest.approx(60 / 600)

    def test_zero_duration_clamped(self, sample_settings):
        plan = compute_sampling_plan(0.0)
        assert plan.total_frames == 1


# ── 分段归属 ──

class TestGroupBySegment:
    def test_frames_classified_by_time_bucket(self, sample_settings):
        plan = compute_sampling_plan(65.0)
        # fps=4/30 → 帧间期 7.5s：t = 0, 7.5, ... 60 → 三段各有归属
        frames = [(i * 7.5, f"/tmp/f{i}.jpg") for i in range(9)]
        groups = _group_frames_by_segment(plan, frames)
        assert [g[0] for g in groups] == [0.0, 30.0, 60.0]
        assert groups[0][2] == frames[0:4]
        assert groups[1][2] == frames[4:8]
        assert groups[2][2] == frames[8:]


# ── 段拼图（真实 PIL） ──

class TestComposeGrid:
    def test_grid_is_jpeg_with_size(self, tmp_path, sample_settings):
        frames = []
        for i in range(4):
            p = tmp_path / f"f{i}.jpg"
            p.write_bytes(_mk_jpg())
            frames.append((i * 7.5, str(p)))
        grid = compose_segment_grid(frames)
        assert grid[:2] == b"\xff\xd8"          # JPEG magic
        assert len(grid) > 1000

    def test_empty_frames_none(self, sample_settings):
        assert compose_segment_grid([]) is None


# ── 全流程（mock ffmpeg + 视觉） ──

@pytest.fixture
def mock_video_pipeline(monkeypatch):
    """拦截探测 JSON / 抽帧 / 视觉调用 — 全链路无 ffmpeg 依赖"""
    import infra.data_process.core.video_analyzer as va

    calls = {"analyze": []}

    def _fake_ffprobe(args):
        return json.dumps({
            "format": {"duration": "65.0"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"}],
        })

    def _fake_extract(video_path, plan, out_dir):
        paths = []
        for i in range(plan.total_frames):
            p = os.path.join(out_dir, f"frame_{i:04d}.jpg")
            with open(p, "wb") as f:
                f.write(_mk_jpg())
            paths.append((i / plan.sample_fps, p))
        return paths

    async def _fake_analyze(image_bytes, prompt):
        calls["analyze"].append(prompt)
        return {"description": f"SEG{len(calls['analyze'])}: a test scene", "error": None}

    monkeypatch.setattr(va, "_run_ffprobe", _fake_ffprobe)
    monkeypatch.setattr(va, "_run_ffmpeg", lambda *a, **k: None)
    # 沙箱可能没装 ffmpeg/ffprobe — 使可用性检查恒真（extract/probe 已被 mock）
    monkeypatch.setattr(va.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(va, "extract_frames", _fake_extract)
    monkeypatch.setattr(va, "_analyze_image_bundle", _fake_analyze)
    return calls


class TestAnalyzeVideoBytes:
    def test_full_flow(self, mock_video_pipeline, sample_settings):
        calls = mock_video_pipeline
        res = _run(get_video_analyzer().analyze_video_bytes(b"FAKE", "clip.mp4"))
        assert res["ok"] is True
        assert res["duration"] == pytest.approx(65.0)
        assert res["truncated"] is False
        assert res["frame_count"] == 8           # 65s * (4/30) = 8 帧
        assert res["segment_count"] >= 1
        assert calls["analyze"], "视觉模型应被逐段调用"
        assert res["segments"][0]["end"] > res["segments"][0]["start"]

    def test_disabled_flag_guards(self, sample_settings, monkeypatch):
        monkeypatch.setattr(settings, "VIDEO_ANALYSIS_ENABLED", False, raising=True)
        res = _run(get_video_analyzer().analyze_video_bytes(b"X", "v.mp4"))
        assert res["ok"] is False
        assert "未启用" in res.get("error", "")

    def test_oversize_guard(self, sample_settings, monkeypatch):
        monkeypatch.setattr(settings, "VIDEO_MAX_SIZE_MB", 1, raising=True)
        res = _run(get_video_analyzer().analyze_video_bytes(b"x" * (2 * 1024 * 1024), "big.mp4"))
        assert res["ok"] is False
        assert "1MB" in res.get("error", "")

    def test_missing_ffmpeg_msg(self, sample_settings, monkeypatch):
        import infra.data_process.core.video_analyzer as va
        # 所有探测来源全部关闭：which + 配置 + 常见目录（本机可能装有 conda/brew ffmpeg）
        monkeypatch.setattr(va, "find_ffmpeg_tool", lambda _n: None)
        res = _run(get_video_analyzer().analyze_video_bytes(b"X", "v.mp4"))
        assert res["ok"] is False
        assert "ffmpeg" in res["error"]


# ── 附件路由（三模式共用） ──

def _video_attachment(name="clip.mp4"):
    raw = b"FAKE_VIDEO_BYTES"
    data = "data:video/mp4;base64," + base64.b64encode(raw).decode()
    return {"type": "video/mp4", "name": name, "data": data}


@pytest.fixture
def mock_attachment_video(monkeypatch):
    import modules.thinking.attachment_handler as ah
    async def _fake(att):
        return f"[用户上传视频: {att['name']}] MOCKED_VIDEO_DESC"
    monkeypatch.setattr(ah, "_analyze_video_attachment", _fake)


class TestAttachmentRouting:
    def test_describe_mode_video_routed(self, mock_attachment_video):
        from modules.thinking.attachment_handler import parse_attachments
        out = _run(parse_attachments([_video_attachment()]))
        assert "MOCKED_VIDEO_DESC" in out

    def test_direct_mode_video_routed_via_summarize(self, mock_attachment_video):
        from modules.thinking.attachment_handler import summarize_attachments
        out = _run(summarize_attachments([_video_attachment()]))
        assert "MOCKED_VIDEO_DESC" in out

    def test_disabled_image_video_still_routed(self, mock_attachment_video, monkeypatch):
        monkeypatch.setattr(settings, "LARGE_MODEL_ENABLE_IMAGE", False, raising=True)
        from modules.thinking.attachment_handler import summarize_attachments
        atts = [_video_attachment(), {"type": "image/png", "name": "a.png",
                                      "data": "data:image/png;base64," + base64.b64encode(_mk_jpg()).decode()}]
        out = _run(summarize_attachments(atts))
        assert "MOCKED_VIDEO_DESC" in out        # 视频仍识别
        assert "[用户上传图片: a.png]" in out      # 图片只标名

    def test_video_disabled_flag_msg(self, sample_settings, monkeypatch):
        from modules.thinking.attachment_handler import _analyze_video_attachment
        monkeypatch.setattr(settings, "VIDEO_ANALYSIS_ENABLED", False, raising=True)
        out = _run(_analyze_video_attachment(_video_attachment()))
        assert "未启用" in out

    def test_oversize_video_msg(self, sample_settings, monkeypatch):
        from modules.thinking.attachment_handler import _analyze_video_attachment
        monkeypatch.setattr(settings, "VIDEO_MAX_SIZE_MB", 1, raising=True)
        att = _video_attachment()
        att["data"] = "data:video/mp4;base64," + base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
        out = _run(_analyze_video_attachment(att))
        assert "1MB" in out


# ── 打包 ffmpeg 优先级链 ──

class TestBundledToolPriority:
    def test_bundle_dir_beats_path(self, sample_settings, monkeypatch, tmp_path):
        """_MEIPASS/ffmpeg 里的 ffprobe 命中，无需 PATH/配置。"""
        import infra.data_process.core.video_analyzer as va
        bundle = tmp_path / "ffmpeg"
        bundle.mkdir()
        for n in ("ffmpeg", "ffprobe"):
            binf = bundle / n
            binf.write_bytes(b"#!bin")
            binf.chmod(0o755)
        monkeypatch.setattr(va.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.delenv("VIDEO_FFMPEG_PATH", raising=False)
        monkeypatch.setattr(va.shutil, "which", lambda n: None)
        assert va.find_ffmpeg_tool("ffprobe") == str(bundle / "ffprobe")

    def test_config_overrides_bundle(self, sample_settings, monkeypatch, tmp_path):
        import infra.data_process.core.video_analyzer as va
        bundle = tmp_path / "ffmpeg"
        bundle.mkdir()
        (bundle / "ffmpeg").write_bytes(b"x")
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        cfg_bin = cfg_dir / "ffmpeg"
        cfg_bin.write_bytes(b"x")
        cfg_bin.chmod(0o755)
        monkeypatch.setattr(settings, "VIDEO_FFMPEG_PATH", str(cfg_dir), raising=True)
        monkeypatch.setattr(va.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(va.shutil, "which", lambda n: None)
        assert va.find_ffmpeg_tool("ffmpeg") == str(cfg_bin)

    def test_no_bundle_falls_back_to_which(self, sample_settings, monkeypatch, tmp_path):
        import infra.data_process.core.video_analyzer as va
        monkeypatch.setattr(va.sys, "_MEIPASS", str(tmp_path), raising=False)  # 空目录，无 ffmpeg
        monkeypatch.delenv("VIDEO_FFMPEG_PATH", raising=False)
        monkeypatch.setattr(va.shutil, "which", lambda n: f"/fake/{n}")
        assert va.find_ffmpeg_tool("ffmpeg") == "/fake/ffmpeg"


# ── 设置接线 ──

class TestSettingsWiring:
    def test_video_keys_modifiable(self):
        from config.settings import settings
        keys = {
            "VIDEO_ANALYSIS_ENABLED", "VIDEO_SEGMENT_SECONDS", "VIDEO_FRAMES_PER_SEGMENT",
            "VIDEO_MAX_DURATION_SECONDS", "VIDEO_MAX_SIZE_MB", "VIDEO_MAX_TOTAL_FRAMES",
            "VIDEO_FRAME_WIDTH", "VIDEO_JPEG_QUALITY", "VIDEO_DESC_MAX_CHARS",
        }
        assert keys.issubset(settings._MODIFIABLE_FIELDS)

    def test_endpoint_registered(self):
        import infra.data_process.api as api_mod
        paths = {r.path for r in api_mod.router.routes}
        assert "/data-process/video/analyze" in paths
