"""桌宠发声标记 — 桌宠自己决定哪部分「说出口」

约定：模型把要说出口的句子包在 `<say>…</say>` 里，其余文字视为内心独白。
- 内心独白：入库、显示、参与后续思考，但**不朗读**
- 说出口的内容：朗读（TTS）+ 显示

没有 `<say>` 标签时的兜底由调用方决定（见 PetEngine._after_reply）：
- 用户刚说了一句话 → 整段都算回答，朗读
- 桌宠自己思考（无用户输入）→ 视为内心独白，不朗读
"""
import re
from typing import Tuple

_SAY_RE = re.compile(r"<\s*say\s*>(.*?)<\s*/\s*say\s*>", re.S | re.I)
_SAY_TAG_RE = re.compile(r"<\s*/?\s*say\s*>", re.I)
# 代码块/行内代码/标记符号：朗读时去掉，避免念出符号
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MARKUP_RE = re.compile(r"[*_#>~|]+")
_URL_RE = re.compile(r"https?://\S+")


def has_say(text: str) -> bool:
    """文本里是否出现 <say> 标记（无论是否闭合）"""
    return bool(_SAY_TAG_RE.search(text or ""))


def split_speech(text: str) -> Tuple[str, str]:
    """拆分文本 → (可见/入库文本, 要朗读的文本)

    可见文本保留说出口的内容（去掉标记本身），所以气泡/历史里读起来是连贯的话。
    朗读文本只取 `<say>` 内的片段，多段按出现顺序用空格连接。
    """
    raw = text or ""
    spoken_parts = [
        _SAY_RE.sub(r"\1", m.group(1)).strip()
        for m in _SAY_RE.finditer(raw)
    ]
    spoken = " ".join(p for p in spoken_parts if p).strip()
    visible = _SAY_TAG_RE.sub("", raw).strip()
    visible = re.sub(r"\n{3,}", "\n\n", visible)
    return visible, spoken


def clean_for_tts(text: str) -> str:
    """朗读前清洗：去代码/URL/Markdown 符号与多余空白"""
    s = text or ""
    s = _CODE_BLOCK_RE.sub("", s)
    s = _INLINE_CODE_RE.sub(r"\1", s)
    s = _URL_RE.sub("", s)
    s = _MARKUP_RE.sub("", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()
