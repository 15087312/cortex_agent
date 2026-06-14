"""
主动搭话 — 差异检测器触发的单次大模型调用

当系统检测到显著差异（intensity ≥ 50，如空闲阈值分钟+）时，
触发一次单次大模型调用，主动询问用户是否需要帮助。

工作模式: 探测器身份，专业报告差异
陪伴模式: 朋友身份，模仿之前的说话风格搭话

不走完整编排链路（无 ContinuousThinker / ModelRunner），
直接通过 WebSocket connection_manager 推送消息。

配置项 (config/settings.py):
- PROACTIVE_OUTREACH_ENABLED: 是否启用自动搭话
- PROACTIVE_OUTREACH_COOLDOWN_MINUTES: 搭话冷却时间（分钟）
- PROACTIVE_OUTREACH_IDLE_MINUTES: 触发搭话的空闲阈值（分钟）
- PROACTIVE_OUTREACH_COMPANION_PROMPT: 陪伴模式自定义提示词（为空则用默认）
- PROACTIVE_OUTREACH_WORK_PROMPT: 工作模式自定义提示词（为空则用默认）
"""
import asyncio
import concurrent.futures
import random
import time
import threading
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("proactive_outreach")

# 默认冷却时间（秒），实际值从 settings 读取
DEFAULT_COOLDOWN_SECONDS = 15 * 60


def _run_async(coro):
    """在同步线程中安全运行异步协程"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _get_settings():
    """延迟加载 settings，避免循环导入"""
    from config.settings import settings
    return settings


class ProactiveOutreachHandler:
    """主动搭话处理器

    注册到 DifferenceDetector.on_high_intensity()，
    在检测到高强度差异时触发一次大模型调用并推送消息到 WebSocket。
    所有时间/开关/提示词配置从 config.settings 读取。
    """

    def __init__(self):
        self._last_outreach_ts: float = time.time()
        self._current_cooldown_seconds: float = random.uniform(15 * 60, 30 * 60)
        self._lock = threading.Lock()
        settings = _get_settings()
        logger.info(
            f"[主动搭话] 初始化: "
            f"enabled={settings.PROACTIVE_OUTREACH_ENABLED}, "
            f"cooldown={settings.PROACTIVE_OUTREACH_COOLDOWN_MINUTES}min, "
            f"idle_threshold={settings.PROACTIVE_OUTREACH_IDLE_MINUTES}min, "
            f"首次冷却={self._current_cooldown_seconds / 60:.1f}min"
        )

    # ------------------------------------------------------------------
    # 冷却管理
    # ------------------------------------------------------------------

    def reset_cooldown(self) -> None:
        """重置冷却计时器 — 用户开始说话时调用，避免在活跃对话中搭话"""
        with self._lock:
            self._last_outreach_ts = time.time()
        logger.debug("[主动搭话] 冷却已重置（用户正在说话）")

    def _is_in_cooldown(self) -> bool:
        """是否在冷却期内"""
        with self._lock:
            elapsed = time.time() - self._last_outreach_ts
            cooldown = self._current_cooldown_seconds
        if cooldown <= 0:
            return False
        return elapsed < cooldown

    def _start_cooldown(self) -> None:
        """启动一次随机冷却（15-30分钟）"""
        settings = _get_settings()
        base = settings.PROACTIVE_OUTREACH_COOLDOWN_MINUTES * 60
        random_extra = random.uniform(0, base)  # 0 ~ base 秒的随机增量
        with self._lock:
            self._current_cooldown_seconds = base + random_extra
            self._last_outreach_ts = time.time()
        minutes = self._current_cooldown_seconds / 60
        logger.info(f"[主动搭话] 冷却启动: {minutes:.1f}分钟")

    # ------------------------------------------------------------------
    # 核心处理
    # ------------------------------------------------------------------

    def handle(self, differences: list) -> None:
        """DifferenceDetector 回调入口（在 daemon 线程中执行）

        Args:
            differences: intensity >= 50 的差异列表
        """
        settings = _get_settings()
        if not settings.PROACTIVE_OUTREACH_ENABLED:
            logger.debug("[主动搭话] 未启用，跳过")
            return

        if self._is_in_cooldown():
            logger.debug("[主动搭话] 冷却期内，跳过")
            return

        # 过滤：只关注 idle 类差异（空闲搭话）
        idle_diffs = [
            d for d in differences
            if d.source_type == "time" and "idle" in d.category
        ]
        if not idle_diffs:
            logger.debug(
                f"[主动搭话] 无 idle 类差异，跳过 "
                f"(收到: categories={[d.category for d in differences]}, "
                f"source_types={[d.source_type for d in differences]})"
            )
            return

        max_intensity = max(d.intensity for d in idle_diffs)
        idle_minutes = max(
            (d.payload.get("idle_minutes", 0) for d in idle_diffs), default=0
        )

        logger.info(
            f"[主动搭话] 触发: intensity={max_intensity}, "
            f"idle={idle_minutes:.0f}分钟"
        )

        try:
            result = self._do_outreach(idle_minutes)
            if result:
                self._start_cooldown()
            else:
                logger.warning("[主动搭话] _do_outreach 返回 False")
        except Exception as e:
            logger.error(f"[主动搭话] 执行失败: {e}", exc_info=True)

    def _do_outreach(self, idle_minutes: float) -> bool:
        """执行搭话：生成消息 → 存入会话 + 推送 WebSocket

        同步方法，在 daemon 线程中直接执行。
        仅 _call_large_model 需要异步（通过 _run_async），
        WebSocket 推送通过 send_json_from_thread 调度到 uvicorn 事件循环。
        """
        logger.info(f"[主动搭话] _do_outreach 开始, idle_minutes={idle_minutes}")

        # 1. 获取活跃 session 和对话上下文
        session_id, conversation_context, style_examples = self._get_active_session_info()

        if not session_id:
            logger.warning("[主动搭话] 无活跃 session，跳过")
            return False

        # 2. 加载模型身份和风格
        from config.settings import settings
        is_companion = False  # 陪伴模式改为 Skill 激活，不再使用 COMPANION_MODE 标志

        # 3. 构建 prompt（传入完整对话上下文）
        prompt = self._build_prompt(
            is_companion=is_companion,
            conversation_context=conversation_context,
            style_examples=style_examples,
            idle_minutes=idle_minutes,
        )

        # 4. 调用大模型（需要异步，创建新事件循环）
        response_text = _run_async(self._call_large_model(prompt))

        if not response_text or not response_text.strip():
            logger.warning("[主动搭话] 大模型返回空内容")
            return False

        logger.info(f"[主动搭话] 模型回复: {response_text[:100]}")

        # 5. 存入会话历史（用户下次发消息时上下文可见）
        self._append_to_session(session_id, response_text)

        # 6. 通过 uvicorn 事件循环推送 WebSocket（线程安全）
        self._push_to_websocket(session_id, response_text)

        return True

    # ------------------------------------------------------------------
    # 会话信息获取
    # ------------------------------------------------------------------

    def _get_active_session_info(self):
        """获取最近活跃的 session_id 和对话上下文

        优先选择有活跃 WebSocket 连接的 session，
        避免推送到无连接的 session 导致消息丢失。

        Returns:
            (session_id, conversation_context: str, style_examples: List[str])
            - conversation_context: 最近对话记录（用户+助手），供模型理解话题
            - style_examples: 最近助手消息，供风格模仿
        """
        try:
            from modules.thinking.api_stream import get_thinking_system, connection_manager
            system = get_thinking_system()

            if not system.sessions:
                return "", "", []

            # 优先选有活跃 WebSocket 连接的 session
            active_ws_sessions = set(connection_manager.active_connections.keys())

            best_session_id = ""
            best_ts = 0
            for sid, session_data in system.sessions.items():
                started = session_data.get("started_at", 0)
                # 有活跃连接的 session 优先
                has_ws = sid in active_ws_sessions
                # 用 (has_ws, started) 元组比较：有连接的排前面
                if (has_ws, started) > (
                    best_session_id in active_ws_sessions, best_ts
                ):
                    best_ts = started
                    best_session_id = sid

            if not best_session_id:
                return "", "", []

            if best_session_id not in active_ws_sessions:
                logger.warning(
                    f"[主动搭话] 最佳 session {best_session_id[:8]} 无活跃 WebSocket 连接，"
                    f"消息可能无法实时送达"
                )

            # 获取最近对话消息（用户 + 助手），构造可读的对话上下文
            messages = []  # 对话历史存根，不再使用记忆系统
            recent = [
                m for m in messages
                if m.get("role") in ("user", "assistant") and m.get("content", "").strip()
            ][-6:]  # 最近6条（~3轮对话）

            conversation_lines = []
            style_examples = []
            for m in recent:
                role_label = "用户" if m["role"] == "user" else "你"
                content = m["content"][:300]
                conversation_lines.append(f"{role_label}: {content}")
                if m["role"] == "assistant":
                    style_examples.append(content)

            conversation_context = "\n".join(conversation_lines)

            return best_session_id, conversation_context, style_examples[-5:]

        except Exception as e:
            logger.debug(f"[主动搭话] 获取会话信息失败: {e}")
            return "", "", []

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        is_companion: bool,
        conversation_context: str,
        style_examples: List[str],
        idle_minutes: float,
    ) -> str:
        """根据模式和历史构建搭话 prompt

        优先使用 settings 中的自定义提示词，为空则使用内置默认。
        """
        settings = _get_settings()

        if is_companion:
            custom = settings.PROACTIVE_OUTREACH_COMPANION_PROMPT
            if custom:
                return self._apply_custom_prompt(custom, idle_minutes, conversation_context, style_examples)
            return self._build_companion_prompt(conversation_context, style_examples, idle_minutes)
        else:
            custom = settings.PROACTIVE_OUTREACH_WORK_PROMPT
            if custom:
                return self._apply_custom_prompt(custom, idle_minutes, conversation_context, style_examples)
            return self._build_detector_prompt(conversation_context, idle_minutes)

    def _apply_custom_prompt(
        self, template: str, idle_minutes: float,
        conversation_context: str, style_examples: List[str],
    ) -> str:
        """将自定义提示词模板中的变量替换为实际值

        支持的变量:
        - {idle_minutes}: 空闲分钟数
        - {conversation_context}: 最近对话记录（用户+助手）
        - {history}: 最近助手消息列表（向下兼容）
        - {user_name}: 用户名
        """
        from config.settings import settings as cfg

        history_text = ""
        if style_examples:
            history_text = "\n".join(
                f"- \"{msg[:200]}\"" for msg in style_examples[-3:]
            )

        return template.format(
            idle_minutes=f"{idle_minutes:.0f}",
            conversation_context=conversation_context or "（无最近对话）",
            history=history_text,
            user_name=getattr(cfg, "USER_NAME", "用户"),
        )

    def _build_companion_prompt(
        self, conversation_context: str, style_examples: List[str], idle_minutes: float
    ) -> str:
        """陪伴模式: 朋友身份，延续之前的对话话题"""
        from modules.thinking.identity import ModelIdentity

        identity = ModelIdentity.from_template("large_companion")

        # 风格模仿
        style_block = ""
        if style_examples:
            examples = "\n".join(
                f"- \"{msg[:200]}\"" for msg in style_examples[-3:]
            )
            style_block = (
                f"\n\n【你之前的说话风格 — 请模仿】\n"
                f"以下是你说过的话，用类似的语气和风格搭话：\n{examples}"
            )
        else:
            style_block = (
                "\n\n【无历史记录 — 默认风格】\n"
                "用自然、轻松、像朋友一样的语气搭话。"
                "不要正式，不要太热情，就像一个好朋友随口问一句。"
            )

        # 对话上下文（让模型知道用户之前在聊什么）
        context_block = ""
        if conversation_context:
            context_block = (
                f"\n\n【最近对话记录】\n"
                f"以下是你们最近的对话内容，了解上下文后自然地延续话题：\n"
                f"{conversation_context}\n"
            )

        return (
            f"你是用户的对话伙伴。{identity.personality}\n\n"
            f"【当前情况】\n"
            f"用户已经 {idle_minutes:.0f} 分钟没有说话了。\n"
            f"你想主动跟用户打个招呼，问问对方是否需要帮助或者只是聊聊天。\n"
            f"{context_block}"
            f"\n【要求】\n"
            f"- 只输出你要对用户说的话（1-2句话，简短自然）\n"
            f"- 不要提'系统'、'检测'、'差异'、'工具'这些词\n"
            f"- 不要说'作为AI'、'我检测到'、'我发现'这类话\n"
            f"- 不要用敬语，不用'您'\n"
            f"- 语气要自然随意，像朋友随口问一句\n"
            f"- 可以有点小情绪（比如抱怨无聊、吐槽没人理）但不要太夸张\n"
            f"- 如果知道用户在做什么，可以自然地接上之前的话题\n"
            f"{style_block}\n\n"
            f"现在说："
        )

    def _build_detector_prompt(self, conversation_context: str, idle_minutes: float) -> str:
        """工作模式: 探测器身份，基于对话上下文询问"""
        context_block = ""
        if conversation_context:
            context_block = (
                f"\n【最近对话记录】\n"
                f"{conversation_context}\n\n"
                f"基于以上对话上下文，自然地询问用户是否需要继续之前的话题。\n"
            )

        return (
            "你是系统的差异检测器，负责监控系统状态并在异常时通知用户。\n\n"
            "【当前情况】\n"
            f"系统已空闲 {idle_minutes:.0f} 分钟，超过正常阈值。\n"
            f"{context_block}"
            "【要求】\n"
            "- 简洁专业地报告当前状态\n"
            "- 询问用户是否需要帮助，可以自然地衔接之前的话题\n"
            "- 1-2句话即可，不要啰嗦\n"
            "- 不要暴露内部实现细节（如 intensity、TTL 等技术参数）\n\n"
            "现在通知用户："
        )

    # ------------------------------------------------------------------
    # 大模型调用
    # ------------------------------------------------------------------

    async def _call_large_model(self, prompt: str) -> str:
        """单次大模型调用（不经过编排器）

        每次创建新客户端实例，避免 singleton 的 aiohttp session
        绑定到已关闭的事件循环（daemon 线程中 asyncio.run 会创建新 loop）。
        """
        try:
            from infra.model.large_model_client import LargeModelClient
            from config.settings import settings

            client = LargeModelClient(
                api_key=settings.LARGE_MODEL_API_KEY,
                api_url=settings.LARGE_MODEL_API_URL,
                timeout=30,
            )
            client.max_tokens = 200
            client.temperature = 0.7

            result = await client.generate(prompt, max_retries=1)

            # 关闭本次创建的 session
            try:
                await client.close()
            except Exception as e:
                logger.debug(f"[主动搭话] 关闭模型客户端 session 失败 (非致命): {e}")

            return result if isinstance(result, str) else str(result)
        except Exception as e:
            logger.error(f"[主动搭话] 大模型调用失败: {e}")
            return ""

    # ------------------------------------------------------------------
    # WebSocket 推送
    # ------------------------------------------------------------------

    def _push_to_websocket(
        self, session_id: str, content: str
    ) -> None:
        """通过 WebSocket connection_manager 推送消息（线程安全）

        使用 send_json_from_thread 将发送调度到 uvicorn 事件循环，
        避免在 daemon 线程的新事件循环中直接操作 WebSocket transport。
        """
        if not session_id:
            logger.warning("[主动搭话] 无活跃 session，跳过推送")
            return

        try:
            from modules.thinking.api_stream import connection_manager, _build_event

            envelope = _build_event(
                session_id=session_id,
                msg_type="message",
                event="assistant_message",
                content=content,
                role="main",
                data={
                    "source": "proactive_outreach",
                    "trace_id": "proactive_outreach",
                },
            )
            ok = connection_manager.send_json_from_thread(session_id, envelope)
            if ok:
                logger.info(
                    f"[主动搭话] 已推送到 session={session_id[:8]}: "
                    f"{content[:60]}..."
                )
            else:
                logger.warning(
                    f"[主动搭话] WebSocket 推送失败（无活跃连接或超时），"
                    f"session={session_id[:8]}"
                )
        except Exception as e:
            logger.error(f"[主动搭话] WebSocket 推送失败: {e}")

    # ------------------------------------------------------------------
    # 会话历史写入
    # ------------------------------------------------------------------

    def _append_to_session(
        self, session_id: str, content: str
    ) -> None:
        """将搭话消息写入会话历史，让后续对话能看到"""
        if not session_id:
            return
        try:
            from modules.thinking.api_stream import get_thinking_system
            system = get_thinking_system()
            # 从 daemon 线程调用，不能用 get_event_loop()
            import concurrent.futures
            def _do():
                import asyncio
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    new_loop.run_until_complete(
                        system._append_message(session_id, "assistant", content)
                    )
                finally:
                    new_loop.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_do).result(timeout=5)
        except Exception as e:
            logger.debug(f"[主动搭话] 写入会话历史失败: {e}")


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

class PerceptionThinkTriggerPort:
    """感知→思考触发器端口 — ThinkTriggerPort 协议实现

    当 PerceptionThinkTrigger 收到高强度感知差异时，
    通过此端口发起一次单次模型思考并将结果推送到 WebSocket。

    与 ProactiveOutreachHandler 的区别：
    - ProactiveOutreachHandler: 注册在 DifferenceDetector 上，处理空闲时间
    - PerceptionThinkTriggerPort: 注册在 PerceptionThinkTrigger 上，处理感知差异
    """

    async def trigger_think(self, context: str, differences: list) -> dict:
        """触发单次模型思考

        Args:
            context: 差异描述文本（供模型理解）
            differences: 差异列表

        Returns:
            包含 duration_ms 的字典
        """
        start_ts = time.time()

        if not context:
            return {"duration_ms": 0}

        logger.info(f"[感知→思考] 触发: {context[:80]}...")

        # 1. 构建 prompt，分析环境变化
        prompt = (
            "你注意到环境发生了变化。\n\n"
            f"{context}\n\n"
            "【要求】\n"
            "- 分析这个变化是否值得关注\n"
            "- 如果值得关注，简短注明（1-2句）\n"
            "- 如果不值得，忽略即可\n"
            "- 不要编造细节，只说观察到的情况\n"
            "现在："
        )

        # 2. 调用大模型
        response_text = await self._call_large_model(prompt)

        if not response_text:
            logger.debug("[感知→思考] 模型返回空，跳过推送")
            duration = (time.time() - start_ts) * 1000
            return {"duration_ms": duration}

        # 3. 推送到活跃 WebSocket session
        self._push_to_websocket(response_text)

        duration = (time.time() - start_ts) * 1000
        logger.info(f"[感知→思考] 完成: {duration:.0f}ms")
        return {"duration_ms": duration}

    async def _call_large_model(self, prompt: str) -> str:
        """单次大模型调用"""
        try:
            from infra.model.large_model_client import LargeModelClient
            from config.settings import settings

            client = LargeModelClient(
                api_key=settings.LARGE_MODEL_API_KEY,
                api_url=settings.LARGE_MODEL_API_URL,
                timeout=15,
            )
            client.max_tokens = 150
            client.temperature = 0.5

            result = await client.generate(prompt, max_retries=1)
            try:
                await client.close()
            except Exception:
                pass
            return result if isinstance(result, str) else str(result)
        except Exception as e:
            logger.debug(f"[感知→思考] 模型调用失败: {e}")
            return ""

    def _push_to_websocket(self, content: str) -> None:
        """通过 WebSocket connection_manager 推送消息"""
        try:
            from modules.thinking.api_stream import (
                connection_manager,
                _build_event,
            )

            # 找活跃的 session
            session_ids = list(connection_manager.active_connections.keys())
            if not session_ids:
                logger.debug("[感知→思考] 无活跃 WebSocket 连接，跳过推送")
                return

            for sid in session_ids[:1]:  # 只推第一个活跃 session
                envelope = _build_event(
                    session_id=sid,
                    msg_type="message",
                    event="difference_detected",
                    content=content,
                    role="main",
                    data={
                        "source": "perception_think_trigger",
                    },
                )
                connection_manager.send_json_from_thread(sid, envelope)
        except Exception as e:
            logger.debug(f"[感知→思考] WebSocket 推送失败: {e}")


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_handler: Optional[ProactiveOutreachHandler] = None


def get_proactive_outreach_handler() -> ProactiveOutreachHandler:
    """获取全局主动搭话处理器（所有配置从 settings 读取）"""
    global _handler
    if _handler is None:
        _handler = ProactiveOutreachHandler()
    return _handler
