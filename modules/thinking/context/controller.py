"""
上下文控制器 — 上下文路由、去重、压缩

职责：
1. 接收来自 orchestrator/thinker/runner 的上下文请求
2. 根据当前执行模式应用策略
3. 去重：检测已注入的内容，避免重复
4. 压缩：超出阈值时自动压缩

时间上下文格式化保留在此；prompt 组装已委托给 config.prompts.composer。
"""
import threading
import datetime
from typing import Set
from utils.logger import setup_logger

logger = setup_logger("context_controller")


class ContextController:
    """上下文控制器 — 单例"""

    def __init__(self):
        self._injected_hashes: Set[str] = set()
        self._mode = "edit"
        self._lock = threading.Lock()
        logger.info("ContextController 初始化")

    def set_mode(self, mode: str) -> None:
        """设置当前执行模式"""
        if mode not in ("plan", "edit", "yolo", "control"):
            logger.warning(f"未知模式: {mode}")
            return
        with self._lock:
            self._mode = mode
            self._injected_hashes.clear()
        logger.info(f"ContextController 模式: {mode}")

    @property
    def mode(self) -> str:
        return self._mode

    def build_time_context(self, user_name: str = "", last_msg_time: float = 0.0) -> str:
        """构建时间感知块"""
        now = datetime.datetime.now()
        parts = [f"【当前时间】{now.strftime('%Y-%m-%d %H:%M')}"]
        if user_name:
            parts.append(f"【对话对象】{user_name}")
        if last_msg_time > 0:
            elapsed = (datetime.datetime.now().timestamp() - last_msg_time) / 60
            parts.append(f"【上次对话】{user_name}{elapsed:.0f}分钟前说过话")
        return "\n".join(parts)

    def clear(self) -> None:
        """清空去重缓存（新对话开始时调用）"""
        with self._lock:
            self._injected_hashes.clear()


# 模块级单例
_instance = None
_init_lock = threading.Lock()


def get_context_controller() -> ContextController:
    """获取全局 ContextController 实例"""
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = ContextController()
    return _instance
