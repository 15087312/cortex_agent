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

  # 可执行性：由 tool_security_gate.py 统一处理（参数级危险检测 + 风险等级审批）
  # 不再在此类中重复实现
"""
from typing import Dict, List, Any, Optional, Tuple
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
                          skill_tool_rules: Any = None) -> List[str]:
        """返回模型可见的工具列表

        权限决定策略：
        1. 从 identity.py 获取基础白名单
        2. 展开 tag: 前缀
        3. 按 tier 风险过滤（专家不能看 HIGH 工具）
        4. 技能工具规则（激活的 Skill ToolRules 重排/排除）
        """
        from infra.tool_manager.tool_registry import ToolRegistry

        # 1. 从 identity.py 获取基础白名单
        whitelist = self._get_base_whitelist(tier)

        # 2. 展开 tag:
        expanded = self._expand_tags(whitelist)

        # 3. 按 tier 风险过滤（专家不能看 HIGH 工具）
        tier_filtered = self._apply_tier_filter(expanded, tier, ToolRegistry)

        # 4. 技能工具规则（重排/排除 — 当技能激活时作为主要过滤源）
        if skill_tool_rules:
            tier_filtered = self._apply_skill_rules(tier_filtered, skill_tool_rules, ToolRegistry)

        return tier_filtered

    def _get_base_whitelist(self, tier: str, role: str = "") -> List[str]:
        """获取基础白名单

        优先从 YAML 配置的 identity.tool_whitelist 读取，
        再回退到 DEFAULT_TOOL_WHITELISTS。
        """
        from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS

        # 尝试从外部 YAML 配置获取
        try:
            from modules.thinking.identity import get_identities
            all_ids = get_identities()
            for key, idata in all_ids.items():
                wt = idata.get("tool_whitelist")
                if wt and isinstance(wt, list) and len(wt) > 0:
                    if idata.get("tier") == tier:
                        return list(wt)
        except Exception:
            pass

        # 回退：硬编码默认
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

    # ── 执行权限检查（角色类别权限）────────────────────────────────────

    def check_execution_permission(self, tool_name: str, caller_tier: str,
                                    caller_model_id: str = "",
                                    caller_role: str = "") -> Tuple[bool, str]:
        """检查调用者是否有权限执行指定工具

        基于 ModelPermissions.allowed_tool_categories 判断：
        - large: 允许 query/mutation/admin
        - supervisor: 允许 query/mutation
        - expert: 通常只允许 query

        Args:
            tool_name: 工具名
            caller_tier: 调用者层级 (large/supervisor/expert)
            caller_model_id: 调用者 model_id（用于精确查找）
            caller_role: 调用者角色（回退查找用）

        Returns:
            (allowed, reason)
        """
        from infra.tool_manager.tool_registry import ToolRegistry

        tool_info = ToolRegistry.get_tool(tool_name)
        if not tool_info:
            return True, ""  # 控制工具（delegate_task 等）不在 registry 中，默认允许

        permissions = self._get_caller_permissions(caller_model_id, caller_tier, caller_role)
        if permissions is not None:
            if not permissions.can_use_tool_category(tool_info.category):
                return False, (
                    f"当前模型无权调用 {tool_info.category} 类别工具: {tool_name}。"
                    f"允许的类别: {permissions.allowed_tool_categories}"
                )

        return True, ""

    @staticmethod
    def _get_caller_permissions(caller_model_id: str, caller_tier: str,
                                 caller_role: str = ""):
        """获取调用者的 ModelPermissions

        从旧 tool_manager._get_caller_permissions 迁移而来。
        查找顺序: model_id 精确查找 → tier 回退查找 → template_key 回退。
        """
        try:
            from modules.thinking.model_factory import get_model_factory
            from modules.thinking.identity import get_permissions

            factory = get_model_factory()

            # 优先通过 model_id 精确查找
            if caller_model_id:
                instance = factory.get(caller_model_id)
                if instance and hasattr(instance.identity, 'permissions'):
                    return instance.identity.permissions

            # 回退: 通过 tier 查找同层级的任意实例
            tier = caller_tier
            if caller_role and (caller_role.startswith("expert")
                                 or caller_role.startswith("supervisor")):
                tier = caller_role.split("_")[0]
            else:
                tier = caller_tier if caller_tier in ("large", "supervisor", "expert") else ""

            if tier:
                instances = factory.list_by_tier(tier)
                if instances:
                    identity = instances[0].identity
                    if hasattr(identity, 'permissions'):
                        return identity.permissions

            # 尝试从 YAML 配置的 identity.permissions 读取
            try:
                from modules.thinking.identity import get_identities
                from modules.thinking.identity import ModelPermissions
                all_ids = get_identities()
                for key, idata in all_ids.items():
                    perm_dict = idata.get("permissions")
                    if perm_dict and isinstance(perm_dict, dict):
                        cats = perm_dict.get("allowed_tool_categories", [])
                        if cats and isinstance(cats, list):
                            # 按 tier/key 匹配
                            if caller_role and (key == caller_role or key.endswith(f"_{caller_role}")):
                                return ModelPermissions(allowed_tool_categories=cats)
                            if caller_tier and idata.get("tier") == caller_tier:
                                return ModelPermissions(allowed_tool_categories=cats)
            except Exception:
                pass

            # 最后回退: 通过 template_key 查找 DEFAULT_PERMISSIONS
            if caller_role:
                permissions = get_permissions(caller_role)
                if permissions.allowed_tool_categories:
                    return permissions

            if caller_tier:
                permissions = get_permissions(caller_tier)
                if permissions.allowed_tool_categories:
                    return permissions

        except Exception:
            pass
        return None

    # ── 控制工具可见性 ──────────────────────────────────────────────────

    def get_control_tools(self, tier: str, mode: str,
                          delegation_available: bool) -> List[str]:
        """返回该 tier+mode 可用的控制工具名列表

        由激活的 Skill 的 ToolRules 控制 delegate_task 等工具的可见性。
        """
        from modules.thinking.core.control_tools import (
            CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL,
            DELEGATE_TASK_TOOL, CREATE_SUPERVISOR_TOOL,
            RESPOND_TO_USER_TOOL, REQUEST_SKILL_TOOL,
            LIST_SKILLS_TOOL, STOP_SKILL_TOOL,
            REQUEST_MODE_CHANGE_TOOL, ASK_USER_INTENT_TOOL,
        )

        tools = [CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL]

        if delegation_available and tier in ("large", "supervisor"):
            tools.append(DELEGATE_TASK_TOOL)
        if delegation_available and tier == "large":
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
