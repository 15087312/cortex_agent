"""连续思考内核 (v0.2.0 工作流版)

v0.1.0 传统阻塞循环实现已归档至 git tag `v0.1.0`。
自 v0.2.0 起，ContinuousThinker 由 WorkflowThinker 显式状态机驱动，
对外 100% 保持历史接口、静态方法与测试兼容。
"""
from __future__ import annotations

import asyncio  # noqa: F401 - 测试会 monkeypatch continuous_thinker.asyncio.sleep
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from utils.suspension import pausable_wait_for  # noqa: F401 - 供测试 monkeypatch 兼容
from modules.thinking.core.workflow_thinker import (  # noqa: F401
    WorkflowThinker,
    WorkflowThinkingState,
)
from modules.thinking.core.control_tools import (  # noqa: F401
    ThinkingControlDecision,
    ThinkingTaskContext,
)
from modules.thinking.core.delegation_port import DelegationPort
from modules.thinking.core.process_collector import ThinkingProcessCollector

logger = logging.getLogger(__name__)


class ContinuousThinker(WorkflowThinker):
    """连续思考内核 (v0.2.0 工作流实现)"""

    def __init__(
        self,
        think_fn: Optional[Callable[[str], Awaitable[str]]] = None,
        max_rounds: int = 30,
        min_rounds: int = 1,
        interval: float = 3.0,
        memory_manager: Optional[Any] = None,
        session_id: Optional[str] = None,
        tool_validator: Optional[Callable[[Dict[str, Any], str], Awaitable[Any]]] = None,
        gcm_pool: Optional[Any] = None,
        model_id: str = "",
        tier: str = "large",
        task_context: Optional[ThinkingTaskContext] = None,
        prompt_builder: Optional[Callable[..., Any]] = None,
        process_collector: Optional[ThinkingProcessCollector] = None,
        delegation_port: Optional[DelegationPort] = None,
        blackboard: Optional[Any] = None,
        runner_ref: Optional[Any] = None,
        role: str = "orchestrator",
        context_window_size: int = 0,
        initial_question: str = "",
        on_token_callback: Optional[Callable] = None,
        prompt_composer: Optional[Any] = None,
    ) -> None:
        super().__init__(
            think_fn=think_fn,
            max_rounds=max_rounds,
            min_rounds=min_rounds,
            interval=interval,
            memory_manager=memory_manager,
            session_id=session_id,
            tool_validator=tool_validator,
            gcm_pool=gcm_pool,
            model_id=model_id,
            tier=tier,
            task_context=task_context,
            prompt_builder=prompt_builder,
            process_collector=process_collector,
            delegation_port=delegation_port,
            blackboard=blackboard,
            runner_ref=runner_ref,
        )
        self._role = role
        self._context_window_size = context_window_size
        self._initial_question = initial_question
        self._on_token_callback = on_token_callback
        self._prompt_composer = prompt_composer


__all__ = ["ContinuousThinker", "WorkflowThinker", "WorkflowThinkingState"]
