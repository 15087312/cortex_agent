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
from infra.model.small_model_client import SmallModelClient

class ModelManager:
    def __init__(self):
        ...
        self.lite_model: Optional[SmallModelClient] = None

    ...


    
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


# 模块级工厂函数 + 向后兼容
import threading as _threading

_instance = None
_init_lock = _threading.Lock()


def get_model_manager() -> ModelManager:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = ModelManager()
    return _instance


# 向后兼容
model_manager = get_model_manager()
