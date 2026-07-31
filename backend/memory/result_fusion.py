"""
ResultFusion — 深度回忆结果融合与格式组装

把因果结论（因果链路 + 置信度 + 共享因子）与事件列表
按统一格式装配，供 ContinuousThinker 注入 prompt。
"""
from typing import List, Optional

from backend.memory.depth_recall import DeepRecallResult, DepthRecallScheduler
from backend.memory.event_store import MemoryEvent

from backend.utils.logger import get_logger
logger = get_logger(__name__)

# ── 输出模板 ──

_DEEP_RECALL_TEMPLATE = """【深度回忆 — 因果分析】
{conclusion}

【因果链路】
{chains_text}

【佐证事件】
{events_text}"""

_COUNTER_EXAMPLE_TEMPLATE = """
【反例 / 例外】
{counter_text}"""


def format_deep_recall_result(result: DeepRecallResult, max_events: int = 5) -> str:
    """将深度回忆结果格式化为可注入 prompt 的文本"""
    if not result.success or result.fallback:
        return ""

    # 因果结论
    conclusion = result.causal_conclusion or f"锚点: {result.anchor.label if result.anchor else '未知'}"

    # 因果链路
    chain_lines = []
    for chain in result.causal_chains:
        labels = [n.label for n in chain.nodes]
        direction = "→" if chain.direction == "forward" else "←"
        chain_lines.append(f"  {direction} {' → '.join(labels)} (置信度 {chain.confidence:.0%})")

    if result.shared_factors:
        chain_lines.append(f"  ★ 共享因果因子: {'、'.join(result.shared_factors)}")

    chains_text = "\n".join(chain_lines) if chain_lines else "  无"

    # 佐证事件
    event_lines = []
    for ev in result.supporting_events[:max_events]:
        event_lines.append(f"  · {ev.fact} (重要性 {ev.importance:.0%})")

    events_text = "\n".join(event_lines) if event_lines else "  无"

    # 组装
    parts = [
        _DEEP_RECALL_TEMPLATE.format(
            conclusion=conclusion,
            chains_text=chains_text,
            events_text=events_text,
        ),
    ]

    if result.counter_examples:
        counter_lines = [f"  · {ev.fact}" for ev in result.counter_examples[:3]]
        parts.append(
            _COUNTER_EXAMPLE_TEMPLATE.format(counter_text="\n".join(counter_lines))
        )

    return "\n".join(parts)


def format_retrieve_result(events: List[MemoryEvent], max_events: int = 5) -> str:
    """格式化为浅层检索文本"""
    if not events:
        return ""
    lines = ["【相关记忆】"]
    for ev in events[:max_events]:
        lines.append(f"· {ev.fact} (重要性 {ev.importance:.0%})")
    return "\n".join(lines)


class ResultFusion:
    """回忆结果融合器"""

    def __init__(self, scheduler: DepthRecallScheduler = None):
        self._scheduler = scheduler or DepthRecallScheduler()

    async def recall_and_fuse(
        self,
        query: str,
        max_results: int = 10,
        depth_level: int = 1,
        task_type: str = "",
        shallow_events: Optional[List[MemoryEvent]] = None,
    ) -> str:
        """执行完整回忆（先试深度，失败则用浅层），返回格式化文本

        Args:
            query: 查询文本
            max_results: 最多返回事件数
            depth_level: 1=标准 2=深度
            task_type: 任务类型（影响触发判断）
            shallow_events: 已取得的浅层检索结果（可选）
        """
        from backend.memory.depth_recall import should_trigger_deep_recall

        trigger, reason = should_trigger_deep_recall(query, task_type=task_type)
        if trigger:
            deep_result = await self._scheduler.deep_recall(
                query, max_results, depth_level, task_type=task_type,
            )
            if deep_result.success and not deep_result.fallback:
                return format_deep_recall_result(deep_result, max_events=max_results)

        # 回退：使用浅层结果
        if shallow_events:
            return format_retrieve_result(shallow_events, max_events=max_results)
        return ""
