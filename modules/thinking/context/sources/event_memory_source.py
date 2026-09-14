"""EventMemorySource — 全局事件记忆 → 格式化文本片段

thinking 层合法依赖 memory，这里把"检索事件记忆并格式化为 prompt 片段"抽成
公共函数，供感知层主动搭话等场景复用，避免业务模块直接依赖 memory。

用法:
    frag = await collect_event_memory(prompt)
    if frag:
        extras.append(frag)
"""


async def collect_event_memory(prompt: str, max_results: int = 3, threshold: float = 0.10) -> str:
    """检索与 prompt 相关的全局事件记忆，返回格式化文本片段；无结果返回空串。"""
    try:
        from modules.memory.event_retrieval import get_event_retrieval

        events = await get_event_retrieval().retrieve(
            query=prompt, max_results=max_results, threshold=threshold
        )
    except Exception:  # pragma: no cover — 记忆子系统不可用时静默降级
        return ""
    if not events:
        return ""
    lines = [
        "【曾经发生的事】",
        "（以下为过去的事件记忆，仅供参考，不要把过去任务当作当前任务执行）",
    ]
    for i, ev in enumerate(events, 1):
        date = str(getattr(ev, "time", "") or "")[:10] or "未知日期"
        lines.append(f"  [{i}] (日期={date}) {str(getattr(ev, 'fact', ''))[:150]}")
    return "\n".join(lines)
