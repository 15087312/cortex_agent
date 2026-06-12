"""
上下文控制器 — 所有 prompt 上下文的单一入口和决策者

职责：
1. 接收来自 orchestrator/thinker/runner 的上下文请求
2. 根据当前执行模式（learn/edit/plan/yolo）应用策略
3. 去重：检测已注入的内容，避免重复
4. 压缩：超出阈值时自动压缩

设计意图：
之前上下文注入分散在 8+ 个地方，各自独立判断模式。
orchestrator 注入一份记忆，thinker 又注入一份。
learn 模式下 delegation 是否可用要查 4 处 if-else。

ContextController 是唯一的上下文出口，策略集中管理。
"""
import threading
import hashlib
from typing import Dict, Any, Optional, List, Set
from utils.logger import setup_logger

logger = setup_logger("context_controller")


class ContextController:
    """上下文控制器 — 单例"""

    def __init__(self):
        self._injected_hashes: Set[str] = set()  # 已注入内容的 hash，用于去重
        self._mode = "edit"  # 当前执行模式
        self._lock = threading.Lock()
        logger.info("ContextController 初始化")

    def set_mode(self, mode: str) -> None:
        """设置当前执行模式"""
        if mode not in ("plan", "edit", "yolo", "control", "learn"):
            logger.warning(f"未知模式: {mode}")
            return
        with self._lock:
            self._mode = mode
            # 切换模式时清空去重缓存，因为上下文策略变了
            self._injected_hashes.clear()
        logger.info(f"ContextController 模式: {mode}")

    @property
    def mode(self) -> str:
        return self._mode

    def build_context(self, **sources: str) -> str:
        """从各来源构建最终上下文

        根据当前模式决定哪些来源可用、哪些需要压缩。
        自动去重：相同 hash 的内容不重复注入。

        Args:
            **sources: 上下文来源字典。
                常见 key: memory, perception, importance, expert_guidance, delegation_guide

        Returns:
            合并后的上下文字符串
        """
        from config.settings import settings as _cfg
        mode = self._mode

        parts = []

        # ── 按模式过滤上下文来源 ──
        allowed_keys = self._get_allowed_sources(mode)
        for key in allowed_keys:
            content = sources.get(key, "")
            if not content:
                continue

            # 去重
            content_hash = self._hash_content(content)
            with self._lock:
                if content_hash in self._injected_hashes:
                    logger.debug(f"[上下文] 跳过重复: {key}")
                    continue
                self._injected_hashes.add(content_hash)

            parts.append(content)

        # ── 压缩（超出阈值时）──
        combined = "\n\n".join(parts)

        try:
            from .compression import get_compression_engine
            engine = get_compression_engine()
            max_tokens = _cfg.CONTEXT_WINDOW_SIZE
            compressed = engine.compress(combined, max_tokens=int(max_tokens * 0.6))
            if compressed != combined:
                logger.info(f"[上下文] 已压缩: {len(combined)} → {len(compressed)} 字符")
            return compressed
        except Exception as e:
            logger.debug(f"[上下文] 压缩失败 (非致命): {e}")
            return combined

    def clear(self) -> None:
        """清空去重缓存（新对话开始时调用）"""
        with self._lock:
            self._injected_hashes.clear()

    def _get_allowed_sources(self, mode: str) -> List[str]:
        """根据模式返回允许的上下文来源列表"""
        # 所有模式都允许的基础来源
        base = ["memory", "perception", "importance"]

        if mode == "learn":
            # 学习模式：不需要委托引导和专家上下文
            # 注入 less，模型聚焦 UI 操作
            return base + ["learning_tools"]

        elif mode == "plan":
            # 只读模式：注入记忆和感知，不注入写工具引导
            return base + ["plan_rules"]

        elif mode == "control":
            return base + ["expert_guidance", "delegation_guide"]

        else:  # edit / yolo / default
            return base + ["expert_guidance", "delegation_guide"]

    def _hash_content(self, content: str) -> str:
        """生成内容的 hash 用于去重"""
        if not content:
            return ""
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]


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
