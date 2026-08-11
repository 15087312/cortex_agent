"""附件解析 — 将聊天附件的图片/文件转换为文本注入对话上下文

图片 → 视觉后端（ImageAnalyzer）生成描述；其他文件 → 标注文件名。
"""
import base64
import re

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
    """解析附件列表为文本描述（追加到用户消息）"""
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
        else:
            # 非图片文件：尝试当文本读取，否则仅标注文件名
            try:
                if data.startswith("data:text/"):
                    m = re.match(r"data:text/[^;]+;base64,(.*)", data, re.S)
                    if m:
                        text = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
                        parts.append(f"[用户上传文件: {name or atype}] 内容：\n{text[:2000]}")
                        continue
            except Exception:
                pass
            parts.append(f"[用户上传文件: {name or atype}]")
    return "\n".join(parts)
