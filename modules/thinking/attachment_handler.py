"""附件解析 — 将聊天附件的图片/文件转换为文本注入对话上下文

图片 → 视觉后端（ImageAnalyzer）生成描述；其他文件 → 标注文件名。
"""
import base64
import re
from typing import List

from pydantic import BaseModel

from utils.logger import setup_logger

logger = setup_logger("attachment_handler")


class ChatAttachment(BaseModel):
    """聊天附件元素契约（WS input.attachments 每项：{type, name, data}）

    - type:  MIME 类型（image/png、text/plain …）
    - name:  原始文件名
    - data:  base64 dataURL（data:image/png;base64,…）
    """

    type: str = ""
    name: str = ""
    data: str = ""


ALLOWED_VIDEO_EXT = {
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".flv", ".ts", ".mpg", ".mpeg",
}


def _is_video(att: dict) -> bool:
    """判断附件是否为视频（MIME video/* 或扩展名白名单[某些客户端不传 video MIME]）"""
    atype = str(att.get("type") or "")
    if atype.startswith("video/"):
        return True
    name = str(att.get("name") or "")
    dot = name.lower().rfind(".")
    return dot > 0 and name[dot:].lower() in ALLOWED_VIDEO_EXT


async def _analyze_video_attachment(att: dict) -> str:
    """视频附件 → 分段采样识别 → 一行时间轴描述文本（供任一注入路径复用）

    三种图片模式都走这里（视频从来不该直连给大模型，肉眼可见的成本/长度不可控）：
    - describe：作为附件描述注入
    - direct  ：同样转成文字（大模型只需要知道视频内容，不用看图）
    - 图片关闭：视频识别仍可用（独立于 LARGE_MODEL_ENABLE_IMAGE，受 VIDEO_ANALYSIS_ENABLED 控制）
    """
    from config.settings import settings
    name = str(att.get("name") or "")
    atype = str(att.get("type") or "")
    label = f"[用户上传视频: {name or atype}]"
    if not getattr(settings, "VIDEO_ANALYSIS_ENABLED", True):
        return f"{label}（视频识别未启用）"
    data = str(att.get("data") or "")
    if not data:
        return f"{label}（视频内容为空）"
    try:
        m = re.match(r"data:[^;]+;base64,(.*)", data, re.S)
        raw = m.group(1) if m else data
        video_bytes = base64.b64decode(raw)
        # 体积守卫：超限直接返回提示，不解码不清 GPU
        max_mb = max(1, int(getattr(settings, "VIDEO_MAX_SIZE_MB", 50) or 50))
        if len(video_bytes) > max_mb * 1024 * 1024:
            return f"{label}（视频超过 {max_mb}MB 识别上限，未处理）"
        from infra.data_process.core.video_analyzer import get_video_analyzer
        res = await get_video_analyzer().analyze_video_bytes(video_bytes, name)
        if not res.get("ok"):
            return f"{label}（{res.get('error', '视频识别失败')}）"
        desc = str(res.get("description") or "(无描述)")
        extra = []
        if res.get("truncated"):
            extra.append("视频超出处理时长上限已截断")
        extra.append(f"时长 {res.get('duration')}s / 采样 {res.get('frame_count')} 帧 / {res.get('segment_count')} 段")
        return f"{label}（{', '.join(extra)}）视频内容：\n{desc}"
    except Exception as e:
        logger.warning(f"[视频附件解析失败] {e}")
        return f"{label}（视频解析失败: {e}）"


def validate_attachments(value) -> str:
    """校验 WS input.attachments 形状，返回错误描述（空字符串表示合法）。

    前端契约：attachments 为数组，每项为 {type, name, data} 对象。
    历史事故：前端曾发送裸 base64 字符串数组，后端 parse_attachments 因
    ``if not isinstance(att, dict): continue`` 而静默丢弃，图片"发不出去"。
    网关层应先用本函数校验，非法形状显式报错而非静默吞掉。
    """
    if value is None:
        return ""
    if not isinstance(value, list):
        return f"attachments 必须为数组，收到 {type(value).__name__}"
    for i, att in enumerate(value):
        if not isinstance(att, dict):
            return f"attachments[{i}] 必须为对象，收到 {type(att).__name__}"
        data = att.get("data")
        if not isinstance(data, str) or not data:
            return f"attachments[{i}] 缺少非空 data 字段（应为 base64 dataURL 字符串）"
    return ""


async def parse_attachments(attachments) -> str:
    """解析附件列表为文本描述（追加到用户消息）

    图片 → 视觉后端（ImageAnalyzer）生成描述；其他文件 → 标注文件名。
    """
    if not attachments:
        return ""
    parts = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        data = str(att.get("data") or "")
        name = str(att.get("name") or "")
        atype = str(att.get("type") or "")

        if atype.startswith("image/"):
            try:
                m = re.match(r"data:image/[^;]+;base64,(.*)", data, re.S)
                raw = m.group(1) if m else data
                img_bytes = base64.b64decode(raw)
                from infra.data_process.core.image_analyzer import ImageAnalyzer
                res = await ImageAnalyzer().analyze(img_bytes, prompt="请详细描述这张图片的内容（物体/场景/文字等）")
                desc = str(res.get("description") or res.get("error") or "(无法识别图片)")
                parts.append(f"[用户上传图片: {name or atype}] 图片内容：{desc[:500]}")
            except Exception as e:
                parts.append(f"[用户上传图片: {name or atype}]（图片解析失败: {e}）")
        elif _is_video(att):
            parts.append(await _analyze_video_attachment(att))
        else:
            # 非图片文件：尝试当文本读取，否则仅标注文件名
            try:
                if data.startswith("data:text/"):
                    m = re.match(r"data:text/[^;]+;base64,(.*)", data, re.S)
                    if m:
                        text = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
                        parts.append(f"[用户上传文件: {name or atype}] 内容：\n{text[:2000]}")
                        continue
            except Exception as _swallowed_err:
                logger.debug(f"[parse_attachments] ignored Exception: {_swallowed_err}")
            parts.append(f"[用户上传文件: {name or atype}]")
    return "\n".join(parts)


def extract_images(attachments) -> List[str]:
    """提取图片附件为 dataURL 列表（直连多模态模式用，不调用视觉模型）"""
    if not attachments:
        return []
    images = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        atype = str(att.get("type") or "")
        data = str(att.get("data") or "")
        if atype.startswith("image/") and data:
            images.append(data)
    return images


async def summarize_attachments(attachments) -> str:
    """不调视觉模型的轻量标注（直连多模态模式用）

    图片只标文件名（原图由 extract_images 直接附给大模型）；文本文件读内容。
    例外：视频附件走分段采样识别（视频无法直连，必须转文字才能被大模型理解）。
    """
    if not attachments:
        return ""
    parts = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        data = str(att.get("data") or "")
        name = str(att.get("name") or "")
        atype = str(att.get("type") or "")
        if atype.startswith("image/"):
            parts.append(f"[用户上传图片: {name or atype}]")
        elif _is_video(att):
            # 视频：无论直连/关闭模式都转文字（大模型看不了视频，识别结果即上下文）
            parts.append(await _analyze_video_attachment(att))
        else:
            try:
                if data.startswith("data:text/"):
                    m = re.match(r"data:text/[^;]+;base64,(.*)", data, re.S)
                    if m:
                        text = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
                        parts.append(f"[用户上传文件: {name or atype}] 内容：\n{text[:2000]}")
                        continue
            except Exception as _swallowed_err:
                logger.debug(f"[summarize_attachments] ignored Exception: {_swallowed_err}")
            parts.append(f"[用户上传文件: {name or atype}]")
    return "\n".join(parts)
