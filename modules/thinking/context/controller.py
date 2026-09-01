"""
Token 计算器 — 精确 token 计数（基于 tiktoken）

重写背景：
- 原 ContextController 声称「路由 / 去重 / 压缩」，但实现只有模式追踪与时间上下文；
- token 计数长期依赖 CompressionEngine.estimate_tokens 的字符启发式粗估
  （中文≈2 字/token、英文≈4 字/token），并非真实 tokenizer 结果，
  中英混排与代码/符号场景误差较大。

本模块重写为真正的 token 计算器：
1. 使用 tiktoken 真实编码（cl100k_base / o200k_base …）精确 token 化；
2. 按模型名解析对应编码，未知模型回退 cl100k_base；
3. 不提供字符粗估兜底 —— tiktoken 未安装或编码加载失败时直接抛异常，
   依赖已在 requirements.txt 声明（tiktoken>=0.7.0），必须安装；
4. 保留 set_mode / mode / build_time_context / clear / 单例 等原接口，
   避免破坏 orchestrator 与既有测试的向后兼容。

典型用法：
    cc = get_context_controller()
    n = cc.count_tokens("一段混排 text 与代码", model="gpt-4")
    m = cc.estimate_tokens(system_prompt + user_msg)
    k = cc.count_messages_tokens(messages, model="gpt-4o")
"""
import datetime
import threading
from typing import Any, Dict, List, Optional, Set

from utils.logger import setup_logger

logger = setup_logger("context_controller")


class ContextController:
    """Token 计算器 — 单例

    核心职责：基于 tiktoken 的精确 token 计数（替代字符粗估）。
    附带保留：执行模式追踪、时间上下文构建、去重缓存（向后兼容）。
    """

    # 默认编码：gpt-3.5 / gpt-4 / text-embedding 通用
    DEFAULT_ENCODING = "cl100k_base"

    # 每条消息的固定开销（OpenAI 计费公式：role + format 分隔符）。
    # gpt-3.5-turbo-0301 为 4，其余（gpt-4 / gpt-4o 等）为 3。
    TOKENS_PER_MESSAGE = 3

    def __init__(self):
        self._injected_hashes: Set[str] = set()
        self._mode = "edit"
        self._lock = threading.Lock()
        # 懒加载的 tiktoken 编码缓存：{encoding_name: Encoding}
        self._encodings: Dict[str, Any] = {}
        logger.info("ContextController（token 计算器）初始化")

    # ── token 计数（核心能力） ─────────────────────────────────────────

    def _resolve_encoding(self, model: str = "") -> Any:
        """解析并缓存 tiktoken 编码。model 为空时用默认编码。

        tiktoken 是本地分词库（无需网络/API Key）。未安装或编码加载失败时
        直接抛出异常，不使用任何字符粗估兜底。
        """
        import tiktoken

        if model:
            try:
                return tiktoken.encoding_for_model(model)
            except (KeyError, ValueError):
                model = ""

        name = self.DEFAULT_ENCODING
        enc = self._encodings.get(name)
        if enc is None:
            enc = tiktoken.get_encoding(name)
            self._encodings[name] = enc
        return enc

    def count_tokens(self, text: str, model: str = "") -> int:
        """精确统计文本 token 数（tiktoken，本地分词，无粗估回退）。

        Args:
            text: 待统计文本。
            model: 模型名（如 "gpt-4" / "gpt-4o"），空则用默认 cl100k_base。
                未知模型自动回退默认编码。

        Returns:
            该文本在对应编码下的精确 token 数。

        Raises:
            ImportError: 未安装 tiktoken。
            Exception: 编码加载/分词失败（如未知编码名）。
        """
        if not text:
            return 0
        encoding = self._resolve_encoding(model)
        return len(encoding.encode(text))

    def estimate_tokens(self, text: str) -> int:
        """count_tokens 的便捷别名（与 CompressionEngine.estimate_tokens 同名，
        便于新旧调用方平滑迁移）。默认编码，不区分模型。"""
        return self.count_tokens(text)

    def tokenize(self, text: str, model: str = "") -> List[int]:
        """返回 tokens 的整数 id 列表（供调试/更细粒度分析）。

        基于 tiktoken 真实编码；未安装或失败时直接抛异常，不回退 UTF-8 字节。
        """
        if not text:
            return []
        encoding = self._resolve_encoding(model)
        return encoding.encode(text)

    def count_messages_tokens(self, messages: List[Dict[str, Any]], model: str = "") -> int:
        """统计 OpenAI 风格消息列表（messages）的 token 数，含每消息固定开销。

        公式：sum(每消息 role token + content token + TOKENS_PER_MESSAGE) + 3（回复引导）。
        content 为多模态或 None 时按字符串处理。
        tiktoken 不可用时直接抛异常，不使用字符粗估兜底。
        """
        if not messages:
            return 0
        encoding = self._resolve_encoding(model)

        total = 3  # 回复引导 priming
        for m in messages:
            role = str(m.get("role", "user"))
            content = m.get("content", "") or ""
            total += self.TOKENS_PER_MESSAGE
            total += len(encoding.encode(role))
            total += len(encoding.encode(str(content)))
        return total

    # ── 向后兼容（原 ContextController 接口） ──────────────────────────

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
_instance: Optional[ContextController] = None
_init_lock = threading.Lock()


def get_context_controller() -> ContextController:
    """获取全局 ContextController（token 计算器）实例"""
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = ContextController()
    return _instance