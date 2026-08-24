"""
Token 估算器（原 CompressionEngine）

历史说明：本模块曾有 5 级规则压缩（去空行/头尾截断等），已按设计决策移除。
token 控制统一交由 LLM 总结机制处理：
- 工具循环超限：ModelRunner._maybe_summarize_context（上下文超 90% 时调当前模型总结）
- 会话历史裁剪：chat_light/context_slicer.ContextSlicer（旧消息 LLM 总结）
- TurnContext._compact 超限仅告警，不做硬裁剪

现仅保留 token 粗估能力，供各处上下文占用统计与阈值判断使用。
"""
import threading as _threading


class CompressionEngine:
    """
    Token 估算器 — 单例

    类名保留 CompressionEngine 以减少调用方改动；
    实际职责仅为中英文混合文本的 token 数粗略估算。
    """

    # 粗略 token 估算比例
    # 注意：实际比例取决于具体 tokenizer，以下为保守估计（偏低以避免超出窗口）
    # Claude/GPT tokenizer 中文通常 1-2 字符/token，英文约 4 字符/token
    CHARS_PER_TOKEN_CN = 2   # 保守估计：中文 1 token ≈ 2 字符（预留安全边界）
    CHARS_PER_TOKEN_EN = 4   # 英文 1 token ≈ 4 字符

    def estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数"""
        if not text:
            return 0
        # 中文字符比例估计
        cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        en_chars = len(text) - cn_chars
        return cn_chars // self.CHARS_PER_TOKEN_CN + en_chars // self.CHARS_PER_TOKEN_EN


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
