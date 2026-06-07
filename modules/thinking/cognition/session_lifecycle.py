"""
会话生命周期管理 — 显式状态机，替代 SessionManager 的隐式 reset 逻辑

设计原则：
- 每个 SessionLifecycle 管理一个 session 的完整生命周期
- TurnContext + CognitiveBlackboard 都由 SessionLifecycle 管理
- 状态转移显式且可追踪
- 自动处理资源清理（shutdown_all 的竞态问题消失）
"""

import threading
import time
from typing import Optional, Any
from .turn_context import TurnContext, TurnState
from .blackboard import CognitiveBlackboard
from utils.logger import setup_logger

logger = setup_logger("session_lifecycle")


class SessionLifecycle:
    """
    会话生命周期管理

    替代：
    - SessionManager._reset_session_for_new_turn()（隐式 reset）
    - Session 对象（参与者、runner_manager 追踪）
    - 复杂的 shutdown + wait 逻辑

    设计：
    - 显式状态机（idle → planning → executing → integrating → complete）
    - 每轮对话自动清理上一轮状态
    - 支持状态查询和生命周期监控
    """

    def __init__(self, session_id: str):
        self._session_id = session_id
        self._lock = threading.RLock()

        # ── 当前轮次的状态 ──
        self._turn_context: Optional[TurnContext] = None
        self._blackboard: Optional[CognitiveBlackboard] = None
        self._state = TurnState.IDLE

        # ── 资源追踪 ──
        self._active_runners: dict = {}  # runner_id → runner_info
        self._participants: set = set()  # 本轮参与的 model_id

        # ── 统计 ──
        self._total_turns: int = 0
        self._completed_turns: int = 0

        logger.info(f"[SessionLifecycle] 创建: session={session_id[:8]}")

    # ── 轮次管理 ──

    def start_turn(self, user_input: str) -> TurnContext:
        """
        开始新一轮对话

        步骤：
        1. 清理上一轮所有资源
        2. 创建新 TurnContext
        3. 创建新 CognitiveBlackboard
        4. 转移到 PLANNING 状态
        """
        # CONC-8: Wait for runners outside lock to avoid deadlock
        # Collect runners while holding lock, then release lock before waiting
        with self._lock:
            runners_to_wait = list(self._active_runners.values()) if self._active_runners else []

        # Wait for active runners outside the lock
        if runners_to_wait:
            logger.debug(f"[SessionLifecycle] 等待 {len(runners_to_wait)} 个 runner 完成...")
            done = threading.Event()

            def wait_runners():
                for runner_info in runners_to_wait:
                    if hasattr(runner_info, "wait"):
                        runner_info.wait(timeout=0.5)
                done.set()

            t = threading.Thread(target=wait_runners, daemon=True)
            t.start()
            done.wait(timeout=3.0)

        # Now re-acquire lock to cleanup and initialize new turn
        with self._lock:
            # 清理上一轮状态
            self._active_runners.clear()
            self._participants.clear()
            if self._blackboard:
                self._blackboard.clear_turn_state()

            # 创建新轮
            turn_id_obj = TurnContext(
                session_id=self._session_id,
                user_input=user_input,
            )
            self._turn_context = turn_id_obj
            self._blackboard = CognitiveBlackboard(
                session_id=self._session_id,
                turn_id=turn_id_obj.turn_id,
            )
            self._blackboard.set_goal(user_input)

            # 转移状态
            self._state = TurnState.PLANNING
            self._total_turns += 1

            logger.info(
                f"[SessionLifecycle] 新轮开始: turn={turn_id_obj.turn_id[:8]} "
                f"input_len={len(user_input)}"
            )
            return turn_id_obj

    def transition_to(self, new_state: TurnState) -> None:
        """显式转移到新状态"""
        with self._lock:
            old_state = self._state
            self._state = new_state
            if self._turn_context:
                self._turn_context.transition_to(new_state)
            logger.info(
                f"[SessionLifecycle] 状态转移: {old_state.value} → {new_state.value}"
            )

    def complete_turn(self) -> Optional[str]:
        """
        完成本轮对话

        返回最终回复内容，清理运行时状态
        """
        with self._lock:
            if not self._blackboard:
                return None

            response = self._blackboard.final_response or ""
            self._state = TurnState.COMPLETE
            if self._turn_context:
                self._turn_context.transition_to(TurnState.COMPLETE)
            self._completed_turns += 1

            logger.info(
                f"[SessionLifecycle] 轮次完成: turn={self._turn_context.turn_id[:8] if self._turn_context else '?'} "
                f"response_len={len(response)}"
            )
            return response

    def error_turn(self, error: str) -> None:
        """记录错误并转移到 ERROR 状态"""
        with self._lock:
            self._state = TurnState.ERROR
            if self._turn_context:
                self._turn_context.transition_to(TurnState.ERROR)
            logger.error(
                f"[SessionLifecycle] 轮次错误: turn={self._turn_context.turn_id[:8] if self._turn_context else '?'} "
                f"error={error}"
            )

    # ── 资源管理 ──

    def register_runner(self, runner_id: str, runner_info: Any) -> None:
        """注册活跃 runner（用于追踪和清理）"""
        with self._lock:
            self._active_runners[runner_id] = runner_info
            logger.debug(f"[SessionLifecycle] runner 注册: {runner_id}")

    def unregister_runner(self, runner_id: str) -> None:
        """注销 runner"""
        with self._lock:
            if runner_id in self._active_runners:
                del self._active_runners[runner_id]
                logger.debug(f"[SessionLifecycle] runner 注销: {runner_id}")

    def add_participant(self, model_id: str) -> None:
        """记录本轮参与者"""
        with self._lock:
            self._participants.add(model_id)

    # ── 查询接口 ──

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def turn_id(self) -> Optional[str]:
        """获取当前轮 turn_id"""
        with self._lock:
            return self._turn_context.turn_id if self._turn_context else None

    @property
    def turn_context(self) -> Optional[TurnContext]:
        """获取当前 TurnContext"""
        with self._lock:
            return self._turn_context

    @property
    def blackboard(self) -> Optional[CognitiveBlackboard]:
        """获取当前 CognitiveBlackboard"""
        with self._lock:
            return self._blackboard

    @property
    def state(self) -> TurnState:
        """获取当前状态"""
        with self._lock:
            return self._state

    @property
    def is_active(self) -> bool:
        """是否在处理中"""
        with self._lock:
            return self._state in (
                TurnState.PLANNING,
                TurnState.EXECUTING,
                TurnState.INTEGRATING,
            )

    @property
    def is_complete(self) -> bool:
        """是否已完成"""
        with self._lock:
            return self._state in (TurnState.COMPLETE, TurnState.ERROR)

    def get_status(self) -> dict:
        """获取会话状态摘要"""
        with self._lock:
            return {
                "session_id": self._session_id[:8],
                "state": self._state.value,
                "turn_id": self._turn_context.turn_id[:8] if self._turn_context else None,
                "is_active": self.is_active,
                "active_runners": len(self._active_runners),
                "participants": len(self._participants),
                "total_turns": self._total_turns,
                "completed_turns": self._completed_turns,
                "blackboard_status": self._blackboard.get_status() if self._blackboard else None,
            }

    def __repr__(self) -> str:
        return (
            f"SessionLifecycle("
            f"session_id={self._session_id[:8]}, "
            f"state={self._state.value}, "
            f"turns={self._completed_turns}/{self._total_turns})"
        )
