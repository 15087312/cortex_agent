"""
EventValidation — 记忆事件写入前的结构校验与批内去重

定位
    代码管结构，模型管语义。本模块只回答「这条事件能不能安全落盘」，
    不判断「值不值得记」——那是 LLM 提炼与检索打分的事。

与「只追加」架构的关系
    事件是历史记录，不是可改写的人设状态。因此这里只做**截断、丢弃、去重**，
    绝不改写已存在的事件，也不把新事件合并进旧事件。校验不引入 status /
    supersedes 之类会破坏"事件不可变"语义的字段。

失败隔离
    sanitize_event 不抛异常；不合格的事件返回 event=None 并附原因，
    由调用方跳过该条、继续处理同批其他事件。
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from modules.memory.event_store import MemoryEvent

# ── 结构边界（提示词要求 fact 20-80 字，这里给足余量，只挡明显异常）──
VALID_TYPES: Tuple[str, ...] = ("emotion", "thought", "fact", "strategy")
MAX_FACT_CHARS: int = 200
MAX_THOUGHT_CHARS: int = 300
MAX_LESSON_CHARS: int = 200
MIN_FACT_CHARS: int = 2
MAX_KEYWORDS: int = 6
MAX_KEYWORD_CHARS: int = 40
MIN_KEYWORD_CHARS: int = 2

# 句读符：截断时优先在这些位置收尾，避免把一句话从中间切断
_SENTENCE_END = "。！？；.!?;"
_PUNCT_RE = re.compile(
    r"[\s，。！？、；：“”‘’（）《》【】,.!?;:\"'()\[\]{}<>|/\\~`@#$%^&*+=_\-]+"
)


def normalize_for_match(text: str) -> str:
    """比对用归一化：忽略大小写、空白与标点。"""
    return _PUNCT_RE.sub("", (text or "").lower())


def _clip(text: str, limit: int) -> str:
    """截断到 limit 字，尽量在句读处收尾。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    for i in range(len(head) - 1, max(limit // 2, 0) - 1, -1):
        if head[i] in _SENTENCE_END:
            return head[: i + 1]
    # 省略号也算进上限，避免截断后反而超长
    return head[: limit - 1].rstrip() + "…"


@dataclass
class SanitizedEvent:
    """单条事件的清洗结果。event 为 None 表示该条应被丢弃。"""

    event: Optional[MemoryEvent] = None
    reasons: List[str] = field(default_factory=list)


@dataclass
class RejectedEvent:
    """被丢弃的事件（只保留可读摘要，避免日志里带整段正文）。"""

    fact: str
    reasons: List[str] = field(default_factory=list)


# @link:block MemValidate
def sanitize_event(
    event: MemoryEvent, conversation_text: str = ""
) -> SanitizedEvent:
    """校验并清洗单条事件，永不抛异常。

    Args:
        event: 待写入的事件
        conversation_text: 本批事件所依据的原始对话。提供时用于核对
            keywords 是否真的出现在原文里（为空则跳过该项核对）。

    Returns:
        SanitizedEvent：event 为清洗后的事件，reasons 记录所有修正/拒绝原因。
    """
    reasons: List[str] = []

    try:
        # 1. fact：唯一硬性必填项
        fact = (event.fact or "").strip()
        if len(fact) < MIN_FACT_CHARS:
            return SanitizedEvent(
                event=None, reasons=[f"fact 为空或过短（<{MIN_FACT_CHARS} 字）"]
            )
        clipped = _clip(fact, MAX_FACT_CHARS)
        if clipped != fact:
            reasons.append(
                f"fact 超长：{len(fact)} 字截断到 {len(clipped)} 字（上限 {MAX_FACT_CHARS}）"
            )
            fact = clipped
        event.fact = fact

        # 2. thought / lesson：只截断，不因超长丢弃
        for attr, limit in (("thought", MAX_THOUGHT_CHARS), ("lesson", MAX_LESSON_CHARS)):
            raw = (getattr(event, attr) or "").strip()
            clipped = _clip(raw, limit)
            if clipped != raw:
                reasons.append(
                    f"{attr} 超长：{len(raw)} 字截断到 {len(clipped)} 字（上限 {limit}）"
                )
            setattr(event, attr, clipped)

        # 3. type：白名单外一律归为 fact（保持既有行为，但记录一次）
        event_type = (event.type or "fact").strip().lower()
        if event_type not in VALID_TYPES:
            reasons.append(f"type「{event.type}」不在白名单，归一为 fact")
            event_type = "fact"
        event.type = event_type

        # 4. importance：夹到 [0, 1]
        try:
            importance = float(event.importance)
        except (TypeError, ValueError):
            importance = 0.4
            reasons.append("importance 非数值，回落 medium(0.4)")
        if not 0.0 <= importance <= 1.0:
            clamped = max(0.0, min(1.0, importance))
            reasons.append(f"importance {importance} 越界，夹到 {clamped}")
            importance = clamped
        event.importance = importance

        # 5. keywords：去空、去重、截断、超长丢弃；给了原文就核对是否真的出现
        raw_keywords = event.keywords if isinstance(event.keywords, list) else []
        normalized_text = normalize_for_match(conversation_text)
        kept: List[str] = []
        seen: set = set()
        dropped_by_source = 0
        for item in raw_keywords:
            kw = str(item or "").strip()
            if not kw:
                continue
            if len(kw) > MAX_KEYWORD_CHARS:
                continue
            key = normalize_for_match(kw)
            # 归一化后不足 2 字的词（如单个字母）会在原文里命中一切，无检索价值
            if len(key) < MIN_KEYWORD_CHARS:
                continue
            if key in seen:
                continue
            if normalized_text and key not in normalized_text:
                dropped_by_source += 1
                continue
            seen.add(key)
            kept.append(kw)
        if dropped_by_source:
            reasons.append(f"丢弃 {dropped_by_source} 个未出现在原文中的关键词")
        if len(kept) > MAX_KEYWORDS:
            reasons.append(f"关键词 {len(kept)} 个超过上限 {MAX_KEYWORDS}，已截断")
            kept = kept[:MAX_KEYWORDS]
        event.keywords = kept

        return SanitizedEvent(event=event, reasons=reasons)
    except Exception as exc:  # 防御：校验本身不能成为新的失败源
        return SanitizedEvent(event=None, reasons=[f"校验异常：{exc}"])


# @link:block MemSanitizeBatch
def sanitize_batch(
    events: Sequence[MemoryEvent], conversation_text: str = ""
) -> Tuple[List[MemoryEvent], List[RejectedEvent]]:
    """批量校验 + 批内去重。

    Returns:
        (accepted, rejected)：accepted 为可落盘的事件（保持原顺序），
        rejected 为被丢弃的事件及原因。
    """
    accepted: List[MemoryEvent] = []
    rejected: List[RejectedEvent] = []
    seen_facts: set = set()

    for event in events or []:
        result = sanitize_event(event, conversation_text)
        if result.event is None:
            rejected.append(
                RejectedEvent(fact=(event.fact or "")[:80], reasons=result.reasons)
            )
            continue

        fact_key = normalize_for_match(result.event.fact)
        if fact_key in seen_facts:
            rejected.append(
                RejectedEvent(
                    fact=result.event.fact[:80], reasons=["同批内 fact 重复"]
                )
            )
            continue
        seen_facts.add(fact_key)

        accepted.append(result.event)

    return accepted, rejected
