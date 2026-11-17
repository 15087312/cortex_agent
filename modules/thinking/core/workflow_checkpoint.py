"""工作流状态持久化与检查点管理器 (Workflow Checkpoint Manager)

解决原系统中在内存死等 (in-memory wait_for_wakeup)、进程重启即丢状态的问题。
机制：
1. 节点完成或发起委托时，序列化状态快照落盘 (Checkpoint)
2. 发起跨模型委托时标记为 SUSPENDED，释放协程与线程资源
3. 外部事件（thinking_result）到达时，按 delegation_id/session_id 唤醒并加载状态 (Resume)
4. 即使系统进程发生重启，仍能从 Checkpoint DB/文件完整自愈恢复
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger(__name__)


class CheckpointStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class WorkflowCheckpoint:
    """工作流检查点数据模型"""
    checkpoint_id: str
    session_id: str
    model_id: str
    node_id: str
    status: CheckpointStatus
    state: Dict[str, Any]
    waiting_delegation_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkflowCheckpoint:
        data = dict(data)
        data["status"] = CheckpointStatus(data["status"])
        return cls(**data)


class WorkflowCheckpointManager:
    """检查点持久化存储与管理（支持文件级持久化，重启自愈）"""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir is None:
            # 默认持久化至 data/checkpoints
            self.base_dir = (
                Path(__file__).resolve().parents[3] / "data" / "checkpoints"
            )
        else:
            self.base_dir = Path(base_dir)

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[str, WorkflowCheckpoint] = {}
        self._load_from_disk()

    def _get_path(self, checkpoint_id: str) -> Path:
        return self.base_dir / f"{checkpoint_id}.json"

    def _load_from_disk(self) -> None:
        """启动时从磁盘扫描恢复已有检查点"""
        try:
            for p in self.base_dir.glob("*.json"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        cp = WorkflowCheckpoint.from_dict(data)
                        self._memory_cache[cp.checkpoint_id] = cp
                except Exception as e:
                    logger.warning(f"[Checkpoint] 读取文件 {p} 失败: {e}")
        except Exception as e:
            logger.warning(f"[Checkpoint] 扫描磁盘目录失败: {e}")

    def save_checkpoint(
        self,
        session_id: str,
        model_id: str,
        node_id: str,
        state: Dict[str, Any],
        status: CheckpointStatus = CheckpointStatus.ACTIVE,
        waiting_delegation_id: Optional[str] = None,
    ) -> WorkflowCheckpoint:
        """保存或覆盖当前会话模型的检查点"""
        checkpoint_id = f"{session_id}_{model_id}"
        cp = WorkflowCheckpoint(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            model_id=model_id,
            node_id=node_id,
            status=status,
            state=state,
            waiting_delegation_id=waiting_delegation_id,
            updated_at=time.time(),
        )
        self._memory_cache[checkpoint_id] = cp

        # 写入持久化磁盘
        try:
            path = self._get_path(checkpoint_id)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cp.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"[Checkpoint] 已持久化检查点: {checkpoint_id} (status={status.value})")
        except Exception as e:
            logger.error(f"[Checkpoint] 写入磁盘失败: {e}")

        return cp

    def suspend(
        self,
        session_id: str,
        model_id: str,
        node_id: str,
        state: Dict[str, Any],
        waiting_delegation_id: str,
    ) -> WorkflowCheckpoint:
        """将工作流挂起等待外部委托结果"""
        return self.save_checkpoint(
            session_id=session_id,
            model_id=model_id,
            node_id=node_id,
            state=state,
            status=CheckpointStatus.SUSPENDED,
            waiting_delegation_id=waiting_delegation_id,
        )

    def resume(
        self,
        waiting_delegation_id: str,
        delegation_result: Any,
    ) -> Optional[WorkflowCheckpoint]:
        """根据委托 ID 寻找挂起的工作流并唤醒"""
        target_cp: Optional[WorkflowCheckpoint] = None
        for cp in self._memory_cache.values():
            if (
                cp.status == CheckpointStatus.SUSPENDED
                and cp.waiting_delegation_id == waiting_delegation_id
            ):
                target_cp = cp
                break

        if not target_cp:
            logger.warning(f"[Checkpoint] 未找到等待委托 {waiting_delegation_id} 的挂起工作流")
            return None

        # 唤醒：将外部结果回填入 state，状态改为 ACTIVE
        target_cp.status = CheckpointStatus.ACTIVE
        target_cp.state["last_delegation_result"] = delegation_result
        target_cp.waiting_delegation_id = None
        target_cp.updated_at = time.time()

        # 重新持久化
        try:
            path = self._get_path(target_cp.checkpoint_id)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(target_cp.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"[Checkpoint] 成功唤醒工作流: {target_cp.checkpoint_id} (delegation={waiting_delegation_id})")
        except Exception as e:
            logger.error(f"[Checkpoint] 写入磁盘失败: {e}")

        return target_cp

    def get_checkpoint(self, checkpoint_id: str) -> Optional[WorkflowCheckpoint]:
        return self._memory_cache.get(checkpoint_id)

    def list_suspended(self) -> List[WorkflowCheckpoint]:
        return [cp for cp in self._memory_cache.values() if cp.status == CheckpointStatus.SUSPENDED]

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        self._memory_cache.pop(checkpoint_id, None)
        path = self._get_path(checkpoint_id)
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception as e:
                logger.warning(f"[Checkpoint] 删除文件失败: {e}")
        return False


_default_checkpoint_manager: Optional[WorkflowCheckpointManager] = None


def get_checkpoint_manager() -> WorkflowCheckpointManager:
    """获取进程级共享的工作流检查点管理器单例"""
    global _default_checkpoint_manager
    if _default_checkpoint_manager is None:
        _default_checkpoint_manager = WorkflowCheckpointManager()
    return _default_checkpoint_manager
