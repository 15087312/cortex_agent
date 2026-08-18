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
from typing import List, Any, Dict, Tuple
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
        5. 过滤"不可用"工具（未注册 / 被禁用）——防止模型误调用必然失败的工具
        """
        from infra.tool_manager.tool_registry import ToolRegistry

        # 1. 从 identity.py 获取基础白名单
        whitelist = self._get_base_whitelist(tier, role)

        # 2. 展开 tag:
        expanded = self._expand_tags(whitelist)

        # 3. 按 tier 风险过滤（专家不能看 HIGH 工具）
        tier_filtered = self._apply_tier_filter(expanded, tier, ToolRegistry)

        # 4. 技能工具规则（重排/排除 — 当技能激活时作为主要过滤源）
        if skill_tool_rules:
            tier_filtered = self._apply_skill_rules(tier_filtered, skill_tool_rules, ToolRegistry)

        # 5. 过滤"不可用"工具：未注册（白名单写了但实际不存在）或被禁用（管理端运行时开关）——
        #    这类工具模型即使调用也必然失败，直接不显示，避免误调用
        return [t for t in tier_filtered if ToolRegistry.is_tool_available(t)]

    def _get_base_whitelist(self, tier: str, role: str = "") -> List[str]:
        """获取基础白名单

        优先级：持久化角色工具覆盖（personas.yaml role_tools）> YAML identity > DEFAULT_TOOL_WHITELISTS
        """
        from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS

        # 0. 持久化角色工具权限覆盖（编排页可改）：whitelist 非空则整体替换，blacklist 剔除
        try:
            from config.settings import settings
            rt = settings.get_role_tools(role)
            if rt:
                wl = rt.get("whitelist")
                if wl and isinstance(wl, list) and len(wl) > 0:
                    if "*" in wl:
                        # "*" 全部：展开为实际工具名，保证黑名单可剔除
                        from infra.tool_manager.tool_registry import ToolRegistry
                        base = [
                            n for n, info in ToolRegistry._tools.items()
                            if info.source != "security"
                        ]
                    else:
                        base = list(wl)
                else:
                    base = self._identity_whitelist(tier, role, DEFAULT_TOOL_WHITELISTS)
                bl = rt.get("blacklist") or []
                if bl and isinstance(bl, list):
                    base = [t for t in base if t not in bl]
                return base
        except Exception:
            pass

        return self._identity_whitelist(tier, role, DEFAULT_TOOL_WHITELISTS)

    @staticmethod
    def _identity_whitelist(tier: str, role: str, default_whitelists) -> List[str]:
        """从 YAML identity 获取白名单，回退 DEFAULT_TOOL_WHITELISTS"""
        from modules.thinking.identity import get_identities

        try:
            all_ids = get_identities()
            for key, idata in all_ids.items():
                wt = idata.get("tool_whitelist")
                if wt and isinstance(wt, list) and len(wt) > 0:
                    if idata.get("tier") == tier:
                        return list(wt)
        except Exception:
            pass

        if tier == "large":
            return list(default_whitelists.get("large", []))
        elif tier == "supervisor":
            return list(default_whitelists.get("supervisor", []))
        expert_key = f"expert_{role}" if role else ""
        if expert_key in default_whitelists:
            return list(default_whitelists[expert_key])
        if role in default_whitelists:
            return list(default_whitelists[role])
        return []

    def _expand_tags(self, whitelist: List[str]) -> List[str]:
        """展开 tag: 前缀"""
        from infra.tool_manager.tool_registry import ToolRegistry

        result: list = []
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
        # 兼容 dict 与 dataclass：Skill.tool_rules 当前是 raw dict
        if isinstance(rules, dict):
            rules = type("_SkillRules", (), {
                "restrict_to": bool(rules.get("restrict_to")),
                "allow_tools": rules.get("allow_tools") or [],
                "block_tools": rules.get("block_tools") or [],
                "block_tags": rules.get("block_tags") or [],
                "block_categories": rules.get("block_categories") or [],
            })
        prioritized = list(tools)

        # restrict_to: 限制到 allow_tools + 核心系统工具
        if rules.restrict_to and rules.allow_tools:
            # 核心系统工具（所有模式都必须保留）
            core_system = {"tools_search",
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
        if getattr(rules, 'block_categories', None):
            blocked_cats = set(rules.block_categories)
            prioritized = [
                t for t in prioritized
                if not (registry.get_tool(t) and registry.get_tool(t).category in blocked_cats)
            ]
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

            # 回退: 通过 tier 查找同层级的实例（优先匹配相同 role）
            tier = caller_tier
            if caller_role and (caller_role.startswith("expert")
                                 or caller_role.startswith("supervisor")):
                tier = caller_role.split("_")[0]
            else:
                tier = caller_tier if caller_tier in ("large", "supervisor", "expert") else ""

            if tier:
                instances = factory.list_by_tier(tier)
                if caller_role:
                    for inst in instances:
                        if getattr(getattr(inst, "identity", None), "role", "") == caller_role:
                            identity = inst.identity
                            if hasattr(identity, 'permissions'):
                                return identity.permissions
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
            except Exception as yaml_err:
                # 单层 YAML 解析失败不致命（外层还有 template_key 回退），但必须留痕
                logger.debug(f"[权限] YAML 身份权限解析失败: {yaml_err}")

            # 最后回退: 通过 template_key 查找 DEFAULT_PERMISSIONS
            if caller_role:
                permissions = get_permissions(caller_role)
                if permissions.allowed_tool_categories:
                    return permissions

            if caller_tier:
                permissions = get_permissions(caller_tier)
                if permissions.allowed_tool_categories:
                    return permissions

        except Exception as perm_err:
            # fail-closed：权限查询异常时返回"空权限（拒绝全部）"，而不是返回 None。
            # 原 `except: pass` → return None → check_execution_permission 对 None 放行，
            # 即权限系统故障时所有工具绕过类别校验（fail-open）。
            logger.error(f"[权限] 权限查询异常，按空权限（拒绝全部）处理: {perm_err}")
            from modules.thinking.identity import ModelPermissions
            return ModelPermissions(allowed_tool_categories=[])
        return None

    # ── 控制工具可见性 ──────────────────────────────────────────────────

    def get_control_tools(self, tier: str, mode: str,
                          delegation_available: bool) -> List[Dict[str, Any]]:
        """返回该 tier+mode 可用的控制工具名列表

        由激活的 Skill 的 ToolRules 控制 delegate_task 等工具的可见性。
        """
        from modules.thinking.core.control_tools import (
            CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL,
            DELEGATE_TASK_TOOL, STOP_TASK_TOOL,
            CREATE_SUPERVISOR_TOOL,
            RESPOND_TO_USER_TOOL, REQUEST_SKILL_TOOL,
            LIST_SKILLS_TOOL, STOP_SKILL_TOOL,
            REQUEST_MODE_CHANGE_TOOL, ASK_USER_INTENT_TOOL,
            QUERY_DELEGATION_TOOL, RESUME_DELEGATION_TOOL,
            INSPECT_DELEGATION_TOOL, READ_CONTEXT_TOOL,
        )

        tools = [CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL]

        if delegation_available and tier in ("large", "supervisor"):
            tools.append(DELEGATE_TASK_TOOL)
            tools.append(STOP_TASK_TOOL)
            tools.append(QUERY_DELEGATION_TOOL)
            tools.append(RESUME_DELEGATION_TOOL)
            # 深入查看下级具体执行过程（large/supervisor 可查自己下属专家的过程）
            tools.append(INSPECT_DELEGATION_TOOL)
            # 读取黑板记忆/委托上下文（large/supervisor 有记忆与委托链，expert 不直接读）
            tools.append(READ_CONTEXT_TOOL)
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
