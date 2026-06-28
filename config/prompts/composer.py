"""Prompt 组装器 — 所有 prompt 的唯一出口"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class PromptRequest:
    """一次 prompt 组装请求"""
    tier: str = "large"
    role: str = "orchestrator"
    mode: str = "edit"
    skill_id: Optional[str] = None
    tool_count: int = 0
    conscience_guidance: str = ""   # 良知系统内心独白

    # 动态数据
    task: str = ""
    notebook: str = ""
    history_output: str = ""
    memory: str = ""
    values: str = ""
    blackboard: str = ""
    messages: str = ""
    delegation: str = ""
    perception: str = ""
    guidance: str = ""
    skill_suggestion: str = ""
    round_num: int = 0


@dataclass
class RoleInfo:
    name: str = ""
    tier: str = "expert"
    model_id: str = ""
    personality: str = ""
    speaking_style: str = ""
    expertise: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    tool_whitelist: List[str] = field(default_factory=list)


class PromptComposer:
    """唯一的 prompt 组装入口"""

    def __init__(self):
        from config.prompts.loader import get_loader
        self._loader = get_loader()

    @property
    def _base(self):
        return self._loader.load("base") or {}

    @property
    def _roles(self):
        return self._loader.load("roles") or {}

    def build(self, pool, role: str, tier: str, question: str = "") -> str:
        """从 TurnContext 池构建轮次上下文（不含系统提示词）
        
        系统提示词由 model_runner._build_system_prompt_for_mode() 
        单独注入，避免在 system + user 消息中重复出现。
        """
        from modules.thinking.context.pool import TurnContext
        round_ctx = pool.view(role) if isinstance(pool, TurnContext) else ""
        task_block = f"【当前任务】\n{question}" if question else ""
        parts = [round_ctx, task_block]
        return "\n\n".join(p for p in parts if p)

    def build_system(self, req: PromptRequest) -> str:
        """构建 system prompt"""
        parts = []

        # 良知引导（最顶部）
        if req.conscience_guidance:
            parts.append(req.conscience_guidance)

        role = self._get_role(req.role)
        parts.append(self._build_identity(role))
        parts.extend(self._build_rules(req))
        if req.skill_id:
            parts.append(self._build_skill(req.skill_id))
        parts.append(self._build_capability_table(role, req.tier))
        parts.append(self._build_tool_section(req))
        parts.append(self._build_values_section())

        return "\n\n".join(p for p in parts if p)

    def build_round(self, req: PromptRequest) -> str:
        parts = []
        role = self._get_role(req.role)

        parts.append(self._build_task_header(req, role))
        if req.notebook:
            parts.append(f"【当前任务进度记事本】\n{req.notebook}")
        if req.memory and req.memory != "无近期上下文":
            parts.append(req.memory)
        if req.history_output:
            parts.append(f"【历史输出（不得重复）】\n{req.history_output}")
        tools = self._build_available_tools(req)
        if tools:
            parts.append(f"【可用工具与指令】\n{tools}")
        if req.values:
            parts.append(req.values)
        if req.blackboard:
            parts.append(req.blackboard)
        if req.messages:
            parts.append(req.messages)
        if req.guidance:
            parts.append(req.guidance)
        if req.delegation:
            parts.append(req.delegation)
        if req.skill_suggestion:
            parts.append(req.skill_suggestion)
        parts.append("【请开始工作】\n执行你的任务。需要继续、等待或委托时使用内部控制工具；只有在参数完整且确有必要时才调用普通工具。")
        phase = self._build_phase_hint(req)
        if phase:
            parts.append(phase)

        return "\n\n".join(p for p in parts if p)

    def _get_role(self, role_key: str) -> RoleInfo:
        roles = self._roles.get("roles", {})
        data = roles.get(role_key, roles.get("orchestrator", {}))
        return RoleInfo(
            name=data.get("name", "助手"),
            tier=data.get("tier", "expert"),
            model_id=data.get("model_id", ""),
            personality=data.get("personality", ""),
            speaking_style=data.get("speaking_style", ""),
            expertise=data.get("expertise", []),
            weaknesses=data.get("weaknesses", []),
            tool_whitelist=data.get("tool_whitelist", []),
        )

    def _build_identity(self, role: RoleInfo) -> str:
        tier = self._base.get("tiers", {}).get(role.tier, {})
        identity_text = tier.get("identity", "")
        lines = [f"【人格】{role.personality}", f"【风格】{role.speaking_style}"]
        if role.expertise:
            lines.append(f"【擅长】{'、'.join(role.expertise)}")
        if role.weaknesses:
            lines.append(f"【不擅长】{'、'.join(role.weaknesses)}")
        lines.append("【约束】严格遵守你的角色边界，不要越权操作。")
        if identity_text:
            lines.insert(0, identity_text)
        lines.append(
            "【工具使用】所有工具在用户本地电脑上执行。"
            "只能调用系统已列出的工具，禁止编造、推测或假设存在未列出的工具名。"
            "不确定有哪些工具时，使用 ★ tools_search ★ 列出所有可用工具及其参数。"
            "当用户要求执行操作时，先思考是否有可用工具能完成。"
        )
        return "\n".join(lines)

    def _build_rules(self, req: PromptRequest) -> List[str]:
        result = []
        base = self._base

        safety = base.get("safety", [])
        if safety:
            lines = ["【安全规则 — 强制执行】"]
            for i, r in enumerate(safety, 1):
                lines.append(f"{i}. {r}")
            result.append("\n".join(lines))

        perception = base.get("perception", [])
        if perception:
            lines = ["【被动感知系统】"]
            for r in perception:
                lines.append(r)
            result.append("\n".join(lines))

        mode_constraints = base.get("modes", {}).get(req.mode, [])
        if mode_constraints:
            lines = [f"【执行模式: {req.mode.upper()}】"]
            lines.extend(mode_constraints)
            result.append("\n".join(lines))

        network = base.get("network", [])
        if network:
            lines = ["【网络内容处理规则】"]
            lines.extend(network)
            result.append("\n".join(lines))

        output_rules = base.get("output", {}).get(req.tier, [])
        if output_rules:
            lines = ["【输出规则】"]
            for r in output_rules:
                lines.append(f"- {r}")
            result.append("\n".join(lines))

        exec_reqs = base.get("execution", {}).get(req.tier, [])
        if exec_reqs:
            lines = ["【执行要求】"]
            for i, r in enumerate(exec_reqs, 1):
                lines.append(f"{i}. {r}")
            result.append("\n".join(lines))

        return result

    def _build_skill(self, skill_id: str) -> str:
        try:
            from modules.thinking.skills import skill_manager
            skill = skill_manager.get_skill(skill_id)
            if skill:
                return skill.to_prompt_block()
        except Exception as e:
            logger.warning(f"Skill '{skill_id}' 指令加载失败: {e}")
        return ""

    def _build_capability_table(self, role: RoleInfo, tier: str) -> str:
        if tier == "supervisor":
            return self._build_expert_table()
        elif tier == "large":
            return self._build_supervisor_table()
        return ""

    def _build_expert_table(self) -> str:
        roles = self._roles.get("roles", {})
        experts = [(k, r) for k, r in roles.items() if r.get("tier") == "expert"]
        if not experts:
            return ""
        lines = ["【可委托的专家】", "你可以通过 delegate_task(role=..., task=...) 委托以下专家："]
        for key, r in experts:
            lines.append(f"  {key}: {r.get('expertise', [])}")
        lines.append("选择专家时，根据任务类型匹配最合适的 role。")
        return "\n".join(lines)

    def _build_supervisor_table(self) -> str:
        roles = self._roles.get("roles", {})
        sups = [(k, r) for k, r in roles.items() if r.get("tier") == "supervisor"]
        if not sups:
            return ""
        lines = ["【可委托的主管】", "你可以通过 delegate_task(role=..., task=...) 委托以下主管："]
        for key, r in sups:
            lines.append(f"  {key}: {r.get('expertise', [])}")
        lines.append("根据任务类型选择最合适的主管。")
        return "\n".join(lines)

    def _build_tool_section(self, req: PromptRequest) -> str:
        base = self._base
        rules = base.get("tool_rules", {})
        common = rules.get("common", [])
        tier_rules = rules.get(req.tier, [])
        lines = ["【工具调用规则】"]
        for r in common:
            lines.append(f"- {r}")
        for r in tier_rules:
            lines.append(f"- {r}")
        return "\n".join(lines)

    def _build_values_section(self) -> str:
        base = self._base
        values = base.get("values", {})
        core = values.get("core", [])
        behavior = values.get("behavior", [])
        weights = values.get("weights", {})
        name_cn = values.get("name_cn", {})

        parts = []
        if core:
            lines = ["【核心价值观约束 - 必须遵守】"]
            for i, r in enumerate(core, 1):
                lines.append(f"{i}. {r}")
            parts.append("\n".join(lines))
        if behavior:
            lines = ["【行为准则 - 必须遵循】"]
            for i, r in enumerate(behavior, 1):
                lines.append(f"{i}. {r}")
            parts.append("\n".join(lines))
        if weights:
            lines = ["【价值观权重 - 参考执行】（1.0为最高）"]
            sorted_w = sorted(weights.items(), key=lambda x: x[1], reverse=True)
            for name, w in sorted_w:
                cn = name_cn.get(name, name)
                stars = "★" * int(w * 5)
                lines.append(f"- {cn} ({name}): {stars} {w:.0%}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def _build_task_header(self, req: PromptRequest, role: RoleInfo) -> str:
        lines = [
            f"【你的任务】\n{req.task}",
            f"你是 {role.name}（{role.tier} 层 / {req.role}）。",
        ]
        if role.personality:
            lines.append(f"【角色边界】\n{role.personality}")
            lines.append(f"擅长: {', '.join(role.expertise)}")
            lines.append(f"不擅长: {', '.join(role.weaknesses)}")
        return "\n".join(lines)

    def _build_available_tools(self, req: PromptRequest) -> str:
        if req.tier == "supervisor":
            return (
                "- delegate_task: 委托任务给专家执行。调用后系统会暂停当前思考并等待专家完成，"
                "专家完成后你会被唤醒并收到结果。\n"
                "- continue_thinking: 控制思考节奏。continue=true: 请求获取最新上下文后继续；"
                "continue=false: 最终结束，将 result_summary 返回给上级。\n"
                "三阶段：1.目标分析 → 2.规划与委托 → 3.等待整合"
            )
        elif req.tier == "expert":
            return (
                "- 你调用的每个普通工具，系统会自动把结果追加给你继续执行，无需主动暂停。\n"
                "- continue_thinking(continue=false): 任务完成，输出 result_summary 返回给委托方。\n"
                "不要把控制标记写进自然语言回复。"
            )
        else:
            return (
                "- 【工具执行】: 当调用 web_search / read_file 等普通工具时，系统会自动把结果返回给你继续处理，不需要手动暂停或等待。\n"
                "- 【委托】delegate_task: 委托任务给主管执行。调用后会暂停当前思考进入等待，"
                "主管和专家完成后你会被唤醒，收到结果并看到最新的黑板状态（专家发现、委托进度等）。"
                "寒暄和简单问题不要委托。\n"
                "- 【继续思考】continue_thinking: continue=true 请求刷新全局上下文（你会看到最新的记忆、黑板发现和感知信息），"
                "适合在获得新信息后重建全局视野；continue=false 最终结束任务，将 result_summary 返回给用户。\n"
                "- respond_to_user: 向用户输出最终回复\n"
                "- request_skill: 激活技能说明书（先 list_skills 查看可用技能）\n"
                "- set_memory_focus: 设置记忆检索配比\n"
                "- list_skills: 列出所有可用技能"
            )

    def _build_phase_hint(self, req: PromptRequest) -> str:
        if req.tier != "supervisor":
            return ""
        phases = self._base.get("supervisor_phases", {})
        return phases.get(str(req.round_num), "")

    def reload(self):
        self._loader.reload()
