"""
上下文切片器 — 为每个 tier 生成定制化上下文

替代 SharedDialog.format_for_model() 的全量读取。

核心思想：
- Large 看到：完整的目标 + 计划 + 所有发现
- Supervisor 看到：具体的任务描述 + 目标背景 + 工具列表
- Expert 看到：具体的执行步骤 + 工具状态 + 最近5条历史

这样每个 Agent 的 prompt 都是精心裁剪的，不会淹没在无关信息中。
"""

from typing import Any, Dict, List, Optional
from .blackboard import CognitiveBlackboard, BlackboardSnapshot
from utils.logger import setup_logger

logger = setup_logger("context_slicer")


class ContextSlicer:
    """上下文切片器 — 为不同 tier 生成定制化上下文"""

    def slice_for_large(
        self,
        bb: CognitiveBlackboard,
    ) -> str:
        """
        为 Large 模型生成上下文

        包含：
        - 系统级观察（委托引导、专家引导等）
        - 目标
        - 当前计划
        - 最近的风险
        - 委托状态
        - 专家发现
        - 用户最新输入
        """
        parts = []

        # 0. 系统级观察（编排器写入的委托引导、专家引导等）
        system_observations = [
            o for o in bb.observations
            if o.tier == "system"
        ]
        if system_observations:
            obs_text = "\n\n".join(o.content for o in system_observations[-5:])
            parts.append(obs_text)

        # 1. 目标
        if bb.goal:
            parts.append(f"【总体目标】\n{bb.goal}")

        # 2. 当前计划
        if bb.current_plan:
            plan_text = self._format_plan(bb.current_plan)
            parts.append(f"【当前计划】\n{plan_text}")

        # 3. 最近的风险
        if bb.risks:
            risk_text = self._format_risks(bb.risks)
            parts.append(f"【风险摘要】\n{risk_text}")

        # 4. 委托状态
        if bb.delegations:
            dlg_text = self._format_delegations(bb.delegations)
            parts.append(f"【委托状态】\n{dlg_text}")

        # 5. 专家发现
        if bb.expert_findings:
            findings_text = self._format_findings(bb.expert_findings)
            parts.append(f"【专家发现】\n{findings_text}")

        # 6. 公共记忆上下文（从 dialog entries 中提取，由 inject_to_dialog 写入）
        with bb._lock:
            entries_snapshot = list(bb._dialog_entries)
        memory_entries = [
            e for e in entries_snapshot
            if e.entry_type == "thought"
            and e.tier == "system"
            and e.metadata.get("context_type") == "shared_memory_context"
        ]
        if memory_entries:
            parts.append(memory_entries[-1].content)

        return "\n\n".join(parts)

    def slice_for_supervisor(
        self,
        bb: CognitiveBlackboard,
        delegation_id: Optional[str] = None,
    ) -> str:
        """
        为 Supervisor 模型生成上下文

        包含：
        - 具体的任务描述
        - 背景目标
        - 可用工具
        """
        parts = []

        # 任务描述
        if delegation_id and delegation_id in bb.delegations:
            delegation = bb.delegations[delegation_id]
            parts.append(f"【任务】\n{delegation.task}")
            if delegation.metadata.get("context"):
                parts.append(f"【任务背景】\n{delegation.metadata['context']}")

        # 目标背景
        if bb.goal:
            parts.append(f"【总体目标】\n{bb.goal}")

        # 工具列表
        if bb.runtime_state.get("available_tools"):
            tools = bb.runtime_state["available_tools"]
            tool_text = self._format_tools(tools)
            parts.append(f"【可用工具】\n{tool_text}")

        return "\n\n".join(parts)

    def slice_for_expert(
        self,
        bb: CognitiveBlackboard,
        task_description: str = "",
        cursor: int = 0,
    ) -> str:
        """
        为 Expert 模型生成上下文

        包含：
        - 具体执行步骤
        - 当前工具状态
        - 最近5条执行历史
        """
        parts = []

        # 执行任务
        if task_description:
            parts.append(f"【执行任务】\n{task_description}")
        elif bb.current_plan:
            task_text = self._format_plan(bb.current_plan)
            parts.append(f"【执行步骤】\n{task_text}")

        # 工具状态
        if bb.runtime_state:
            state_text = self._format_runtime_state(bb.runtime_state)
            parts.append(f"【工具状态】\n{state_text}")

        # 执行历史
        recent_obs = bb.get_observations_since(cursor)[-5:] if cursor >= 0 else bb.observations[-5:]
        if recent_obs:
            history_text = self._format_observations(recent_obs)
            parts.append(f"【执行历史】\n{history_text}")

        return "\n\n".join(parts)

    # ── 格式化辅助方法 ──

    def _format_plan(self, plan: List[Dict[str, Any]]) -> str:
        """格式化计划步骤"""
        lines = []
        for i, step in enumerate(plan, 1):
            step_str = str(step.get("description", step))
            lines.append(f"{i}. {step_str}")
        return "\n".join(lines) if lines else "(无计划)"

    def _format_delegations(self, delegations: Dict[str, Any]) -> str:
        """格式化委托状态"""
        lines = []
        for dlg_id, dlg in delegations.items():
            status = dlg.get("status", "?") if isinstance(dlg, dict) else dlg.status
            role = dlg.get("role", "?") if isinstance(dlg, dict) else dlg.role
            task = dlg.get("task", "?") if isinstance(dlg, dict) else dlg.task
            lines.append(f"- [{status}] {role}: {task}")
        return "\n".join(lines) if lines else "(无委托)"

    def _format_risks(self, risks: List[Dict[str, Any]]) -> str:
        """格式化风险"""
        lines = []
        for risk in risks:
            severity = risk.get("severity", "medium")
            description = risk.get("description", "未知风险")
            lines.append(f"- 【{severity}】{description}")
        return "\n".join(lines) if lines else "(无风险)"

    def _format_findings(self, findings: Dict[str, Any]) -> str:
        """格式化专家发现"""
        lines = []
        for finding_id, finding in list(findings.items())[-3:]:  # 只显示最近3条
            role = finding.get("role", "?") if isinstance(finding, dict) else finding.role
            content = finding.get("content", "?") if isinstance(finding, dict) else finding.content
            lines.append(f"- [{role}] {content}")
        return "\n".join(lines) if lines else "(无发现)"

    def _format_tools(self, tools: Dict[str, Any]) -> str:
        """格式化工具列表"""
        lines = []
        for tool_name, tool_info in list(tools.items())[:5]:  # 只显示前5个
            lines.append(f"- {tool_name}")
        return "\n".join(lines) if lines else "(无工具)"

    def _format_runtime_state(self, state: Dict[str, Any]) -> str:
        """格式化运行时状态"""
        lines = []
        for key, value in state.items():
            if key == "available_tools":
                continue  # 单独处理
            lines.append(f"- {key}: {value}")
        return "\n".join(lines) if lines else "(无状态)"

    def _format_observations(self, observations: List[Any]) -> str:
        """格式化观察结果"""
        lines = []
        for obs in observations[-5:]:  # 最近5条
            tier = obs.tier if hasattr(obs, "tier") else obs.get("tier", "?")
            content = obs.content if hasattr(obs, "content") else obs.get("content", "?")
            lines.append(f"- [{tier}] {content}")
        return "\n".join(lines) if lines else "(无历史)"
