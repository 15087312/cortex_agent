"""
工具权限控制器 — 统一管理工具可见性和执行权限

设计意图：
  之前工具权限分散在 3 个独立系统：
  - identity.py: 白名单（可见性）
  - tool_security_gate.py: 执行门控（风险+模式）
  - tool_manager.py: 角色匹配（category 权限）
  三者独立判断、互不知情，导致可见/可执行不一致。

  ToolPermissionController 是工具权限的唯一出口，
  所有权限策略集中管理，不再分散 if-else。

用法：
  ctrl = get_tool_permission_controller()
  
  # 可见性：模型能看到哪些工具
  visible = ctrl.get_visible_tools(tier="large", mode="edit")
  
  # 可执行性：某个工具能否执行
  allowed, reason = ctrl.check_executable(
      tier="large", role="large_primary",
      tool_name="exec_command", tool_params={...},
      mode="learn"
  )
"""
from typing import Dict, List, Any, Tuple, Optional, Set
from utils.logger import setup_logger
import threading

logger = setup_logger("tool_permission_controller")


class ToolPermissionController:
    """工具权限控制器 — 单例"""

    def __init__(self):
        self._lock = threading.Lock()
        logger.info("ToolPermissionController 初始化")

    # ── 可见性 ──────────────────────────────────────────────────────────

    def get_visible_tools(self, tier: str, mode: str, role: str = "",
                          skill_tool_rules: Any = None,
                          companion_mode: bool = False) -> List[str]:
        """返回模型可见的工具列表

        按模式+tier 组合策略过滤：
        - learn: 只显示 query + 学习相关工具
        - plan: 只显示 query 工具
        - control/edit/yolo: 全量（按 tier 裁剪）
        """
        from infra.tool_manager.tool_registry import ToolRegistry

        # 1. 从 identity.py 获取基础白名单
        whitelist = self._get_base_whitelist(tier, companion_mode)

        # 2. 展开 tag:
        expanded = self._expand_tags(whitelist)

        # 3. 按模式过滤
        mode_filtered = self._apply_mode_filter(expanded, mode, tier, ToolRegistry)

        # 4. 按 tier 风险过滤（专家不能看 HIGH 工具）
        tier_filtered = self._apply_tier_filter(mode_filtered, tier, ToolRegistry)

        # 5. 技能工具规则（重排/排除）
        if skill_tool_rules:
            tier_filtered = self._apply_skill_rules(tier_filtered, skill_tool_rules, ToolRegistry)

        return tier_filtered

    def _get_base_whitelist(self, tier: str, companion: bool) -> List[str]:
        """获取基础白名单"""
        from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS

        if companion and tier == "large":
            return list(DEFAULT_TOOL_WHITELISTS.get("companion", []))

        if tier == "large":
            return list(DEFAULT_TOOL_WHITELISTS.get("large", []))
        elif tier == "supervisor":
            return list(DEFAULT_TOOL_WHITELISTS.get("supervisor", []))
        # expert: 根据 role 查找
        expert_key = f"expert_{role}" if role else ""
        if expert_key in DEFAULT_TOOL_WHITELISTS:
            return list(DEFAULT_TOOL_WHITELISTS[expert_key])
        return []

    def _expand_tags(self, whitelist: List[str]) -> List[str]:
        """展开 tag: 前缀"""
        from infra.tool_manager.tool_registry import ToolRegistry

        result = []
        for item in whitelist:
            if item.startswith("tag:"):
                tag = item[4:]
                result.extend(ToolRegistry.get_tools_by_tag(tag))
            else:
                result.append(item)
        return result

    def _apply_mode_filter(self, tools: List[str], mode: str,
                           tier: str, registry) -> List[str]:
        """按模式过滤工具"""
        if mode == "learn":
            # 学习模式：只保留 query 类 + 学习相关工具
            return [
                t for t in tools
                if registry.get_tool(t) and (
                    registry.get_tool(t).category == "query"
                    or "learned" in registry.get_tool(t).tags
                    or "toolbuilder" in registry.get_tool(t).tags
                )
            ]
        elif mode == "plan":
            # plan 模式：只保留 query 类
            return [
                t for t in tools
                if registry.get_tool(t) and registry.get_tool(t).category == "query"
            ]
        return tools  # edit/yolo/control: 不额外过滤

    def _apply_tier_filter(self, tools: List[str], tier: str,
                           registry) -> List[str]:
        """按 tier 过滤（专家不能看 HIGH 风险工具）"""
        if tier != "expert":
            return tools
        return [
            t for t in tools
            if registry.get_tool(t) and registry.get_tool(t).risk_level not in ("HIGH", "CRITICAL")
        ]

    def _apply_skill_rules(self, tools: List[str], rules, registry) -> List[str]:
        """应用技能工具规则（重排 + 排除 + 限制模式）"""
        prioritized = list(tools)

        # restrict_to: 限制到 allow_tools + 核心系统工具
        if rules.restrict_to and rules.allow_tools:
            # 核心系统工具（所有模式都必须保留）
            core_system = {"read_file", "search_files", "list_my_tools",
                           "tools_search", "query_tool_details",
                           "calc", "memory_match", "todo"}
            restricted = set(rules.allow_tools) | core_system
            prioritized = [t for t in prioritized if t in restricted]

        if rules.allow_tools and not rules.restrict_to:
            # 非限制模式：只重排，不删除
            skill_tools = [t for t in tools if t in rules.allow_tools]
            other = [t for t in tools if t not in rules.allow_tools]
            prioritized = skill_tools + other
        if rules.block_tools:
            prioritized = [t for t in prioritized if t not in rules.block_tools]
        if rules.block_tags:
            blocked = set()
            for name, info in registry._tools.items():
                if any(tag in info.tags for tag in rules.block_tags):
                    blocked.add(name)
            prioritized = [t for t in prioritized if t not in blocked]
        return prioritized

    # ── 可执行性 ─────────────────────────────────────────────────────────

    def check_executable(
        self, tier: str, role: str,
        tool_name: str, tool_params: Dict[str, Any],
        mode: str,
    ) -> Tuple[bool, str]:
        """检查工具是否可以执行

        综合判断：
        - 模式策略（learn 自动放行）
        - 风险等级（HIGH 需要审批）
        - 角色匹配（专家不能调 admin 工具）
        - 极端危险（rm -rf / 等硬阻断）
        """
        from infra.tool_manager.tool_registry import ToolRegistry

        info = ToolRegistry.get_tool(tool_name)
        if not info:
            # 控制工具（delegate_task 等）不在 registry 中，默认允许
            return True, ""

        # 1. 极端危险阻断
        if tool_name in self._get_extreme_danger_tools():
            return False, "极端危险操作被永久禁止"

        # 2. 学习模式自动放行
        if mode == "learn":
            return True, "学习模式自动批准"

        # 3. plan 模式拦截写操作
        if mode == "plan" and info.category in ("mutation", "admin"):
            return False, "plan 模式禁止写操作"

        # 4. 角色匹配：专家不能调 admin 工具
        if tier == "expert" and info.category == "admin":
            return False, f"当前角色无权调用 {info.category} 类别工具"

        # 5. 高风险工具标记为待审批
        if info.risk_level == "HIGH":
            return True, "需要审批"  # 调用方自行走审批流程

        return True, ""

    def _get_extreme_danger_tools(self) -> Set[str]:
        """极端危险工具名列表"""
        return {"exec_command"}  # 实际危险判断在安全门控的参数级别

    # ── 控制工具可见性 ──────────────────────────────────────────────────

    def get_control_tools(self, tier: str, mode: str,
                          delegation_available: bool) -> List[str]:
        """返回该 tier+mode 可用的控制工具名列表"""
        from modules.thinking.core.control_tools import (
            CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL,
            DELEGATE_TASK_TOOL, CREATE_SUPERVISOR_TOOL,
            RESPOND_TO_USER_TOOL, REQUEST_SKILL_TOOL,
            LIST_SKILLS_TOOL, STOP_SKILL_TOOL,
            REQUEST_MODE_CHANGE_TOOL, ASK_USER_INTENT_TOOL,
        )

        tools = [CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL]

        if delegation_available and tier in ("large", "supervisor"):
            if mode != "learn":
                tools.append(DELEGATE_TASK_TOOL)
        if delegation_available and tier == "large":
            if mode != "learn":
                tools.append(CREATE_SUPERVISOR_TOOL)
        if tier == "large":
            tools.extend([
                RESPOND_TO_USER_TOOL, REQUEST_SKILL_TOOL,
                LIST_SKILLS_TOOL, STOP_SKILL_TOOL,
                REQUEST_MODE_CHANGE_TOOL, ASK_USER_INTENT_TOOL,
            ])
        return tools


# 模块级单例
_instance = None
_init_lock = threading.Lock()


def get_tool_permission_controller() -> ToolPermissionController:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = ToolPermissionController()
    return _instance
