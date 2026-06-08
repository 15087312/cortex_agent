"""
模型实例工厂 — 创建独立配置的模型实例

每个模型实例 = ModelIdentity + ModelClient + 独立配置
不再使用全局单例模式，每个模型都是独立个体。
"""
import asyncio
import threading as _threading
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

from utils.logger import setup_logger
from config.settings import settings
from .identity import ModelIdentity, ModelTier

logger = setup_logger("model_factory")


@dataclass
class ModelInstance:
    """模型实例 — 一个完整的独立模型个体"""

    identity: ModelIdentity
    client: Any  # LargeModelClient | MediumModelClient | SmallModelClient | LiteModelClient
    created_at: float = 0.0
    status: str = "idle"  # idle | busy | error

    @property
    def model_id(self) -> str:
        return self.identity.model_id

    @property
    def tier(self) -> str:
        return self.identity.tier

    @property
    def tool_whitelist(self) -> list:
        return self.identity.tool_whitelist

    def can_use_tool(self, tool_name: str) -> bool:
        """检查该实例是否有权使用某工具"""
        whitelist = self.tool_whitelist
        if "*" in whitelist:
            return True
        return tool_name in whitelist

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "name": self.identity.name,
            "tier": self.tier,
            "role": self.identity.role,
            "status": self.status,
            "tool_whitelist": self.tool_whitelist,
            "expertise": self.identity.expertise,
        }


class ModelInstanceFactory:
    """模型实例工厂

    职责:
    - 创建独立配置的模型实例 (不再使用全局单例)
    - 管理实例生命周期
    - 按层级限制实例数 (large≤1, supervisor≤5, expert≤10)
    - 实例数限制优先使用 identity.permissions.max_instances
    """

    # 默认上限（当 identity 未提供 permissions 时回退使用）
    DEFAULT_MAX_INSTANCES = {
        "large": 1,
        "supervisor": 5,
        "expert": 10,
    }

    def __init__(self):
        self._instances: Dict[str, ModelInstance] = {}
        self._count_by_tier: Dict[str, int] = {"large": 0, "supervisor": 0, "expert": 0}

    def _get_max_for_identity(self, identity: ModelIdentity) -> int:
        """获取指定身份的最大实例数，优先使用 identity.permissions"""
        if hasattr(identity, 'permissions') and identity.permissions:
            return identity.permissions.max_instances
        return self.DEFAULT_MAX_INSTANCES.get(identity.tier, 1)

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------

    def _ensure_capacity(self, tier: str, max_n: int) -> None:
        """确保 tier 层级的容量充足，若已达上限则自动回收旧实例"""
        if self._count_by_tier[tier] < max_n:
            return
        # 自愈：达到上限时自动回收旧实例
        existing = self.list_by_tier(tier)
        if existing:
            for inst in existing:
                logger.warning(
                    f"[工厂] {tier} 已达上限 ({max_n})，"
                    f"自动回收旧实例: {inst.model_id}"
                )
                self.destroy(inst.model_id)
        else:
            # 理论上不应发生（count>0 但 list 为空），防御性处理
            self._count_by_tier[tier] = 0

    def create_large(self, identity: ModelIdentity = None, **kwargs) -> ModelInstance:
        """创建大模型实例（上限由 identity.permissions.max_instances 控制）"""
        if identity is None:
            identity = ModelIdentity.from_template("large")
        max_n = self._get_max_for_identity(identity)
        self._ensure_capacity("large", max_n)

        from infra.model.large_model_client import LargeModelClient

        # 优先级：kwargs > identity > 全局配置
        api_key = kwargs.get("api_key") or identity.api_key
        api_url = kwargs.get("api_url") or identity.api_url

        if api_key or api_url:
            client = LargeModelClient(
                api_key=api_key,
                api_url=api_url,
                timeout=kwargs.get("timeout", 120),
            )
        else:
            client = LargeModelClient.from_config()
        client.max_tokens = identity.max_tokens or 4096
        client.temperature = identity.temperature or 0.7

        return self._register(identity, client)

    def create_supervisor(self, template_key: str = "supervisor_code",
                          identity: ModelIdentity = None, **kwargs) -> ModelInstance:
        """创建主管模型实例（上限由 identity.permissions.max_instances 控制）"""
        if identity is None:
            identity = ModelIdentity.from_template(template_key)
        max_n = self._get_max_for_identity(identity)
        self._ensure_capacity("supervisor", max_n)

        from infra.model.medium_model_client import MediumModelClient

        # 优先级：kwargs > identity > 全局配置
        api_key = kwargs.get("api_key") or identity.api_key
        api_url = kwargs.get("api_url") or identity.api_url

        if api_key or api_url:
            client = MediumModelClient(
                api_key=api_key,
                api_url=api_url,
                timeout=kwargs.get("timeout", 60),
            )
        else:
            client = MediumModelClient.from_config()
        client.max_tokens = identity.max_tokens or 1024
        client.temperature = identity.temperature or 0.1

        return self._register(identity, client)

    def create_expert(self, template_key: str = "expert_implementer",
                      identity: ModelIdentity = None, **kwargs) -> ModelInstance:
        """创建专家模型实例（云端 7B，上限由 identity.permissions.max_instances 控制）"""
        if identity is None:
            identity = ModelIdentity.from_template(template_key)
        max_n = self._get_max_for_identity(identity)
        self._ensure_capacity("expert", max_n)

        from infra.model.small_model_client import SmallModelClient

        # 优先级：kwargs > identity > 全局配置
        api_key = kwargs.get("api_key") or identity.api_key
        api_url = kwargs.get("api_url") or identity.api_url

        if api_key or api_url:
            client = SmallModelClient(
                model_name=identity.model_name or settings.SMALL_MODEL_NAME,
                max_tokens=identity.max_tokens or 512,
                temperature=identity.temperature or 0.3,
                api_key=api_key or settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
                api_url=api_url or settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
            )
        else:
            client = SmallModelClient.from_config()
            client.model_name = identity.model_name or client.model_name
            client.max_tokens = identity.max_tokens or client.max_tokens
            client.temperature = identity.temperature or client.temperature

        return self._register(identity, client)

    def create_lite(self, template_key: str = "expert_analyzer",
                    identity: ModelIdentity = None, **kwargs) -> ModelInstance:
        """创建轻量专家实例（用于快速API调用）"""
        if identity is None:
            identity = ModelIdentity.from_template(template_key)

        from infra.model.lite_model_client import LiteModelClient

        client = LiteModelClient(
            model_name=kwargs.get("model_name") or settings.SMALL_MODEL_NAME,
            max_tokens=identity.max_tokens or 64,
            temperature=identity.temperature or 0.1,
            api_key=kwargs.get("api_key") or settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
            api_url=kwargs.get("api_url") or settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
        )

        return self._register(identity, client)

    # ------------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------------

    def _register(self, identity: ModelIdentity, client: Any) -> ModelInstance:
        import time
        instance = ModelInstance(
            identity=identity,
            client=client,
            created_at=time.time(),
            status="idle",
        )
        self._instances[identity.model_id] = instance
        self._count_by_tier[identity.tier] += 1
        logger.info(
            f"[工厂] 创建实例: {identity.name} ({identity.tier}/{identity.role}) "
            f"id={identity.model_id} "
            f"tier_count={self._count_by_tier[identity.tier]}"
        )
        return instance

    def destroy(self, model_id: str) -> bool:
        """销毁模型实例"""
        instance = self._instances.pop(model_id, None)
        if instance is None:
            return False
        self._count_by_tier[instance.tier] -= 1
        logger.info(f"[工厂] 销毁实例: {instance.identity.name} ({model_id})")
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, model_id: str) -> Optional[ModelInstance]:
        """获取指定实例"""
        return self._instances.get(model_id)

    def list_by_tier(self, tier: str) -> list:
        """列出指定层级的所有实例"""
        return [i for i in self._instances.values() if i.tier == tier]

    def list_all(self) -> list:
        return list(self._instances.values())

    def get_large(self) -> Optional[ModelInstance]:
        """获取大模型实例（只有一个）"""
        large_instances = self.list_by_tier("large")
        return large_instances[0] if large_instances else None

    def get_supervisors(self) -> list:
        return self.list_by_tier("supervisor")

    def get_experts(self) -> list:
        return self.list_by_tier("expert")

    def get_status(self) -> dict:
        return {
            "total_instances": len(self._instances),
            "by_tier": dict(self._count_by_tier),
            "instances": [i.to_dict() for i in self._instances.values()],
        }

    async def close_all(self):
        """关闭所有实例"""
        for instance in self._instances.values():
            try:
                if hasattr(instance.client, 'close'):
                    result = instance.client.close()
                    if hasattr(result, '__await__'):
                        await result
            except Exception as e:
                logger.warning(f"关闭实例 {instance.model_id} 失败: {e}")
        self._instances.clear()
        self._count_by_tier = {"large": 0, "supervisor": 0, "expert": 0}


# ---------------------------------------------------------------------------
# 全局工厂单例
# ---------------------------------------------------------------------------

_factory: Optional[ModelInstanceFactory] = None
_factory_lock = _threading.Lock()


def get_model_factory() -> ModelInstanceFactory:
    """获取全局模型实例工厂"""
    global _factory
    if _factory is None:
        with _factory_lock:
            if _factory is None:
                _factory = ModelInstanceFactory()
    return _factory
