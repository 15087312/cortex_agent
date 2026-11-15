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
import os
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
        # 实时语音：转写文字先进缓冲，等冷却（min_think_interval）到了再合并唤醒
        self._loop_async = None
        self._pending_voice: list = []
        self._wake_task = None
        self._mic = None
        self._player = _AudioPlayer()

    @classmethod
    def get_instance(cls, event_bus=None):
        """端口优先取桌宠引擎：接管 ``pet_engine`` 端口 = 换掉全部调用点。

        内置单例挪进 ``_builtin_instance``，bootstrap 给这条端口的 provider 指的也是
        它 —— 指回本函数就是 端口→本函数→端口 的一调用即递归，那种接线由
        scripts/seam_catalogue.py 在生成目录时判错。
        带参数的调用点在接管后不再传参：插件自己决定用什么构造，原来的参数只喂内置实现。
        """
        from infra.tool_manager.service_registry import get_capability

        factory = get_capability("pet_engine")
        if factory is not None:
            return factory()
        return cls._builtin_instance(event_bus)

    @classmethod
    def _builtin_instance(cls, event_bus=None):
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
        try:
            self._loop_async = asyncio.get_running_loop()
        except RuntimeError:
            self._loop_async = None
        # 实时监听：后端直读本机麦克风（与浏览器/窗口无关）
        self.refresh_voice()
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
        # 实时语音收尾：取消待唤醒任务、关麦、停掉正在播的 TTS
        wake = getattr(self, "_wake_task", None)
        if wake is not None:
            try:
                wake.cancel()
            except Exception:
                pass
            self._wake_task = None
        getattr(self, "_pending_voice", []).clear()
        mic = getattr(self, "_mic", None)
        if mic is not None:
            try:
                mic.stop()
            except Exception as _mic_err:
                logger.debug(f"[Pet] 停止麦克风失败: {_mic_err}")
        player = getattr(self, "_player", None)
        if player is not None:
            try:
                player.stop()
            except Exception:
                pass
        if self._event_bus and self._sub_id:
            try:
                self._event_bus.unsubscribe(self._sub_id)
            except Exception as _swallowed_err:
                logger.debug(f"[stop] ignored Exception: {_swallowed_err}")
            self._sub_id = ""
        # 串行任务队列的 worker 也要收尾，否则循环关闭后它会变成 pending task
        try:
            self.task_queue.shutdown()
        except Exception as _q_err:
            logger.debug(f"[Pet] 关闭任务队列失败: {_q_err}")
        mem_client = getattr(self, "_memory_client", None)
        if mem_client is not None:
            try:
                from infra.model.config_fingerprint import close_client_session
                close_client_session(mem_client)
            except Exception as _swallowed_err:
                logger.debug(f"[stop] close memory client: {_swallowed_err}")
            self._memory_client = None
        logger.info("[Pet] 桌宠已停止")

    # ── 独立 LLM client（PetConfig 的 API 配置，未配置时回退全局） ──

    @property
    def client(self):
        """桌宠独立模型客户端：按 PetConfig 的 api_url/key/model/format 构建。

        配置变更时自动重建（对比配置指纹），未配置的字段回退全局 settings。
        独立 API 鉴权/余额失败时，_stream_task 会临时设置 _client_override
        使用主模型配置重试本次请求。
        """
        override = getattr(self, "_client_override", None)
        if override is not None:
            return override
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
            api_provider=cfg.get("api_provider") or "",            enable_thinking=settings.LARGE_MODEL_ENABLE_THINKING,
            timeout=180,
        )

    # ── 实时语音（后端麦克风）：转写累积 + 冷却唤醒 ──

    def submit_utterance(self, text: str, source: str = "mic") -> None:
        """接收一段转写文字：入缓冲，等冷却后合并唤醒（线程安全）

        冷却时间 = 桌宠配置的 min_think_interval（用户自己设的那个间隔）。
        每来一句就重新计时，所以是「说完最后一句、静默满 N 秒」才唤醒。
        """
        text = (text or "").strip()
        if not text or not self._running:
            return
        # 用户在说话 → 立刻停掉正在播的 TTS（打断）
        player = getattr(self, "_player", None)
        if player is not None:
            try:
                player.stop()
            except Exception:
                pass
        loop = self._loop_async
        if loop is None or loop.is_closed():
            self._pending_voice.append(text)
            return
        try:
            loop.call_soon_threadsafe(self._queue_utterance, text)
        except RuntimeError as e:
            logger.debug(f"[Pet] 转写入队失败（循环已关闭）: {e}")

    def _queue_utterance(self, text: str) -> None:
        self._pending_voice.append(text)
        self._reschedule_wake()

    def _cooldown_seconds(self) -> float:
        try:
            return max(0.0, float(self.config.get("min_think_interval") or 0))
        except (TypeError, ValueError):
            return 5.0

    def _reschedule_wake(self) -> None:
        if not getattr(self, "_pending_voice", None):
            return
        wake = getattr(self, "_wake_task", None)
        if wake is not None and not wake.done():
            wake.cancel()
        delay = self._cooldown_seconds()
        self._wake_task = asyncio.create_task(self._wake_after(delay))

    async def _wake_after(self, delay: float) -> None:
        """冷却到期：把缓冲里所有转写合并注入上下文，唤醒一轮

        冷却期间只累积、不调模型；多句一次性合并成一条用户消息，
        避免用户说三句、桌宠回三次（也避免只听到最后一句）。
        """
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        texts, self._pending_voice = self._pending_voice, []
        merged = "\n".join(t for t in texts if t)
        if not merged:
            return
        logger.info(
            f"[Pet] 语音唤醒（{len(texts)} 段，冷却 {delay:g}s）: {merged[:60]}"
        )
        try:
            reply = await self.chat(merged)
            if reply:
                await self._after_reply(reply, user_turn=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Pet] 语音唤醒失败: {e}")

    def interrupt_speech(self) -> None:
        """打断当前 TTS 播放（麦克风检测到用户开口时调用）"""
        player = getattr(self, "_player", None)
        if player is not None:
            try:
                player.stop()
            except Exception:
                pass

    def refresh_voice(self) -> None:
        """按 realtime_voice 开关启停麦克风（配置变更 / 启动时调用）"""
        if not self._running:
            return
        want = False
        try:
            want = bool(self.config.get("realtime_voice"))
        except Exception:
            want = False
        mic = getattr(self, "_mic", None)
        if want:
            if mic is None:
                from modules.desktop_pet.voice.mic_listener import BackendMicListener
                mic = BackendMicListener(
                    on_utterance=self.submit_utterance,
                    on_speech_start=self.interrupt_speech,
                )
                self._mic = mic
            if not mic.running and not mic.start():
                logger.warning(f"[Pet] 麦克风监听启动失败: {mic.last_error}")
        elif mic is not None and mic.running:
            mic.stop()

    def voice_status(self) -> dict:
        """实时语音状态（给前端显示“正在听/识别中”）"""
        mic_obj = getattr(self, "_mic", None)
        mic = mic_obj.status() if mic_obj is not None else {
            "running": False, "state": "idle", "last_error": "",
            "energy_threshold": 0, "silence_duration": 0, "heard_anything": False,
        }
        return {
            **mic,
            "pending": len(getattr(self, "_pending_voice", [])),
            "cooldown": self._cooldown_seconds(),
        }

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
                from modules.desktop_pet.speech import split_speech
                visible, _ = split_speech(reply)
                self.save_pair(text, visible or reply)
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

    @staticmethod
    def _is_auth_or_balance_error(exc: Exception) -> bool:
        """独立 API 的鉴权/余额类错误：可回退主模型重试一次。"""
        msg = str(exc).lower()
        return any(k in msg for k in (
            "402", "401", "403",
            "insufficient balance", "insufficient_quota",
            "invalid api key", "invalid_api_key", "authentication",
            "unauthorized",
        ))

    def _has_independent_api(self) -> bool:
        """是否配置了与主模型不同的独立 API（否则回退没有意义）。"""
        try:
            cfg = self.config
            return bool(cfg.get("api_key") or cfg.get("api_url") or cfg.get("api_model"))
        except Exception:
            return False

    async def _stream_task(self, text: str, on_token: Callable[[str], None], extra_system: str = "", on_reasoning: Optional[Callable[[str], None]] = None) -> None:
        # 持续思考循环：每轮完整文本产出即持久化（首次连同 user 落库），
        # 不等到循环结束 —— 无限循环可能永不自然结束（由用户停止/上下文上限终止）
        user_saved = False
        persisted = False
        emitted = False
        memory_scheduled = False
        self._client_override = None
        self.last_fallback_reason = ""

        def _on_token(t: str) -> None:
            nonlocal emitted
            if t != "\x00ROUND_DONE\x00":
                emitted = True
            on_token(t)

        def on_message(m: str, is_tool: bool = False) -> None:
            nonlocal user_saved, persisted, memory_scheduled
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
                # <say> 只是发声标记，不写入历史/气泡
                from modules.desktop_pet.speech import split_speech
                visible, _ = split_speech(m)
                m = visible or m
                if not text.strip():
                    if PetEngine._last_assistant_same_as(None, m, store):
                        logger.info("[Pet] 主动思考产出与上一条重复，跳过落库")
                        # 跳过 assistant 落库，但任由用户历史（如有）不受影响
                        return
                store.add_history("assistant", m)
                persisted = True
                self.last_reply = {"time": time.time(), "text": m}
                logger.info(f"[Pet] 思考产出: {m[:40]}")
                # 自动提炼：仅用户主动对话的首个最终答案，工具观察不提炼
                if (not is_tool and not memory_scheduled and text.strip()
                        and self.config.get("memory_enabled", True)):
                    memory_scheduled = True
                    try:
                        from modules.desktop_pet.memory_bank import PetMemoryBank
                        pid = store.get_active_persona_id()
                        PetMemoryBank.get_instance().schedule_extract(
                            pid, text, m, self._get_memory_model_client(),
                        )
                    except Exception as _mem_err:
                        logger.debug(f"[Pet] 记忆提炼调度失败: {_mem_err}")
                # 通知 SSE 层一轮完整回复已产出（前端结束"接收中"，准备下一轮）
                on_token("\x00ROUND_DONE\x00")
            except Exception as ex:
                logger.error(f"[Pet] 持久化失败: {ex}")

        async def _run_once():
            # 桌宠独立页面形态：SSE 请求即连接存在，不做 WS 握手检查
            return await self.loop.run(
                self, text,
                on_token=_on_token, on_reasoning=on_reasoning,
                on_message=on_message, extra_system=extra_system,
            )

        try:
            try:
                reply = await _run_once()
            except Exception as e:
                # 独立 API 鉴权/余额失败 → 本次请求临时回退主模型。
                # 仅在尚未产出/落库时重试，避免重复追加半截回复。
                if (not emitted and not persisted and self._has_independent_api()
                        and self._is_auth_or_balance_error(e)):
                    logger.warning(f"[Pet] 独立 API 不可用，临时回退主模型: {e}")
                    override = self._build_client({})
                    self._client_override = override
                    self.last_fallback_reason = "独立 API 不可用，已临时使用主模型"
                    try:
                        reply = await _run_once()
                    finally:
                        self._client_override = None
                        try:
                            from infra.model.config_fingerprint import close_client_session
                            close_client_session(override)
                        except Exception as _swallowed_err:
                            logger.debug(f"[_stream_task] close fallback client: {_swallowed_err}")
                else:
                    raise
            if reply:
                logger.info(f"[Pet] 流式对话完成: {text[:30]} → {reply[:60]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Pet] 流式任务失败: {e}")
            raise
        finally:
            # 用户消息先落库：模型报错/空回复时也不会让用户这句话消失
            if text and text.strip() and not user_saved:
                try:
                    from modules.desktop_pet.store import PetStore
                    PetStore.get_instance().add_history("user", text)
                except Exception as ex:
                    logger.error(f"[Pet] 用户消息持久化失败: {ex}")

    def _get_memory_model_client(self):
        """记忆提炼使用的模型：统一走主模型，避免桌宠独立 key 失效导致记忆不落库。"""
        if getattr(self, "_memory_client", None) is None:
            try:
                self._memory_client = self._build_client({})
            except Exception as e:
                logger.debug(f"[Pet] 记忆提炼模型初始化失败: {e}")
                return None
        return self._memory_client

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
            from modules.desktop_pet.store import PetStore
            # memory_enabled 开关：关闭时跳过记忆检索
            if self.config.get("memory_enabled", True):
                store = PetStore.get_instance()
                pid = store.get_active_persona_id()
                persona = store.get_persona(pid) or {}
                # 桌宠循环设计：记忆只返回 top2（最大两条），与 8k 上下文匹配
                mems = await PetMemory.get_instance().retrieve(
                    query=query or "用户与桌宠互动", max_results=2
                )
                if mems:
                    lines = ["【桌宠手动记忆】（独立记忆库，仅作参考）"]
                    for i, ev in enumerate(mems, 1):
                        try:
                            from datetime import datetime
                            date = datetime.fromtimestamp(float(ev.get("created_at", 0) or 0)).strftime("%Y-%m-%d")
                        except Exception:
                            date = "未知日期"
                        lines.append(f"  [{i}] (日期={date}) {str(ev.get('text', ''))[:120]}")
                    extras.append("\n".join(lines))

                # 独立事件记忆库 + 因果图（按人设隔离，自动提炼）
                try:
                    from modules.desktop_pet.memory_bank import PetMemoryBank
                    bank = PetMemoryBank.get_instance()
                    events = await bank.retrieve(
                        pid, query or "用户与桌宠互动", max_results=2
                    )
                    if events:
                        lines = [
                            f"【桌宠记忆库（自动提炼 · 人设：{persona.get('name', '')}）】"
                        ]
                        for ev in events:
                            fact = (
                                getattr(ev, "fact", "")
                                or getattr(ev, "thought", "")
                                or getattr(ev, "lesson", "")
                                or ""
                            ).strip()
                            if not fact:
                                continue
                            date = (getattr(ev, "time", "") or "")[:10]
                            lines.append(f"  - [{date}] {fact[:160]}")
                        if len(lines) > 1:
                            extras.append("\n".join(lines))
                    causal = bank.causal_context(pid, query or "", limit=4)
                    if causal:
                        extras.append(
                            "【因果关联（自动提炼 · 按人设隔离）】\n"
                            + "\n".join(f"  - {x}" for x in causal)
                        )
                except Exception as _mem_err:
                    logger.debug(f"[build_context] 事件记忆/因果图检索失败: {_mem_err}")
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
        # 发声规则：让模型自己决定哪部分说出口（<say> 外的都是内心独白）
        system = system + (
            "\n\n【发声规则】你有一个发声开关，由你自己控制：\n"
            "- 想让用户听到的句子，包在 <say>…</say> 里（会被语音朗读）；\n"
            "- 只在心里想、不打算说出口的（观察、回忆、计划、犹豫），不要加标签；\n"
            "- 用户正在跟你说话时，你可以直接整段回答（系统会朗读），不必加标签；\n"
            "- 自己安静思考时，只有 <say> 里的内容会被读出来，其余保持安静。\n"
            "示例：<say>你今天看起来好累，要不要休息一下？</say>（内心：他连着加班三天了。）"
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

    async def _after_reply(self, reply: str, user_turn: bool = True) -> None:
        """回复后：只朗读 <say> 内容 + 广播完整文本（气泡显示全部）

        发声规则（桌宠自己决定）：
        - 打了 <say> 标签 → 只读标签内的内容，其余是内心独白
        - 没打标签 + 用户刚说话 → 整段当回答读出来
        - 没打标签 + 自己思考 → 内心独白，不朗读
        """
        from modules.desktop_pet.speech import clean_for_tts, has_say, split_speech
        visible, spoken = split_speech(reply)
        if not has_say(reply):
            spoken = visible if user_turn else ""
        self.last_reply = {"time": time.time(), "text": visible or reply}
        tts_text = clean_for_tts(spoken)
        if tts_text:
            try:
                from modules.desktop_pet.voice import tts as pet_tts
                path = await asyncio.to_thread(pet_tts.synthesize, tts_text)
                player = getattr(self, "_player", None)
                if path and player is not None:
                    player.play_async(path)
            except Exception as e:
                logger.debug(f"[Pet] TTS 失败: {e}")
        await self.emit_reply(visible or reply)

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

class _AudioPlayer:
    """可打断的 TTS 播放器

    macOS 用 afplay 子进程（可以 terminate 实现「用户一开口就闭嘴」）；
    Windows/其他平台退回到系统默认播放器（不可打断）。
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._thread = None

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def play_async(self, path: str) -> None:
        if not path or not os.path.exists(path):
            return
        self.stop()
        self._thread = threading.Thread(target=self._play, args=(path,), name="pet-tts-play", daemon=True)
        self._thread.start()

    def _play(self, path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
                return
            import subprocess
            proc = subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with self._lock:
                self._proc = proc
            proc.wait()
        except Exception as e:
            logger.debug(f"[Pet] 播放失败: {e}")
        finally:
            with self._lock:
                self._proc = None

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception as e:
                logger.debug(f"[Pet] 停止播放失败: {e}")

def get_running_pet_engine() -> Optional[PetEngine]:
    """已经在跑的桌宠引擎；没启动就返回 None —— **不**顺手创建一个。

    端口被插件接管时「在跑的那个」归插件说了算，所以先问端口。但内置的 provider 就是
    ``_builtin_instance``，问它一次就当场建实例：这两个调用点都是高频只读轮询，凭空创建
    一个没绑事件总线的单例会吞掉后续 start() 的语音订阅。所以认得出 provider 还是内置
    那个时，直接读 ``_instance``。
    """
    from infra.tool_manager.service_registry import get_capability

    factory = get_capability("pet_engine")
    if factory is not None and factory != PetEngine._builtin_instance:
        return factory()
    return PetEngine._instance

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
