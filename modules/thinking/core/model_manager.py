"""
模型调度管理器 - 大脑指挥中心

负责大中小模型的层级调用：
- 大模型调用中模型（任务派遣）
- 中模型调用小模型（分布处理）

v2: 集成 ModelIdentity + ModelInstanceFactory，每个模型是独立个体。
    支持按角色隔离的工具白名单和人格配置。

注意：模型调用是内部推理链路，不是外部工具。
"""
from typing import Optional, Dict, Any, List
from infra.model.large_model_client import LargeModelClient
from infra.model.medium_model_client import MediumModelClient
from infra.model.small_model_client import SmallModelClient
from infra.model.lite_model_client import LiteModelClient
from utils.logger import setup_logger


class ModelManager:
    """模型调度中心 - 单例模式（兼容旧接口）+ 工厂模式（新架构）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.big_model: Optional[LargeModelClient] = None
            self.middle_model: Optional[MediumModelClient] = None
            self.small_model: Optional[SmallModelClient] = None
            self.lite_model: Optional[LiteModelClient] = None  # 轻量专家模型
            self.logger = setup_logger("model_manager")
            self.gcm_pool = None  # 全局上下文池（可选注入）
            # v2: 身份映射 — model_id → ModelIdentity
            self._identities: Dict[str, Any] = {}
            self._factory = None  # 延迟创建 ModelInstanceFactory
            self._initialized = True

    # ------------------------------------------------------------------
    # v2: 工厂集成
    # ------------------------------------------------------------------

    @property
    def factory(self):
        """获取模型实例工厂（延迟加载）"""
        if self._factory is None:
            from modules.thinking.model_factory import get_model_factory
            self._factory = get_model_factory()
        return self._factory

    def create_supervisor(self, template_key: str = "supervisor_code",
                          tool_whitelist: List[str] = None, **kwargs):
        """创建主管模型实例（独立个体）

        Args:
            template_key: 身份模板键
            tool_whitelist: 可选的自定义工具白名单（覆盖默认）
            **kwargs: 传递给客户端的额外参数

        Returns:
            ModelInstance
        """
        from modules.thinking.identity import ModelIdentity
        identity = ModelIdentity.from_template(template_key)
        if tool_whitelist:
            identity.tool_whitelist = tool_whitelist
        self._identities[identity.model_id] = identity
        return self.factory.create_supervisor(identity=identity, **kwargs)

    def create_expert(self, template_key: str = "expert_implementer",
                      tool_whitelist: List[str] = None, **kwargs):
        """创建专家模型实例（独立个体）

        Args:
            template_key: 身份模板键
            tool_whitelist: 可选的自定义工具白名单（覆盖默认）
            **kwargs: 传递给客户端的额外参数

        Returns:
            ModelInstance
        """
        from modules.thinking.identity import ModelIdentity
        identity = ModelIdentity.from_template(template_key)
        if tool_whitelist:
            identity.tool_whitelist = tool_whitelist
        self._identities[identity.model_id] = identity
        return self.factory.create_expert(identity=identity, **kwargs)

    def get_model_identity(self, model_id: str):
        """获取模型的身份配置"""
        return self._identities.get(model_id)

    def can_use_tool(self, model_id: str, tool_name: str) -> bool:
        """检查指定模型是否有权使用某工具"""
        identity = self._identities.get(model_id)
        if identity is None:
            # 降级：检查工厂中的实例
            instance = self.factory.get(model_id)
            if instance:
                return instance.can_use_tool(tool_name)
            return False
        whitelist = identity.tool_whitelist
        if "*" in whitelist:
            return True
        return tool_name in whitelist

    async def initialize(self):
        """初始化所有模型客户端"""
        if self.big_model is None:
            try:
                self.big_model = LargeModelClient.from_config()
                self.logger.info("大模型客户端初始化成功")
                # 注册大模型身份
                from modules.thinking.identity import ModelIdentity
                ident = ModelIdentity.from_template("large")
                self._identities[ident.model_id] = ident
            except Exception as e:
                self.logger.error("大模型初始化失败: %s", e)

        if self.middle_model is None:
            try:
                self.middle_model = MediumModelClient.from_config()
                self.logger.info("中模型客户端初始化成功")
            except Exception as e:
                self.logger.error("中模型初始化失败: %s", e)

        if self.small_model is None:
            try:
                self.small_model = SmallModelClient.from_config()
                self.logger.info("小模型客户端初始化成功")
            except Exception as e:
                self.logger.error("小模型初始化失败: %s", e)

        if self.lite_model is None:
            try:
                self.lite_model = LiteModelClient.from_config()
                self.logger.info("轻量模型客户端初始化成功")
            except Exception as e:
                self.logger.error("轻量模型初始化失败: %s", e)
    
    # ======================
    # 通用调用接口（供探针/专家使用）
    # ======================
    def call(self, prompt: str, model_size: str = "lite", **kwargs) -> str:
        """
        同步调用指定模型（供探针使用）
        
        Args:
            prompt: 提示词
            model_size: 模型尺寸 (big/middle/small/lite)
            **kwargs: 额外参数
            
        Returns:
            模型输出文本
        """
        import asyncio
        import concurrent.futures
        
        model_map = {
            "big": self.big_model,
            "middle": self.middle_model,
            "small": self.small_model,
            "lite": self.lite_model,
        }
        
        model = model_map.get(model_size)
        if not model:
            raise RuntimeError(f"模型 [{model_size}] 未初始化")
        
        try:
            # 尝试获取当前事件循环
            try:
                loop = asyncio.get_running_loop()
                has_running_loop = True
            except RuntimeError:
                loop = None
                has_running_loop = False
            
            if has_running_loop:
                # 如果已有事件循环在运行，创建新线程
                def _run_in_thread():
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        return new_loop.run_until_complete(model.generate(prompt, **kwargs))
                    finally:
                        new_loop.close()
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_run_in_thread)
                    return future.result(timeout=30)
            elif loop is not None:
                # 有事件循环但未运行
                return loop.run_until_complete(model.generate(prompt, **kwargs))
            else:
                # 没有事件循环，创建一个新的
                new_loop = asyncio.new_event_loop()
                try:
                    return new_loop.run_until_complete(model.generate(prompt, **kwargs))
                finally:
                    new_loop.close()
        except Exception as e:
            self.logger.error("ModelManager.call 失败 [%s]: %s", model_size, e)
            raise
    
    async def close(self):
        """关闭所有模型资源"""
        if self.big_model:
            await self.big_model.close()
        if self.middle_model:
            await self.middle_model.close()
        if self.small_model:
            await self.small_model.close()
        if self.lite_model:
            await self.lite_model.close()
        self.logger.info("ModelManager 已关闭")


# 全局单例
model_manager = ModelManager()
