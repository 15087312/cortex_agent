"""
探针权限管理器 — 三级层级权限链

规则:
- large(3) > supervisor(2) > expert(1)
- large 可以启停 supervisor 和 expert 的探针
- supervisor 可以启停 expert 的探针
- expert 不能启停任何探针
- large 可以修改 supervisor 的记忆和人格
- supervisor 可以向 expert 探针注入上下文

ModelPermissions 集成:
- 当调用者提供了 ModelPermissions 对象时，优先使用其细粒度权限字段
- 没有 ModelPermissions 时，回退到硬编码的 TIER_HIERARCHY / CONTROL_MAP
"""
from typing import Optional, TYPE_CHECKING
from utils.logger import setup_logger

if TYPE_CHECKING:
    from modules.thinking.identity import ModelPermissions

logger = setup_logger("probe_permission")


class ProbePermissionManager:
    """三级模型层级权限管理器

    用法:
        ppm = ProbePermissionManager()
        if ppm.can_control("large", "supervisor"):
            ...  # 大模型可以控制主管
    """

    TIER_HIERARCHY = {
        "large": 3,
        "supervisor": 2,
        "expert": 1,
    }

    # 每个 tier 可以控制的目标 tier
    CONTROL_MAP = {
        "large": ["supervisor", "expert"],
        "supervisor": ["expert"],
        "expert": [],
    }

    # 每个 tier 可以修改记忆/人格的目标 tier
    MEMORY_CONTROL_MAP = {
        "large": ["supervisor", "expert"],
        "supervisor": ["expert"],
        "expert": [],
    }

    def _resolve_tier(self, tier: str) -> str:
        """规范化 tier 名称"""
        tier = (tier or "").lower().strip()
        if tier not in self.TIER_HIERARCHY:
            return ""
        return tier

    def get_tier_level(self, tier: str) -> int:
        """获取 tier 的数值等级"""
        return self.TIER_HIERARCHY.get(self._resolve_tier(tier), 0)

    def can_control(self, caller_tier: str, target_tier: str) -> bool:
        """检查 caller 是否可以启停 target 的探针

        Args:
            caller_tier: 调用者层级 (large/supervisor/expert)
            target_tier: 目标层级 (large/supervisor/expert)

        Returns:
            True 如果 caller 可以控制 target
        """
        caller = self._resolve_tier(caller_tier)
        target = self._resolve_tier(target_tier)

        if not caller:
            logger.warning(f"未知的 caller tier: {caller_tier}")
            return False
        if not target:
            logger.warning(f"未知的 target tier: {target_tier}")
            return False

        allowed = target in self.CONTROL_MAP.get(caller, [])
        if not allowed:
            logger.warning(
                f"[探针权限] 拒绝: {caller} 不能控制 {target} 的探针"
            )
        return allowed

    def can_modify_memory(self, caller_tier: str, target_tier: str) -> bool:
        """检查 caller 是否可以修改 target 的记忆/人格

        Args:
            caller_tier: 调用者层级
            target_tier: 目标层级

        Returns:
            True 如果 caller 可以修改 target 的记忆
        """
        caller = self._resolve_tier(caller_tier)
        target = self._resolve_tier(target_tier)

        if not caller or not target:
            return False

        allowed = target in self.MEMORY_CONTROL_MAP.get(caller, [])
        if not allowed:
            logger.warning(
                f"[探针权限] 拒绝: {caller} 不能修改 {target} 的记忆"
            )
        return allowed

    def validate_probe_start(
        self, caller_tier: str, target_tier: str, identity_key: str
    ) -> Optional[str]:
        """完整校验 probe_start 权限，返回 None 表示通过，否则返回错误信息

        Args:
            caller_tier: 调用者层级
            target_tier: 目标层级
            identity_key: 身份模板键

        Returns:
            None 表示通过，字符串表示错误原因
        """
        if not self.can_control(caller_tier, target_tier):
            return (
                f"权限不足: {caller_tier} 层级不能启动 {target_tier} 层级的探针。"
                f"只有 {'/'.join(self.CONTROL_MAP.get(caller_tier, []))} 可以被 {caller_tier} 启动"
            )

        # 验证 identity_key 存在
        try:
            from modules.thinking.identity import get_identities
            if identity_key not in get_identities():
                return f"未知的身份模板: {identity_key}，可用: {list(get_identities().keys())}"
        except ImportError:
            pass

        return None

    def validate_probe_start_with_permissions(
        self,
        caller_permissions: "ModelPermissions",
        target_tier: str,
        identity_key: str,
        caller_tier: str = "",
    ) -> Optional[str]:
        """使用 ModelPermissions 校验 probe_start 权限

        优先使用 ModelPermissions 的细粒度字段（can_start_probes,
        controllable_tiers），没有 ModelPermissions 时回退到硬编码 map。

        Args:
            caller_permissions: 调用者的 ModelPermissions 对象
            target_tier: 目标层级
            identity_key: 身份模板键
            caller_tier: 调用者层级（回退用）

        Returns:
            None 表示通过，字符串表示错误原因
        """
        # 1. 检查 can_start_probes 标志
        if caller_permissions is not None and not caller_permissions.can_start_probes:
            return (
                f"权限不足: 当前模型无权启动探针。"
                f"(can_start_probes=False)"
            )

        # 2. 检查 controllable_tiers
        if caller_permissions is not None:
            if not caller_permissions.can_control_tier(target_tier):
                return (
                    f"权限不足: 当前模型不能控制 {target_tier} 层级的探针。"
                    f"可控制: {caller_permissions.controllable_tiers}"
                )

        # 3. 回退到硬编码检查（当 ModelPermissions 未提供或为默认空值）
        if caller_permissions is None or not caller_permissions.controllable_tiers:
            if not self.can_control(caller_tier, target_tier):
                return (
                    f"权限不足: {caller_tier} 层级不能启动 {target_tier} 层级的探针。"
                    f"只有 {'/'.join(self.CONTROL_MAP.get(caller_tier, []))} 可以被 {caller_tier} 启动"
                )

        # 4. 验证 identity_key 存在
        try:
            from modules.thinking.identity import get_identities
            if identity_key not in get_identities():
                return f"未知的身份模板: {identity_key}，可用: {list(get_identities().keys())}"
        except ImportError:
            pass

        return None

    def validate_probe_stop_with_permissions(
        self,
        caller_permissions: "ModelPermissions",
        target_tier: str,
        caller_tier: str = "",
    ) -> Optional[str]:
        """使用 ModelPermissions 校验 probe_stop 权限"""
        if caller_permissions is not None and not caller_permissions.can_stop_probes:
            return "权限不足: 当前模型无权停止探针。(can_stop_probes=False)"

        if caller_permissions is not None:
            if not caller_permissions.can_control_tier(target_tier):
                return (
                    f"权限不足: 当前模型不能控制 {target_tier} 层级的探针。"
                    f"可控制: {caller_permissions.controllable_tiers}"
                )

        if caller_permissions is None or not caller_permissions.controllable_tiers:
            if not self.can_control(caller_tier, target_tier):
                return (
                    f"权限不足: {caller_tier} 层级不能停止 {target_tier} 层级的探针。"
                )

        return None

    def can_modify_memory_with_permissions(
        self,
        caller_permissions: "ModelPermissions",
        target_tier: str,
        caller_tier: str = "",
    ) -> bool:
        """使用 ModelPermissions 校验记忆修改权限"""
        if caller_permissions is not None:
            if not caller_permissions.can_write_memory:
                return False
            if caller_permissions.controllable_tiers:
                return target_tier in caller_permissions.controllable_tiers

        # 回退
        return self.can_modify_memory(caller_tier, target_tier)


# 全局单例
_permission_manager: Optional[ProbePermissionManager] = None


def get_probe_permission_manager() -> ProbePermissionManager:
    """获取全局探针权限管理器"""
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = ProbePermissionManager()
    return _permission_manager
