"""
上下文格式化 — 临时保留，待迁入 GuidanceSource / DelegationSource
"""
from typing import Any, Dict, List


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
    def build_delegation_status(
        pending_delegations: Dict[str, dict],
        blackboard: Any = None,
        scope_model_id: str = "",
        scope_tier: str = "",
    ) -> str:
        """构建委托链摘要。

        - 从黑板（blackboard.delegations）读取完整委托链（含父子/进度/target_model/状态）
        - scope_model_id 非空（主管）时，只展示该模型发起/负责的委托及其下级
        - 否则（大模型）展示全部委托链
        """
        # 有黑板 → 用完整委托链；否则回退 pending_delegations 概要
        if blackboard is not None and hasattr(blackboard, "delegations") and blackboard.delegations:
            from dataclasses import asdict
            all_d = {k: asdict(v) for k, v in blackboard.delegations.items()}
            # 按 scope 过滤：主管只看自己发起/负责的委托
            if scope_model_id:
                scope_ids = {
                    did for did, d in all_d.items()
                    if d.get("caller_model_id") == scope_model_id
                    or d.get("return_to_model_id") == scope_model_id
                    or d.get("target_model_id") == scope_model_id
                }
                # 追加这些委托的下级（子委托），形成完整下属链
                def _collect_children(did):
                    for cid, d in all_d.items():
                        if d.get("parent_delegation_id") == did and cid not in scope_ids:
                            scope_ids.add(cid)
                            _collect_children(cid)
                for did in list(scope_ids):
                    _collect_children(did)
                chain = {k: v for k, v in all_d.items() if k in scope_ids}
            else:
                chain = all_d

            if not chain:
                return ""
            # 按创建时间排序
            ordered = sorted(chain.items(), key=lambda kv: kv[1].get("created_at", 0) or 0)
            lines = ["【委托链（协作过程）】"]
            for did, d in ordered[-12:]:
                status = d.get("status", "pending")
                icon = {"pending": "…", "running": "▶", "replied": "✓", "completed": "✓", "stale": "⏱"}.get(status, "?")
                role = d.get("role", "?")
                parent = d.get("parent_delegation_id", "")
                target = d.get("target_model_id", "")
                task = str(d.get("task", ""))[:60]
                progress = str(d.get("progress", ""))[:40]
                parent_txt = f" 父委托:{parent}" if parent else ""
                target_txt = f" target={target}" if target else ""
                prog_txt = f" 进度:{progress}" if progress else ""
                lines.append(f"  [{icon}] {did} ({role}){parent_txt}{target_txt}{prog_txt} 任务:{task}")
            return "\n".join(lines)

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
