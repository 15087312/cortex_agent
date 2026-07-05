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

    def reload(self):
        self._loader.reload()
