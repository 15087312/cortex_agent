"""
模型调用基类 - 统一接口定义
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Callable
import aiohttp
import json
import ssl
from datetime import datetime

from utils.logger import setup_logger
logger = setup_logger("model_client")


# ---------------------------------------------------------------------------
# 数据类 — 原生工具调用
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """模型返回的工具调用指令"""
    id: str = ""
    name: str = ""
    arguments: str = "{}"
    type: str = "function"


@dataclass
class ChatMessage:
    """聊天消息 (含工具调用支持)"""
    role: str  # system / user / assistant / tool
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    name: Optional[str] = None        # DashScope: tool role 时标识函数名
    tool_call_id: Optional[str] = None  # OpenAI: tool role 时关联 tool_call
    reasoning_content: Optional[str] = None  # thinking模式下的推理内容


@dataclass
class ChatResponse:
    """聊天响应 — 可能包含文本和/或工具调用"""
    message: ChatMessage
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None


class BaseModelClient(ABC):
    """模型调用基类"""
    
    def __init__(self, api_key: str, api_url: str, timeout: int = 30, allow_empty: bool = False):
        if not allow_empty and not api_key:
            raise ValueError("API key 不能为空")
        if not allow_empty and not api_url:
            raise ValueError("API URL 不能为空")
        
        self.api_key = api_key
        self.api_url = api_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_count = 0
        self._last_request_time: Optional[datetime] = None
        self._total_tokens_used = 0
        self.supports_native_tools: bool = False
    
    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """生成响应（纯文本，无工具支持）

        Args:
            prompt: 输入提示词
            **kwargs: 额外参数（如 max_tokens, temperature 等）

        Returns:
            生成的响应文本

        Raises:
            Exception: API 请求失败或超时时抛出异常
        """
        pass

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        **kwargs,
    ) -> ChatResponse:
        """带原生工具调用的对话生成（可选覆盖）

        默认实现回退到 generate()，仅保留文本响应。
        子类（如 LargeModelClient）应覆盖此方法以支持原生工具调用。

        Args:
            messages: 消息列表（system / user / assistant / tool）
            tools: API 工具描述列表（JSON Schema 格式）
            **kwargs: 额外参数

        Returns:
            ChatResponse 包含文本和/或 tool_calls
        """
        # 默认回退：仅取最后一条 user 消息调用 generate()
        last_content = next(
            (m.content for m in reversed(messages) if m.content),
            "",
        )
        text = await self.generate(last_content, **kwargs)
        return ChatResponse(
            message=ChatMessage(role="assistant", content=text),
        )

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> ChatResponse:
        """流式对话生成 — 每收到一个 token 调用 on_token 回调

        默认实现回退到非流式 chat()，收到完整结果后一次性调用 on_token。
        子类应覆盖此方法以支持真正的 token 级流式输出。

        Args:
            messages: 消息列表
            tools: API 工具描述列表
            on_token: 每个文本 token 到达时的回调 (chunk: str) -> None
            **kwargs: 额外参数

        Returns:
            ChatResponse — 完整响应（含 tool_calls）
        """
        response = await self.chat(messages, tools=tools, **kwargs)
        if on_token and response.message.content:
            on_token(response.message.content)
        return response

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查
        
        Returns:
            服务是否可用
        """
        pass
    
    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        """创建兼容的 SSL 上下文。

        针对 Python 3.13 + OpenSSL 3.6 环境做兼容处理。

        根因分析："SSL record layer failure (_ssl.c:2658)" 是 OpenSSL 3.6 的协议层
        错误，在以下场景触发：
        - ssl.create_default_context() 继承了系统 CACertBundle + 平台默认选项，
          OpenSSL 3.6 在某些 TLS 1.3 会话票据/0-RTT 场景下会产生无法解析的记录
        - 部分 API 网关（如阿里云/Cloudflare）返回的 TLS 记录格式与 OpenSSL 3.6
          的严格解析逻辑不完全兼容

        修复：不依赖 create_default_context() 的平台默认行为，而是显式构造
        SSLContext，只设置必要的证书验证和密码套件，避免 OpenSSL 3.6 的
        实验性特性（0-RTT、early data、session ticket eager processing）干扰。
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

        # 使用 certifi 加载系统 CA 证书（跨平台可靠，覆盖 anaconda/homebrew 等非标准路径）
        try:
            import certifi as _certifi
            ctx.load_verify_locations(cafile=_certifi.where())
        except Exception:
            ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)

        # 安全的密码套件子集（移除 PSK/SRP 等非对称认证套件，避免干扰）
        # 使用 DEFAULT + @SECLEVEL=1 放宽 OpenSSL 3.6 对部分密码套件的限制
        try:
            ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        except ssl.SSLError:
            pass

        return ctx

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            ssl_ctx = self._create_ssl_context()
            # force_close=False（默认）：复用 keep-alive 连接提升性能。
            # 若服务端关闭连接后客户端仍尝试复用，可能引发 SSL 记录层报错。
            # 如果此类错误高频出现，可改为 force_close=True 禁用连接复用，
            # 但需注意这会增加 TCP 握手开销（约 1-RTT）。
            connector = aiohttp.TCPConnector(
                ssl=ssl_ctx,
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                trust_env=True,
            )
        return self._session

    def _reset_session(self):
        """关闭并重置 session，用于 SSL 等连接错误后重建"""
        if self._session and not self._session.closed:
            try:
                # 不 await close：在异常处理中同步关闭，
                # 下次 _get_session 会重新创建
                import asyncio
                asyncio.ensure_future(self._session.close())
            except Exception:
                pass
        self._session = None
    
    async def close(self):
        """关闭连接"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # API 格式检测 + Anthropic 共享辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def detect_api_format(url: str) -> str:
        """从 URL 自动检测 API 格式: dashscope / openai / anthropic"""
        if not url:
            return "openai"
        url_lower = url.lower()
        if "dashscope" in url_lower:
            return "dashscope"
        if "anthropic" in url_lower or "claude" in url_lower:
            return "anthropic"
        if any(k in url_lower for k in ("openai", "v1/chat", "v1/completions")):
            return "openai"
        return "openai"

    def _build_headers(self, api_format: str) -> Dict[str, str]:
        """按 API 格式构建请求头"""
        if api_format == "anthropic":
            return {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _messages_to_anthropic(self, messages: List[ChatMessage]) -> tuple:
        """将 ChatMessage 列表转为 Anthropic 格式。

        Returns:
            (system_text, anthropic_messages) 元组
        """
        system_text = ""
        result = []
        for m in messages:
            if m.role == "system":
                system_text = m.content or ""
                continue
            if m.role == "tool":
                result.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content or "",
                    }],
                })
                continue
            if m.tool_calls:
                blocks = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    try:
                        inp = json.loads(tc.arguments)
                    except Exception as e:
                        logger.warning(f"工具调用参数解析失败: {e}")
                        inp = {}
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": inp})
                result.append({"role": "assistant", "content": blocks})
            else:
                result.append({"role": m.role, "content": m.content or ""})
        return system_text, result

    @staticmethod
    def _parse_anthropic_response(data: Dict) -> ChatResponse:
        """解析 Anthropic API 响应为 ChatResponse"""
        content_blocks = data.get("content", [])
        stop_reason = data.get("stop_reason", "end_turn")
        usage = data.get("usage")

        text_parts = []
        tool_calls = None
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=json.dumps(block.get("input", {}), ensure_ascii=False),
                ))

        finish_reason = "stop"
        if stop_reason == "tool_use":
            finish_reason = "tool_calls"
        elif stop_reason == "max_tokens":
            finish_reason = "length"

        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="\n".join(text_parts) if text_parts else None,
                tool_calls=tool_calls,
            ),
            finish_reason=finish_reason,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0) if usage else 0,
                "completion_tokens": usage.get("output_tokens", 0) if usage else 0,
            } if usage else None,
        )

    def _tools_to_anthropic(self, tools: List[Dict]) -> List[Dict]:
        """将 OpenAI 格式工具列表转为 Anthropic 格式"""
        result = []
        for t in tools:
            func = t.get("function", t)
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", func.get("input_schema", {})),
            })
        return result
    
    def _update_usage_stats(self, tokens_used: int = 0):
        """更新使用统计信息
        
        Args:
            tokens_used: 本次消耗的 token 数
        """
        self._request_count += 1
        self._last_request_time = datetime.now()
        self._total_tokens_used += tokens_used

    def _log_request(self, method: str, url: str, payload_size: int = 0):
        """记录每次模型 API 请求（POST 日志）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        logger.info(
            f"[API REQUEST #{self._request_count + 1}] {now} | "
            f"{method} {url} | payload={payload_size}B"
        )

    def _log_payload(self, payload: dict):
        """记录完整请求体（INFO 级别）

        tools 字段压缩为单行，固定的 system 提示词（人格/规则）省略，
        避免每次请求重复刷屏。
        """
        # 复制一份，不影响原始 payload
        log_data = {k: v for k, v in payload.items() if k != "tools"}
        msgs = log_data.get("messages")
        if isinstance(msgs, list):
            log_data["messages"] = [
                (dict(m, content="<固定提示词，省略>") if (isinstance(m, dict) and m.get("role") == "system") else m)
                for m in msgs
            ]
        tools = payload.get("tools")

        main_part = json.dumps(log_data, ensure_ascii=False, indent=2)

        if tools:
            tools_line = json.dumps(tools, ensure_ascii=False, separators=(',', ':'))
            if len(tools_line) > 200:
                tools_line = tools_line[:200] + f"...] ({len(tools)} tools, {len(json.dumps(tools))}B)"
            logger.info(
                f"[API PAYLOAD #{self._request_count + 1}]\n{main_part}\n"
                f"\"tools\": {tools_line}"
            )
        else:
            logger.info(f"[API PAYLOAD #{self._request_count + 1}]\n{main_part}")

    def _log_response_body(self, status: int, elapsed_ms: float, text: str, tokens: int = 0):
        """记录完整响应体（INFO 级别）"""
        logger.info(f"[API RESPONSE #{self._request_count + 1}] status={status} elapsed={elapsed_ms:.0f}ms tokens={tokens}\n{text}")

    def _log_response(self, status: int, elapsed_ms: float, tokens: int = 0):
        logger.info(
            f"[API RESPONSE #{self._request_count}] "
            f"status={status} elapsed={elapsed_ms:.0f}ms tokens={tokens}"
        )
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """获取使用统计信息
        
        Returns:
            包含请求次数、最后请求时间、token 使用量的字典
        """
        return {
            "request_count": self._request_count,
            "last_request_time": self._last_request_time.isoformat() if self._last_request_time else None,
            "total_tokens_used": self._total_tokens_used
        }
    
    def reset_usage_stats(self):
        """重置使用统计信息"""
        self._request_count = 0
        self._last_request_time = None
        self._total_tokens_used = 0
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
