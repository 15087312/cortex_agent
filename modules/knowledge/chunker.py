"""文档切分器 — 纯函数，无 IO，便于单测

流程：
    规范化文本 → 结构切分（md 标题 / py AST / 段落）→ 段内按句聚合到目标 token
    → 相邻块按句边界重叠 → 过小碎片并入相邻块 → 输出带精确字符区间的块

约定：
- 所有 char_start / char_end 都是「规范化后全文」的偏移，可直接回溯原文
- 切分不切断句子：只有单句本身超长时才硬切
- 块 text 不含标题行，标题存在 heading_path（索引时由 store 拼入嵌入文本）
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# 句子终止符（中文标点 + 换行）；"." 单独处理，避免切断 3.2 / file.py
_SENT_END = "。！？；!?;\n"
# 终止符后允许跟随的收尾符号（右引号/右括号等），随句子一起带走
_CLOSERS = "”\"’')）】》」』]"
# 次要切分点（超长单句硬切时用）
_SOFT_BREAK = "，,、：:/ \t"

_ENCODER = None
_ENCODER_FAILED = False


@dataclass
class ChunkDraft:
    """切分产物（未落库）：不含 ID，由 store 分配"""
    seq: int
    heading_path: str
    text: str
    char_start: int
    char_end: int
    token_count: int


# @link:block Knowledge_TokenCount = Retrieve
def count_tokens(text: str) -> int:
    """token 计数：优先 tiktoken；不可用时退化为「CJK 1 字≈1 token，其他 4 字符≈1」"""
    global _ENCODER, _ENCODER_FAILED
    if not text:
        return 0
    if _ENCODER is None and not _ENCODER_FAILED:
        try:
            import tiktoken
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENCODER_FAILED = True
    if _ENCODER is not None:
        try:
            return len(_ENCODER.encode(text))
        except Exception:
            pass
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4 + 1


def normalize_text(raw: str) -> str:
    """统一换行、去除行尾空白、压缩 3+ 连续空行为 1 个空行。

    幂等：normalize_text(normalize_text(x)) == normalize_text(x)
    """
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def detect_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".py": "text/x-python",
        ".json": "application/json",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".toml": "application/toml",
    }.get(ext, "text/plain")


def derive_title(text: str, path: str = "") -> str:
    """标题优先级：正文首个一级标题 > 文件名主干"""
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    base = os.path.basename(path) or "未命名文档"
    return os.path.splitext(base)[0]


# ---------------------------------------------------------------------------
# 结构切分：把全文切成「带标题路径的段落区间」
# ---------------------------------------------------------------------------

def _markdown_sections(text: str) -> List[Tuple[str, int, int]]:
    """按 ATX 标题切节，返回 [(heading_path, body_start, body_end)]。

    代码围栏（``` / ~~~）内的 # 不算标题。标题行本身不进正文区间。
    """
    sections: List[Tuple[str, int, int]] = []
    stack: List[Tuple[int, str]] = []
    in_fence = False
    body_start = 0
    pos = 0
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

    def flush(end: int):
        if end > body_start:
            path = " > ".join(t for _, t in stack)
            sections.append((path, body_start, end))

    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence:
            m = heading_re.match(line.rstrip("\n"))
            if m:
                flush(pos)
                level = len(m.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, m.group(2).strip()))
                body_start = pos + len(line)
        pos += len(line)
    flush(len(text))
    return sections


def _python_sections(text: str) -> List[Tuple[str, int, int]]:
    """按顶层 def/class 切节（ast）；解析失败退化为段落模式。

    装饰器也算进节点区间，避免装饰器与 def 被拆到两块。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def offset(lineno: int) -> int:
        idx = max(1, lineno) - 1
        return line_starts[idx] if idx < len(line_starts) else len(text)

    nodes = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if not nodes:
        return []

    sections: List[Tuple[str, int, int]] = []
    if offset(nodes[0].lineno) > 0:
        head_end = offset(nodes[0].lineno)
        head = text[:head_end].strip()
        if head:
            sections.append(("模块头: imports / 常量", 0, head_end))

    for i, node in enumerate(nodes):
        start_line = min(
            [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
        )
        start = offset(start_line)
        end = offset((node.end_lineno or node.lineno) + 1)
        end = min(end, len(text))
        kind = "class" if isinstance(node, ast.ClassDef) else "def"
        sections.append((f"{kind} {node.name}", start, end))
    return sections


def _paragraph_sections(text: str) -> List[Tuple[str, int, int]]:
    """按空行分段（非 markdown/py 的兜底结构）。"""
    sections: List[Tuple[str, int, int]] = []
    for m in re.finditer(r"[^\n]+(?:\n(?![ \t]*\n)[^\n]*)*", text):
        start, end = m.start(), m.end()
        if text[start:end].strip():
            sections.append(("", start, end))
    return sections


# ---------------------------------------------------------------------------
# 句级切分（保留偏移）
# ---------------------------------------------------------------------------

def _trim(text: str, start: int, end: int) -> Optional[Tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def _is_dot_end(text: str, i: int, end: int) -> bool:
    """"." 只在末尾或后接空白时算句末（避免切断 3.2 / file.py / v1.0）"""
    if text[i] != ".":
        return False
    return i + 1 >= end or text[i + 1].isspace()


def _split_units(text: str, start: int, end: int) -> List[Tuple[int, int]]:
    """把区间切成句子级单元，返回去除首尾空白后的 (s, e) 列表。"""
    units: List[Tuple[int, int]] = []
    seg_start = start
    i = start
    while i < end:
        ch = text[i]
        is_end = ch in _SENT_END or _is_dot_end(text, i, end)
        if is_end:
            j = i + 1
            while j < end and text[j] in _CLOSERS:
                j += 1
            trimmed = _trim(text, seg_start, j)
            if trimmed:
                units.append(trimmed)
            seg_start = j
            i = j
            continue
        i += 1
    trimmed = _trim(text, seg_start, end)
    if trimmed:
        units.append(trimmed)
    return units


def _hard_split(text: str, start: int, end: int, max_tokens: int) -> List[Tuple[int, int]]:
    """超长单元硬切：按 token 预算切，优先在软切分点断开，尽量不破坏词。"""
    if count_tokens(text[start:end]) <= max_tokens:
        return [(start, end)]
    pieces: List[Tuple[int, int]] = []
    cur = start
    while cur < end:
        remain = count_tokens(text[cur:end])
        if remain <= max_tokens:
            trimmed = _trim(text, cur, end)
            if trimmed:
                pieces.append(trimmed)
            break
        approx = max(32, int((end - cur) * max_tokens / max(1, remain)))
        cut = min(cur + approx, end)
        floor = cur + max(16, approx // 2)
        while cut > floor and text[cut] not in _SOFT_BREAK:
            cut -= 1
        if cut <= cur:
            cut = min(cur + approx, end)
        trimmed = _trim(text, cur, cut)
        if trimmed:
            pieces.append(trimmed)
        cur = cut
    return pieces


def _unit_tokens(text: str, spans: List[Tuple[int, int]]) -> List[int]:
    return [count_tokens(text[s:e]) for s, e in spans]


# @link:block Knowledge_PackChunks = Write
def _pack_section(
    text: str,
    spans: List[Tuple[int, int]],
    heading_path: str,
    target: int,
    overlap: int,
    min_tokens: int,
    seq_start: int,
) -> List[ChunkDraft]:
    """把句子单元按目标 token 打包成块，块间按句边界重叠。"""
    if not spans:
        return []

    # 超长单句先硬切，保证任何块都接近目标大小
    flat: List[Tuple[int, int]] = []
    for s, e in spans:
        flat.extend(_hard_split(text, s, e, target))
    spans = flat

    tokens = _unit_tokens(text, spans)
    drafts: List[ChunkDraft] = []
    i = 0
    total = len(spans)
    while i < total:
        begin = i
        acc = 0
        last = i
        while last < total and (last == begin or acc + tokens[last] <= target):
            acc += tokens[last]
            last += 1
        s, e = spans[begin][0], spans[last - 1][1]
        drafts.append(ChunkDraft(
            seq=seq_start + len(drafts),
            heading_path=heading_path,
            text=text[s:e],
            char_start=s,
            char_end=e,
            token_count=acc,
        ))
        if last >= total:
            break
        # 回退若干句作为下一块的重叠前缀
        nxt = last
        back = 0
        while nxt > begin + 1 and back < overlap:
            nxt -= 1
            back += tokens[nxt]
        i = nxt

    # 过小碎片并入上一块（同标题路径内），避免产生无信息量的尾巴
    if len(drafts) > 1 and drafts[-1].token_count < min_tokens:
        tail = drafts.pop()
        prev = drafts[-1]
        prev.text = text[prev.char_start:tail.char_end]
        prev.char_end = tail.char_end
        prev.token_count = count_tokens(prev.text)
    return drafts


# @link:block Knowledge_ChunkDocument = Write
def chunk_document(
    text: str,
    source_path: str = "",
    target_tokens: int = 400,
    overlap_tokens: int = 80,
    min_tokens: int = 20,
    max_chunks: int = 5000,
) -> List[ChunkDraft]:
    """切分规范化后的全文，返回按 seq 递增的块列表。

    Args:
        text: 已 normalize_text 的全文（偏移基于此串）
        source_path: 仅用于判定结构类型（扩展名）
        target_tokens / overlap_tokens / min_tokens: 目标块大小 / 重叠 / 最小碎片
        max_chunks: 上限，超出抛 ValueError（防误 ingest 巨物）
    """
    if not text or not text.strip():
        return []

    ext = os.path.splitext(source_path)[1].lower()
    if ext in (".md", ".markdown"):
        sections = _markdown_sections(text)
    elif ext == ".py":
        sections = _python_sections(text) or _paragraph_sections(text)
    else:
        sections = _paragraph_sections(text)
    if not sections:
        sections = [("", 0, len(text))]

    drafts: List[ChunkDraft] = []
    for heading_path, start, end in sections:
        spans = _split_units(text, start, end)
        drafts.extend(_pack_section(
            text, spans, heading_path, target_tokens, overlap_tokens, min_tokens,
            seq_start=len(drafts),
        ))
        if len(drafts) > max_chunks:
            raise ValueError(f"文档块数超过上限 {max_chunks}，请拆分文件后再入库")

    drafts = [d for d in drafts if d.text.strip()]
    for idx, d in enumerate(drafts):
        d.seq = idx
    return drafts[:max_chunks]


def chunk_embedding_text(title: str, heading_path: str, text: str) -> str:
    """构造用于向量化的文本：标题与章节路径提供上下文，正文保真。

    只影响嵌入，落库的 chunk.text 仍是原文子串。
    """
    parts = [p for p in (title, heading_path) if p]
    prefix = " > ".join(parts)
    return f"{prefix}\n{text}" if prefix else text
