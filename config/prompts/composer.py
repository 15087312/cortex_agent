"""Prompt 组装器 — 所有 prompt 的唯一出口"""
from dataclasses import dataclass, field
from typing import Optional, List


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
        self._system_cache: dict[str, str] = {}

    @property
    def _base(self):
        return self._loader.load("base") or {}

    @property
    def _roles(self):
        return self._loader.load("roles") or {}

    def build(self, pool, role: str, tier: str, question: str = "") -> str:
        """唯一入口：从 TurnContext 池构建完整 prompt"""
        from modules.thinking.context.pool import TurnContext
        system = self._build_cached_system(role, tier)
        round_ctx = pool.view(role) if isinstance(pool, TurnContext) else ""
        task_block = f"【当前任务】\n{question}" if question else ""
        parts = [system, round_ctx, task_block]
        return "\n\n".join(p for p in parts if p)

    def _build_cached_system(self, role: str, tier: str) -> str:
        cache_key = f"{role}:{tier}"
        if cache_key not in self._system_cache:
            req = PromptRequest(tier=tier, role=role)
            self._system_cache[cache_key] = self.build_system(req)
        return self._system_cache[cache_key]

    def invalidate_cache(self):
        self._system_cache.clear()

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
            "可用工具包括 web_search、calc、tools_search 等。"
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
        except Exception:
            pass
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
                "- delegate_task: 向专家委托任务\n"
                "- continue_thinking: 继续/结束思考循环\n"
                "三阶段：1.目标分析 → 2.规划与委托 → 3.等待整合"
            )
        elif req.tier == "expert":
            return (
                "- continue_thinking: 继续/结束思考循环\n"
                "完成工作后使用 continue_thinking(continue=false) 输出 result_summary。"
            )
        else:
            return (
                "- delegate_task: 向主管委托任务。所有需要查询、搜索、文件操作等具身任务都必须通过 delegate_task 委托\n"
                "- continue_thinking: 继续/结束思考循环\n"
                "- respond_to_user: 向用户输出最终回复\n"
                "- request_skill: 激活技能说明书（先 list_skills 查看可用技能，再 get_skill_detail 阅读）\n"
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
