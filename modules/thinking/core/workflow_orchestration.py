"""跨模型委托工作流编排器 (Workflow Delegation Orchestrator)

实现 Large -> Supervisor -> Expert 的父子图任务调度，
结合 WorkflowCheckpointManager 实现挂起与事件驱动唤醒。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from modules.thinking.core.workflow_checkpoint import (
    CheckpointStatus,
    WorkflowCheckpoint,
    WorkflowCheckpointManager,
)
from utils.logger import setup_logger

logger = setup_logger(__name__)


class DelegationWorkflowOrchestrator:
    """跨模型委托工作流编排协调器"""

    def __init__(self, checkpoint_mgr: Optional[WorkflowCheckpointManager] = None) -> None:
        self.checkpoint_mgr = checkpoint_mgr or WorkflowCheckpointManager()

    def suspend_for_delegation(
        self,
        session_id: str,
        model_id: str,
        current_node_id: str,
        state: Dict[str, Any],
        delegation_id: str,
        target_role: str,
        task_desc: str,
    ) -> WorkflowCheckpoint:
        """上级模型发起委托时，保存现场并挂起"""
        state_to_save = dict(state)
        state_to_save["delegation_meta"] = {
            "target_role": target_role,
            "task_desc": task_desc,
            "delegation_id": delegation_id,
            "suspended_at": time.time(),
        }
        cp = self.checkpoint_mgr.suspend(
            session_id=session_id,
            model_id=model_id,
            node_id=current_node_id,
            state=state_to_save,
            waiting_delegation_id=delegation_id,
        )
        logger.info(
            f"[Orchestrator] 会话[{session_id[:8]}] 模型 {model_id} 已挂起，"
            f"等待专家 {target_role} 回传 (delegation_id={delegation_id})"
        )
        return cp

    def resume_from_delegation(
        self,
        delegation_id: str,
        result_content: str,
        source_model_id: str = "",
    ) -> Optional[WorkflowCheckpoint]:
        """专家完成任务产生结果时，精准唤醒挂起的工作流"""
        delegation_payload = {
            "delegation_id": delegation_id,
            "result": result_content,
            "source_model_id": source_model_id,
            "completed_at": time.time(),
        }
        cp = self.checkpoint_mgr.resume(
            waiting_delegation_id=delegation_id,
            delegation_result=delegation_payload,
        )
        if cp:
            logger.info(
                f"[Orchestrator] 成功唤醒主模型 {cp.model_id}，"
                f"回填委托结果 ({len(result_content)} 字符)"
            )
        return cp

    def list_active_suspensions(self) -> List[WorkflowCheckpoint]:
        return self.checkpoint_mgr.list_suspended()
