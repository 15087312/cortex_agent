"""
ContinuousThinker — simplified single-model thinking loop.

Flow per user message:
1. Retrieve relevant memories (shallow RAG or deep causal recall)
2. Build context (recent messages + memory context)
3. Compose system prompt + context
4. Run model (streaming to user via queue)
5. Post-session: extract memory events
"""
import asyncio
import threading
from typing import Dict

from modules.thinking.chat_light.model_runner import ModelRunner
from modules.thinking.chat_light.context_slicer import ContextSlicer
from modules.thinking.chat_light.prompt_composer import PromptComposer
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("continuous_thinker")


class ContinuousThinker:
    """Single-model thinking loop."""

    def __init__(self):
        self._runner = ModelRunner()
        self._slicer = ContextSlicer()
        self._composer = PromptComposer()
        # 每会话串行锁：防止新消息打断时旧 think 异步收尾乱序写回会话
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._session_locks_guard = threading.Lock()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        with self._session_locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _model_params(self) -> dict:
        """解析激活的编排大模型角色的 model_params，供请求覆盖全局默认温度/最大token。
        与 agent 模式一致（resolve_active_large_role）；纯对话此前忽略这些参数。"""
        try:
            from modules.thinking.multi_model_orchestrator import resolve_active_large_role
            from config.settings import settings as _cfg
            role = resolve_active_large_role()
            return _cfg.get_model_params(role) or {}
        except Exception:
            return {}

    async def think(
        self,
        session_id: str,
        user_message: str,
        message_queue: asyncio.Queue,
    ) -> None:
        """Main thinking entry point.

        Args:
            session_id: Session ID
            user_message: User's message text
            message_queue: Async queue for streaming tokens to client
        """
        # 每会话串行：防止打断时旧 think 异步收尾与新一轮 think 并发写回会话导致乱序
        async with self._session_lock(session_id):
            try:
                # 1. Retrieve memories
                memory_context = await self._recall_memories(user_message, session_id)

                # 2. Build context — DB 为唯一真源直读（用户消息已由调用方入库）
                context_messages = await self._slicer.slice(
                    [], memory_context, session_id=session_id)

                # 3. Compose system prompt
                system_prompt = self._composer.build_system(memory_context)

                # 3.5 生成心理活动（conscience 内心独白）：注入 system prompt + 推送前端
                try:
                    from modules.thinking.conscience import get_conscience
                    from infra.model.small_model_client import SmallModelClient
                    from config.settings import settings
                    _cons = get_conscience()
                    _cons._model_client = SmallModelClient(
                        api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
                        api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
                    )
                    _cons.add_to_dialog("user", user_message, session_id=session_id or "large_primary")
                    _mental = await _cons.think(user_message, owner_id=session_id or "large_primary")
                    if _mental:
                        # 注入模型上下文（与 agent 模式同款：system prompt 追加过往经验段）
                        system_prompt += f"\n\n【你回忆起的过往经验】\n{_mental}"
                        try:
                            message_queue.put_nowait({
                                "type": "mental",
                                "content": _mental,
                                "identity_name": "总指挥",
                            })
                        except Exception:
                            pass
                except Exception:
                    pass

                # 3.6 环境感知注入（纯对话：感知系统实时采集 → PerceptionPool → 注入模型上下文）
                try:
                    from modules.thinking.context.sources.perception_source import PerceptionSource
                    _frag = await PerceptionSource().collect()
                    if _frag and _frag.content:
                        system_prompt += f"\n\n【环境感知】\n{_frag.content}"
                except Exception as e:
                    logger.debug(f"[ChatLight] 感知上下文收集失败: {e}")

                # 4. Stream response
                full_response = []

                def on_token(token: str):
                    full_response.append(token)
                    try:
                        message_queue.put_nowait({"type": "message", "content": token})
                    except asyncio.QueueFull:
                        pass

                # 尊重激活的编排角色 model_params（temperature/max_tokens），与 agent 模式一致：
                # 纯对话此前硬编码用全局 MODEL_MAX_TOKENS，编排页改的每角色参数从不生效。
                _mp = self._model_params()
                response = await self._runner.run(
                    messages=context_messages,
                    system_prompt=system_prompt,
                    on_token=on_token,
                    max_tokens=_mp.get("max_tokens"),
                    temperature=_mp.get("temperature"),
                )
                # 推送 deepseek 思考过程（reasoning_content）到前端思考区（走消息队列与回复同通道）
                try:
                    reasoning = (getattr(response.message, "reasoning_content", "") or "").strip()
                    if reasoning:
                        message_queue.put_nowait({
                            "type": "thinking",
                            "content": f"【思考】{reasoning}",
                            "identity_name": "总指挥",
                            "tier": "large",
                        })
                except Exception:
                    pass
                # 5. Assistant 响应由调用方（_consume_turn）保存到 DB
                #    此处不再写入，避免重复且无 id
                assistant_content = response.message.content or "".join(full_response)

                # 6. Signal completion
                await message_queue.put({"type": "done"})

                # 7. Post-session memory extraction (background)
                #    从 DB 取完整对话历史（DB 为唯一真源，含全量消息）
                asyncio.create_task(self._extract_memory(session_id))

            except Exception as e:
                logger.error(f"Thinking failed: {e}")
                await message_queue.put({"type": "error", "content": str(e)})

    async def _recall_memories(self, query: str, session_id: str) -> str:
        """Retrieve global event memory.

        仅当本会话已有历史对话时才注入全局事件记忆，避免新会话被无关历史污染。
        从 DB 取历史判断是否有对话（DB 含全量消息）。
        """
        try:
            # 从 DB 取历史，判断是否有过对话
            prior_msgs = []
            try:
                from modules.database.session_repo import get_session_repo
                db_msgs = get_session_repo().get_recent_messages(session_id, limit=5)
                prior_msgs = [
                    m for m in db_msgs
                    if m.get("role") in ("user", "assistant") and m.get("content")
                ]
            except Exception as e:
                logger.debug(f"[历史对话检查] 失败: {e}")
            if not prior_msgs:
                return ""

            from modules.memory.event_retrieval import get_event_retrieval
            from modules.memory.depth_recall import should_trigger_deep_recall
            from modules.memory.result_fusion import (
                format_deep_recall_result,
            )

            parts = []

            # 深度回忆（有历史对话时）
            try:
                trigger_deep, _ = should_trigger_deep_recall(query)
                if trigger_deep:
                    from modules.memory.depth_recall import DepthRecallScheduler
                    scheduler = DepthRecallScheduler()
                    deep_result = await scheduler.deep_recall(query, max_results=10)
                    if deep_result.success and not deep_result.fallback:
                        parts.append(format_deep_recall_result(deep_result))
            except Exception as e:
                logger.debug(f"Deep recall failed: {e}")

            # 浅层全局事件记忆（标注"曾经发生的事"，提示优先当前会话）
            try:
                retrieval = get_event_retrieval()
                global_events = await retrieval.retrieve(query, max_results=10)
                if global_events:
                    lines = [
                        "【曾经发生的事】",
                        "（以下为曾经发生的事，是过去的历史事件记忆，仅供参考。"
                        "回答请优先基于当前会话的【对话历史】进行，不要把下列过去的任务当作当前任务执行）",
                    ]
                    for i, ev in enumerate(global_events, 1):
                        date = str(ev.time or "")[:10] or "未知日期"
                        lines.append(f"  [{i}] (日期={date}, 重要性={ev.importance:.0%}) {ev.fact}")
                        if ev.lesson:
                            lines.append(f"     经验: {ev.lesson}")
                    parts.append("\n".join(lines))
            except Exception as e:
                logger.debug(f"Shallow recall failed: {e}")

            return "\n\n".join(p for p in parts if p and p.strip())

        except Exception as e:
            logger.debug(f"Memory recall failed: {e}")
            return ""

    async def _extract_memory(self, session_id: str) -> None:
        """Post-session memory extraction（从 DB 取完整对话，不依赖黑板窗口）。"""
        # 纯对话路径的门控与 agent 模式保持一致：UI「记忆总结」开关（MEMORY_SUMMARY_ENABLED）
        # 同样生效；同时尊重此前的 MEMORY_REDUCE_ENABLED（隐藏配置，默认 True）。
        if not settings.MEMORY_REDUCE_ENABLED or not settings.MEMORY_SUMMARY_ENABLED:
            return

        try:
            from modules.database.session_repo import get_session_repo
            msgs = get_session_repo().get_messages(session_id, limit=200)
            conversation_parts = [
                f"{m.get('role', '')}: {m.get('content', '')}"
                for m in msgs
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]

            conversation_text = "\n".join(conversation_parts)

            if len(conversation_text.strip()) < 50:
                return

            from modules.memory.event_reducer import EventReducer
            # 复用 ContinuousThinker 单例内 ModelRunner 的 client，避免每轮新建 aiohttp session 泄漏
            reducer = EventReducer(model_client=self._runner.client)
            events = await reducer.reduce(session_id, conversation_text)

            if events:
                logger.info(f"Extracted {len(events)} memory events from session {session_id[:12]}...")

        except Exception as e:
            logger.warning(f"Memory extraction failed: {e}")

    @staticmethod
    def _is_new_topic(new_msg, history):
        """Compare keyword overlap with last user message. <30% = new topic."""
        import re
        last_user = ''
        for m in reversed(history):
            if m.get('role') == 'user':
                last_user = m.get('content', '')
                break
        if not last_user:
            return False
        def words(text):
            return set(re.findall(r'[\u4e00-\u9fff]{2,}', text.lower()))
        a = words(new_msg)
        b = words(last_user)
        if not a or not b:
            return False
        return len(a & b) / max(len(a), 1) < 0.3
