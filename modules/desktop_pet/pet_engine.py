"""桌面宠物引擎 — 独立页面形态（不再作为 ChatSession）

- 桌宠已从「会话」形态改为「独立页面」：对话历史、人设、API 配置、记忆
  全部落在桌宠独立设施（data/pet.db，见 store.py / memory.py），不读写
  sessions 表的 pet_main，也不使用全局事件记忆库。
- 对话走独立 ReAct 循环（react_loop.py）：默认 direct 直答，循环本体
  由用户亲自设计后通过 build_react_loop 挂载。
- 订阅 SPEECH_DETECTED 语音事件 → 识别文本 → 循环对话 → TTS 语音回复
  + 广播 pet_reply（桌宠窗口/前端展示气泡）
"""
import asyncio
import threading
import time

from typing import Any, Callable, Dict, Optional

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("pet_engine")


class PetEngine:
    """桌宠对话引擎（单例，感知系统启动时创建）"""

    _instance: Optional["PetEngine"] = None
    _lock = threading.Lock()

    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._sub_id = ""
        self._running = False
        self._client = None
        self._client_cfg = None
        self._reply_listeners = []  # (session_id, 回调) 前端/桌宠订阅回复
        self.last_reply = None       # {"time": epoch, "text": str} 桌宠窗口轮询取
        self._loop = None
        # 输出前视觉注入的缓存/熔断（不做持续后台检测）：
        #   _vision_cache 命中窗口内复用，避免连续问答重复花钱
        #   _vision_fail_at 连续失败后短暂静默，避免每次回复被坏后端拖慢
        self._vision_cache: Dict[str, Any] = {"time": 0.0, "text": ""}
        self._vision_fail_at: float = 0.0

    @classmethod
    def get_instance(cls, event_bus=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(event_bus)
        return cls._instance

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "DESKTOP_PET_ENABLED", True))

    @property
    def config(self) -> dict:
        from modules.desktop_pet.store import PetStore
        return PetStore.get_instance().get_config()

    @property
    def loop(self):
        """当前 ReAct 循环（随配置懒实例化；循环本体由用户亲自设计）"""
        # 惰性导入：react_loop 对本模块只有 TYPE_CHECKING 期的类型引用
        # （react_loop.py:22，运行时不执行），故此处保持惰性即可，无环风险。
        from modules.desktop_pet.react_loop import build_react_loop
        loop_type = self.config.get("loop_type") or "direct"
        if self._loop is None or getattr(self._loop, "name", "") != loop_type:
            self._loop = build_react_loop(loop_type)
        return self._loop

    # ── 生命周期 ──

    def start(self) -> None:
        if self._running or not self.enabled:
            return
        self._running = True
        if self._event_bus:
            from modules.shared.events import PerceptionEventType
            self._sub_id = self._event_bus.subscribe(
                PerceptionEventType.SPEECH_DETECTED,
                async_handler=self._on_speech,
            )
            logger.info("[Pet] 桌宠已启动（独立页面形态 + 语音订阅；视觉仅在输出前采集注入）")
        else:
            logger.warning("[Pet] 无事件总线，语音触发不可用")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._event_bus and self._sub_id:
            try:
                self._event_bus.unsubscribe(self._sub_id)
            except Exception as _swallowed_err:
                logger.debug(f"[stop] ignored Exception: {_swallowed_err}")
            self._sub_id = ""
        logger.info("[Pet] 桌宠已停止")

    # ── 独立 LLM client（PetConfig 的 API 配置，未配置时回退全局） ──

    @property
    def client(self):
        """桌宠独立模型客户端：按 PetConfig 的 api_url/key/model/format 构建。

        配置变更时自动重建（对比配置指纹），未配置的字段回退全局 settings。
        """
        from infra.model.config_fingerprint import close_client_session
        cfg = self.config
        fingerprint = (
            cfg.get("api_url", ""), cfg.get("api_key", ""), cfg.get("api_model", ""),
            cfg.get("api_format", ""), cfg.get("api_provider", ""),
        )
        if self._client is None:
            self._client = self._build_client(cfg)
            self._client_cfg = fingerprint
        elif self._client_cfg != fingerprint:
            old = self._client
            self._client = self._build_client(cfg)
            self._client_cfg = fingerprint
            close_client_session(old)
        return self._client

    @staticmethod
    def _build_client(cfg: dict):
        from infra.model.large_model_client import LargeModelClient
        from config.settings import settings
        return LargeModelClient(
            api_key=cfg.get("api_key") or None,
            api_url=cfg.get("api_url") or None,
            api_format=cfg.get("api_format") or "",
            model_name=cfg.get("api_model") or None,
            api_provider=cfg.get("api_provider") or "",
            enable_thinking=settings.LARGE_MODEL_ENABLE_THINKING,
            timeout=180,
        )

    # ── 语音触发 ──

    async def _on_speech(self, event) -> None:
        """语音事件 → 入队桌宠对话（串行队列统一消费）"""
        if not self.enabled:
            return
        try:
            text = (event.payload or {}).get("text", "") if hasattr(event, "payload") else ""
            if not text or not text.strip():
                return
            reply = await self.chat(text.strip())
            if not reply:
                return
            await self._after_reply(reply)
        except Exception as e:
            logger.error(f"[Pet] 语音对话失败: {e}")

    # ── 串行任务队列（所有触发源统一入队，同一时间只跑一个模型任务） ──

    @property
    def task_queue(self):
        from modules.desktop_pet.task_queue import PetTaskQueue
        return PetTaskQueue.get_instance()

    # @link:block PetChat
    async def chat(self, text: str) -> str:
        """非流式对话：入队 → worker 串行执行 ReAct 循环 → 回复文本

        语音/按键等触发与页面聊天共用同一队列，保证同一时间桌宠只跑一个模型任务。
        """
        if not self.enabled:
            return ""
        try:
            # @link:block Pet_Think = Think · Write
            fut = await self.task_queue.submit("chat", lambda: self.loop.run(self, text))
            if fut is None:
                return ""
            reply = await fut
            if reply:
                self.save_pair(text, reply)
            return reply or ""
        except Exception as e:
            logger.error(f"[Pet] 对话失败: {e}")
            return ""

    async def stream_chat(self, text: str, extra_system: str = "", on_reasoning: Optional[Callable[[str], None]] = None):
        """流式对话：独立 ReAct 循环，逐 token 产出，思考过程逐段回调，结束后保存历史"""
        if not self.enabled:
            return
        # 每轮完整文本产出后注入的轮次完成标记（前端收到后结束"接收中"，准备下一轮）
        ROUND_DONE = "\x00ROUND_DONE\x00"
        queue: "asyncio.Queue" = asyncio.Queue()
        collected: list = []

        def on_token(t: str):
            try:
                queue.put_nowait(t)
                if t != ROUND_DONE:
                    collected.append(t)
            except Exception as _swallowed_err:
                logger.debug(f"[on_token] ignored Exception: {_swallowed_err}")

        # 串行队列：流式对话作为任务入队，等待 worker 执行期间挂起（不占 SSE 连接），
        # 轮到本任务时才开始跑模型 —— 页面聊天/语音/定时思考统一串行。
        fut = await self.task_queue.submit(
            "stream" if (text or "").strip() else "auto_think",
            lambda: self._stream_task(text, on_token, extra_system, on_reasoning),
        )
        if fut is None:
            return

        async def _runner() -> None:
            await fut  # 任务内异常在此抛出

        task = asyncio.create_task(_runner())
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    token = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield token
                except asyncio.TimeoutError:
                    continue
            # 任务异常且无任何产出 → 向上传播，让 SSE 层发 error 帧而不是静默"无回复"
            await task
        except Exception as e:
            logger.error(f"[Pet] 流式对话失败: {e}")
            if collected:
                yield "".join(collected)
            else:
                raise
        finally:
            # 消费结束/异常/SSE 断开（CancelledError）/生成器被关闭（GeneratorExit）：
            # 任务未跑完（排队中或执行中）则取消，worker 跳过继续下一个
            if not fut.done():
                pt = getattr(fut, "_pet_task", None)
                if pt is not None:
                    pt.cancel()
                task.cancel()

    async def _stream_task(self, text: str, on_token: Callable[[str], None], extra_system: str = "", on_reasoning: Optional[Callable[[str], None]] = None) -> None:
        # 持续思考循环：每轮完整文本产出即持久化（首次连同 user 落库），
        # 不等到循环结束 —— 无限循环可能永不自然结束（由用户停止/上下文上限终止）
        user_saved = False

        def on_message(m: str) -> None:
            nonlocal user_saved
            try:
                # @link:block PetPersist = Retrieve · Write
                from modules.desktop_pet.store import PetStore
                store = PetStore.get_instance()
                # 主动思考模式（text 为空）不存 user 历史，只存 assistant 自言自语
                if not user_saved and text and text.strip():
                    store.add_history("user", text)
                    user_saved = True
                # 防重护栏：主动思考产出与库内最近一条 assistant 完全相同 →
                # 模型在复述旧话，不落库（否则重开页面历史里全是同一句）
                if not text.strip():
                    if PetEngine._last_assistant_same_as(None, m, store):
                        logger.info("[Pet] 主动思考产出与上一条重复，跳过落库")
                        # 跳过 assistant 落库，但任由用户历史（如有）不受影响
                        return
                store.add_history("assistant", m)
                self.last_reply = {"time": time.time(), "text": m}
                logger.info(f"[Pet] 思考产出: {m[:40]}")
                # 通知 SSE 层一轮完整回复已产出（前端结束"接收中"，准备下一轮）
                on_token("\x00ROUND_DONE\x00")
            except Exception as ex:
                logger.error(f"[Pet] 持久化失败: {ex}")

        try:
            # 桌宠独立页面形态：SSE 请求即连接存在，不做 WS 握手检查
            reply = await self.loop.run(
                self, text,
                on_token=on_token, on_reasoning=on_reasoning,
                on_message=on_message, extra_system=extra_system,
            )
            if reply:
                logger.info(f"[Pet] 流式对话完成: {text[:30]} → {reply[:60]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Pet] 流式任务失败: {e}")
            raise

    # ── 循环可用的能力（供 PetReactLoop 实现调用） ──

    async def build_context(self, query: str = "") -> str:
        """上下文：时间/用户身份 + 感知环境 + 桌宠独立记忆（不再用全局事件库）"""
        extras = []
        try:
            from datetime import datetime
            from config.settings import settings as _cfg
            extras.append(f"【当前时间】{datetime.now().strftime('%Y-%m-%d %H:%M')}")
            extras.append(f"【对话对象】{getattr(_cfg, 'USER_NAME', '用户') or '用户'}")
        except Exception as _swallowed_err:
            logger.debug(f"[build_context] ignored Exception: {_swallowed_err}")
        try:
            from modules.thinking.context.sources.perception_source import PerceptionSource
            frag = await PerceptionSource().collect()
            if frag and getattr(frag, "content", ""):  # type: ignore[arg-type]
                extras.append(frag.content)
        except Exception as _swallowed_err:
            logger.debug(f"[build_context] ignored Exception: {_swallowed_err}")
        # 输出前视觉注入：不做持续后台检测，仅在每次即将产出前截屏 + VLM 概括。
        # 命中缓存窗口（8s）直接复用；最近失败（60s）静默跳过，避免回复被坏后端拖慢。
        vision = await self._capture_screen_vision()
        if vision:
            extras.append(f"【屏幕画面】\n{vision}")
        try:
            from modules.desktop_pet.memory import PetMemory
            # memory_enabled 开关：关闭时跳过记忆检索
            if self.config.get("memory_enabled", True):
                # 桌宠循环设计：记忆只返回 top2（最大两条），与 8k 上下文匹配
                mems = await PetMemory.get_instance().retrieve(
                    query=query or "用户与桌宠互动", max_results=2
                )
                if mems:
                    lines = ["【桌宠记忆】（独立记忆库，仅作参考）"]
                    for i, ev in enumerate(mems, 1):
                        try:
                            from datetime import datetime
                            date = datetime.fromtimestamp(float(ev.get("created_at", 0) or 0)).strftime("%Y-%m-%d")
                        except Exception:
                            date = "未知日期"
                        lines.append(f"  [{i}] (日期={date}) {str(ev.get('text', ''))[:120]}")
                    extras.append("\n".join(lines))
        except Exception as _swallowed_err:
            logger.debug(f"[build_context] ignored Exception: {_swallowed_err}")
        return "\n\n".join(extras)

    # ── 输出前视觉采集（替代旧的持续 proactive 后台检测） ──

    _VISION_CACHE_TTL = 8.0      # 命中窗口：连续问答在窗口内复用，避免重复花钱
    _VISION_FAIL_COOLDOWN = 60.0  # 失败后静默期：坏后端不拖慢每次回复

    async def _capture_screen_vision(self) -> str:
        """截屏 + VLM 概括屏幕，返回描述（失败/超时/冷却期返回空串）。"""
        now = time.time()
        cache = getattr(self, "_vision_cache", None)
        fail_at = getattr(self, "_vision_fail_at", 0.0)
        if cache and (now - cache.get("time", 0) < self._VISION_CACHE_TTL):
            return cache.get("text", "")
        if now - fail_at < self._VISION_FAIL_COOLDOWN:
            return ""
        try:
            import asyncio as _asyncio

            async def _do():
                from utils.screen_capture import capture_screen_bytes
                from infra.data_process.core.image_analyzer import ImageAnalyzer

                screen_bytes = capture_screen_bytes(max_width=768)
                if not screen_bytes:
                    return ""
                # 共享单例：openai 后端初始化极轻，不 close()，避免把全局实例重置
                analyzer = ImageAnalyzer()
                await analyzer.initialize()
                result = await analyzer.analyze(
                    screen_bytes,
                    prompt="用一两句话客观概括当前屏幕内容：什么应用、什么界面、正在做什么。",
                )
                return (result.get("description") or "").strip()[:300]

            desc = await _asyncio.wait_for(_do(), timeout=20.0)
            if not desc:
                return ""
            self._vision_cache = {"time": time.time(), "text": desc}
            logger.info(f"[Pet] 输出前视觉注入: {desc[:80]}")
            return desc
        except Exception as e:
            self._vision_fail_at = time.time()
            logger.warning(f"[Pet] 输出前视觉采集失败 (非致命，{self._VISION_FAIL_COOLDOWN:.0f}s 内跳过): {e}")
            return ""

    def build_messages(self, text: str, extra_system: str = ""):
        """独立历史（最近 max_history 条，默认 8）+ 配置化桌宠人设 + 用户消息。
        text 为空时 = 主动思考模式：不加 user 消息，让模型自己决定说什么。"""
        from infra.model.base_model import ChatMessage

        cfg = {}
        try:
            cfg = self.config
        except Exception:
            logger.warning("[Pet] 读取桌宠配置失败，使用默认人设")

        history = []
        try:
            from modules.desktop_pet.store import PetStore
            limit = int(cfg.get("max_history") or 8)
            for m in PetStore.get_instance().get_history(limit=max(1, limit)):
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and content:
                    history.append(ChatMessage(role=role, content=content))
        except Exception as _swallowed_err:
            logger.debug(f"[build_messages] ignored Exception: {_swallowed_err}")

        system = cfg.get("persona") or (
            "你是桌面上的 AI 桌宠助手，像 Siri 一样用语音和用户交流。"
            "性格友善、可爱、贴心。回答要简短自然（通常一两句话），适合语音朗读。"
            "用中文回答。"
        )
        if extra_system:
            system = system + "\n\n" + extra_system
        msgs = [ChatMessage(role="system", content=system)] + history
        if text and text.strip():
            msgs.append(ChatMessage(role="user", content=text))
        else:
            # 主动思考：spark 等本地服务器对「assistant 结尾续写」的请求会返回空
            # content/reasoning（实测 real[:6] 以 assistant 结尾 → 完全空回）。
            # 补一条弱引导 user 消息触发正式输出（仅本轮请求使用，不落库）。
            msgs.append(ChatMessage(
                role="user",
                content="（没有新的用户输入。你可以自言自语地思考、延续话题、反思，或者什么都不说表示想休息一下。）",
            ))
        return msgs

    def save_pair(self, text: str, reply: str) -> None:
        """写入桌宠独立历史（pet.db，不再写 sessions 表）"""
        try:
            from modules.desktop_pet.store import PetStore
            store = PetStore.get_instance()
            store.add_history("user", text)
            store.add_history("assistant", reply)
        except Exception as _swallowed_err:
            logger.debug(f"[save_pair] ignored Exception: {_swallowed_err}")

    async def emit_reply(self, reply: str) -> None:
        """广播回复（统一推送出口；历史已由 save_pair 持久化，不重复落库）"""
        try:
            from modules.thinking.frontend_channel import push_content
            await push_content(
                "pet",
                msg_type="pet",
                event="pet_reply",
                content=reply,
                role="assistant",
                data={"session_id": "pet"},
                persist=False,
            )
        except Exception as e:
            logger.debug(f"[Pet] 广播回复失败: {e}")

    async def _after_reply(self, reply: str) -> None:
        """回复后：TTS 语音播放 + 广播 pet_reply（桌宠窗口/前端气泡）"""
        # 缓存最近回复（桌宠窗口轮询）
        self.last_reply = {"time": time.time(), "text": reply}
        # TTS
        try:
            from modules.output_system.tts import TTSEngine
            tts = TTSEngine()
            if tts.enabled or True:
                path = tts.synthesize_sync(reply)
                if path:
                    await asyncio.to_thread(_play_audio, path)
        except Exception as e:
            logger.debug(f"[Pet] TTS 失败: {e}")
        await self.emit_reply(reply)

    def is_active(self) -> bool:
        return self._running

    @staticmethod
    def _last_assistant_same_as(engine_self, message: str, store) -> bool:
        """主动思考防重轴缘：库内最近一条 assistant 是否与 message 内容一致

        纯函数路径，便于测试直接调用（不走实例生命周期/队列/事件总线）。
        基于最新一条 assistant 做比对（get_history 已改为取最新 N 条）。
        """
        try:
            rows = store.get_history(limit=1)
            if not rows:
                return False
            last_assistant = rows[-1].get("content")
            if not last_assistant:
                return False
            return last_assistant.strip() == message.strip()
        except Exception:
            return False

    @staticmethod
    def _last_assistant_same_as_raw(engine_self, message: str, store) -> Optional[str]:
        """供测试审计：与防重轴缘一致的最新 assistant 内容（命中时返回该内容，否则 None）

        用于测试捕获防重轴缘命中，并揭示为什么重复落库还是发生（
        比如 get_history 返回的是旧缓存而非最新写入的行）。
        """
        try:
            rows = store.get_history(limit=1)
            if not rows:
                return None
            last = rows[-1].get("content")
            if not last:
                return None
            if last.strip() == message.strip():
                return last
            return None
        except Exception:
            return None


def _play_audio(path: str) -> None:
    """播放音频（macOS afplay / 通用）"""
    import subprocess
    import os
    if not path or not os.path.exists(path):
        return
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        else:
            subprocess.run(["afplay", path], capture_output=True, timeout=60)
    except Exception as _swallowed_err:
        logger.debug(f"[_play_audio] ignored Exception: {_swallowed_err}")
