"""
Token 计数器（原 CompressionEngine，现统一委托 ContextController）

历史说明：本模块曾有 5 级规则压缩（去空行/头尾截断等），已按设计决策移除。
token 控制统一交由 LLM 总结机制处理：
- 工具循环超限：ModelRunner._maybe_summarize_context（上下文超 90% 时调当前模型总结）
- 会话历史裁剪：chat_light/context_slicer.ContextSlicer（旧消息 LLM 总结）
- TurnContext._compact 超限仅告警，不做硬裁剪

现统一为 token 计数（非粗估）：
- 真实计数由 modules.thinking.context.controller.ContextController 承担，
  内部使用 tiktoken（cl100k_base / o200k_base …）精确编码；
  tiktoken 未安装 / 编码加载失败时直接抛异常，不做字符粗估兜底。
- 本模块仅保留 CompressionEngine 类名与 get_compression_engine() 工厂，
  全部委托给 ContextController，避免两套口径不一致。
"""
import threading as _threading


class CompressionEngine:
    """Token 计数器 — 单例（兼容旧调用方，实际委托 ContextController）。"""

    def __init__(self):
        # 延迟获取，避免 import 期循环依赖（controller 不依赖本模块）
        from modules.thinking.context.controller import get_context_controller
        self._ctrl = get_context_controller()

    def estimate_tokens(self, text: str) -> int:
        """精确 token 计数（tiktoken，无粗估回退）。等价于 count_tokens(text)。"""
        return self._ctrl.count_tokens(text)

    # 透传 ContextController 的精确能力，供需要区分模型的调用方使用
    def count_tokens(self, text: str, model: str = "") -> int:
        return self._ctrl.count_tokens(text, model=model)

    def count_messages_tokens(self, messages, model: str = "") -> int:
        return self._ctrl.count_messages_tokens(messages, model=model)


# 模块级工厂函数
_instance = None
_init_lock = _threading.Lock()


def get_compression_engine() -> CompressionEngine:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = CompressionEngine()
    return _instance
