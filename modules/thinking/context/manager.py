"""
上下文格式化 — 临时保留，待迁入 GuidanceSource / DelegationSource
"""
from typing import Dict, List


class ContextManager:
    """上下文格式化（静态方法集合）"""

    @staticmethod
    def build_external_guidance(
        persistent_prompts: List[str],
        transient_prompts: List[str],
    ) -> str:
        external_parts = []
        if persistent_prompts:
            limited = persistent_prompts[-5:]
            combined = "\n\n".join(
                f"[系统简报 #{i+1}]\n{pp}"
                for i, pp in enumerate(limited)
            )
            external_parts.append(combined)
        if transient_prompts:
            transient_text = "\n\n".join(
                f"[本轮提示 #{i+1}]\n{tp}"
                for i, tp in enumerate(transient_prompts[-3:])
            )
            external_parts.append(transient_text)
        return "\n\n".join(external_parts)

    @staticmethod
    def build_delegation_status(pending_delegations: Dict[str, dict]) -> str:
        if not pending_delegations:
            return ""
        recent = list(pending_delegations.values())[-10:]
        if not recent:
            return ""
        has_pending = any(d.get("status") == "pending" for d in recent)
        lines = ["【当前委托状态】"]
        if has_pending:
            lines.append("有委托正在等待专家回复。请勿向用户追问，耐心等待结果。")
        for d in recent:
            status_icon = {
                "pending": "pending", "replied": "done", "completed": "done", "stale": "timeout",
            }.get(d.get("status", "pending"), "?")
            lines.append(
                f"  [{status_icon}] 第{d['round']}轮委托 [{d['role']}]: {d['task'][:80]}"
            )
        return "\n".join(lines)
