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
import json
from typing import Optional

from backend.chat.model_runner import ModelRunner
from backend.chat.context_slicer import ContextSlicer
from backend.chat.blackboard import Blackboard
from backend.config.prompts.composer import PromptComposer
from backend.config.settings import settings
from backend.utils.logger import setup_logger

logger = setup_logger("continuous_thinker")


class ContinuousThinker:
    """Single-model thinking loop."""

    def __init__(self):
        self._runner = ModelRunner()
        self._slicer = ContextSlicer()
        self._blackboard = Blackboard()
        self._composer = PromptComposer()

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

            # 5. Store assistant response
            assistant_content = response.message.content or "".join(full_response)
            self._blackboard.add_message(session_id, "assistant", assistant_content)

            # 6. Signal completion
            await message_queue.put({"type": "done"})

            # 7. Post-session memory extraction (background)
            asyncio.create_task(self._extract_memory(session_id, context_messages))

        except Exception as e:
            logger.error(f"Thinking failed: {e}")
            await message_queue.put({"type": "error", "content": str(e)})

    async def _recall_memories(self, query: str, session_id: str) -> str:
        """Retrieve relevant memories via hybrid RAG."""
        try:
            from backend.memory.event_retrieval import get_event_retrieval
            from backend.memory.depth_recall import should_trigger_deep_recall
            from backend.memory.result_fusion import format_retrieve_result

            retrieval = get_event_retrieval()

            # Check if deep recall should trigger
            trigger_deep, reason = should_trigger_deep_recall(query)

            if trigger_deep:
                try:
                    from backend.memory.depth_recall import DepthRecallScheduler
                    from backend.memory.result_fusion import format_deep_recall_result
                    scheduler = DepthRecallScheduler()
                    deep_result = await scheduler.deep_recall(query, max_results=10)
                    if deep_result.success and not deep_result.fallback:
                        return format_deep_recall_result(deep_result)
                except Exception as e:
                    logger.debug(f"Deep recall failed, falling back to shallow: {e}")

            # Shallow recall
            events = await retrieval.retrieve(query, max_results=10)
            return format_retrieve_result(events)

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

            from backend.memory.event_reducer import EventReducer
            from infra.model.model_client import get_model_client

            reducer = EventReducer(model_client=get_model_client())
            events = await reducer.reduce(session_id, conversation_text)

            if events:
                logger.info(f"Extracted {len(events)} memory events from session {session_id[:12]}...")

        except Exception as e:
            logger.debug(f"Memory extraction failed: {e}")

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
