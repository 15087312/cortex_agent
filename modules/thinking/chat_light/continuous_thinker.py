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
from modules.thinking.chat_light.blackboard import Blackboard
from modules.thinking.chat_light.prompt_composer import PromptComposer
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("continuous_thinker")


class ContinuousThinker:
    """Single-model thinking loop."""

    def __init__(self):
        self._runner = ModelRunner()
        self._slicer = ContextSlicer()
        self._blackboard = Blackboard()
        self._composer = PromptComposer()
        # 每会话串行锁：防止新消息打断时旧 think 异步收尾把 assistant 乱序写进黑板
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._session_locks_guard = threading.Lock()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        with self._session_locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

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
        # 每会话串行：防止打断时旧 think 异步收尾与新一轮 think 并发写黑板导致乱序
        async with self._session_lock(session_id):
            try:
                # 1. Retrieve memories
                memory_context = await self._recall_memories(user_message, session_id)

                self._blackboard.add_message(session_id, "user", user_message)
                history = self._blackboard.get_messages(session_id)
                context_messages = await self._slicer.slice(history, memory_context)

                # 3. Compose system prompt
                system_prompt = self._composer.build_system(memory_context)

                # 4. Stream response
                full_response = []

                def on_token(token: str):
                    full_response.append(token)
                    try:
                        message_queue.put_nowait({"type": "message", "content": token})
                    except asyncio.QueueFull:
                        pass

                response = await self._runner.run(
                    messages=context_messages,
                    system_prompt=system_prompt,
                    on_token=on_token,
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
                # 5. Store assistant response
                assistant_content = response.message.content or "".join(full_response)
                self._blackboard.add_message(session_id, "assistant", assistant_content)

                # 6. Signal completion
                await message_queue.put({"type": "done"})

                # 7. Post-session memory extraction (background)
                # 传完整对话历史（user + assistant），而非 slice 上下文（只含 user）
                full_history = self._blackboard.get_messages(session_id)
                asyncio.create_task(self._extract_memory(session_id, full_history))

            except Exception as e:
                logger.error(f"Thinking failed: {e}")
                await message_queue.put({"type": "error", "content": str(e)})

    async def _recall_memories(self, query: str, session_id: str) -> str:
        """Retrieve global event memory（会话记忆即历史对话，由 context_messages 注入）。

        仅当本会话已有历史对话时才注入全局事件记忆，避免新会话被无关历史污染。
        """
        try:
            # 会话已有历史对话（当前消息尚未入黑板，取到的是此前消息）
            prior_msgs = []
            try:
                if self._blackboard is not None:
                    prior_msgs = [
                        m for m in self._blackboard.get_messages(session_id)
                        if m.get("role") in ("user", "assistant") and m.get("content")
                    ]
            except Exception as e:
                logger.debug(f"[历史对话检查] 失败: {e}")
            # DB 兜底：Blackboard 是内存（断开清理/重启后为空），有持久历史也应视为有历史
            if not prior_msgs:
                try:
                    from modules.database.session_repo import get_session_repo
                    db_msgs = get_session_repo().get_recent_messages(session_id, limit=20)
                    prior_msgs = [
                        m for m in db_msgs
                        if m.get("role") in ("user", "assistant") and m.get("content")
                    ]
                except Exception:
                    pass
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

    async def _extract_memory(self, session_id: str, messages: list) -> None:
        """Post-session memory extraction."""
        if not settings.MEMORY_REDUCE_ENABLED:
            return

        try:
            # Build conversation text
            conversation_parts = []
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    conversation_parts.append(f"{role}: {content}")

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

    def get_blackboard(self) -> Blackboard:
        return self._blackboard
