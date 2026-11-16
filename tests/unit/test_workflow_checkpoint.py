"""工作流状态持久化与跨模型挂起唤醒单元测试 (Phase 3 验收)

验证 Checkpoint 磁盘持久化、跨模型委托挂起、事件驱动唤醒以及跨进程重启自愈。
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from modules.thinking.core.workflow_checkpoint import (
    CheckpointStatus,
    WorkflowCheckpoint,
    WorkflowCheckpointManager,
)
from modules.thinking.core.workflow_orchestration import DelegationWorkflowOrchestrator


@pytest.fixture
def temp_checkpoint_dir():
    temp_dir = tempfile.mkdtemp(prefix="test_checkpoints_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_checkpoint_save_and_disk_persistence(temp_checkpoint_dir):
    """验证工作流状态快照能够正确序列化并写入磁盘"""
    mgr = WorkflowCheckpointManager(base_dir=temp_checkpoint_dir)
    state_data = {
        "turn": 2,
        "thought": "正在准备委托代码专家",
        "messages": [{"role": "user", "content": "帮我写代码"}],
    }

    cp = mgr.save_checkpoint(
        session_id="sess_01",
        model_id="large_primary",
        node_id="model_inference",
        state=state_data,
        status=CheckpointStatus.ACTIVE,
    )

    assert cp.checkpoint_id == "sess_01_large_primary"
    assert cp.status == CheckpointStatus.ACTIVE
    assert cp.state["turn"] == 2

    # 验证物理磁盘文件存在
    target_file = temp_checkpoint_dir / "sess_01_large_primary.json"
    assert target_file.exists()


def test_suspend_and_resume_lifecycle(temp_checkpoint_dir):
    """验证委托发生时工作流被挂起 (SUSPENDED)，结果回填时被精准唤醒 (ACTIVE)"""
    mgr = WorkflowCheckpointManager(base_dir=temp_checkpoint_dir)
    orchestrator = DelegationWorkflowOrchestrator(checkpoint_mgr=mgr)

    # 1. 发起委托并挂起
    initial_state = {"prompt": "分析订单异常", "results": []}
    cp = orchestrator.suspend_for_delegation(
        session_id="sess_02",
        model_id="supervisor_01",
        current_node_id="tool_dispatch",
        state=initial_state,
        delegation_id="del_task_999",
        target_role="database_expert",
        task_desc="查询 SQL 慢日志",
    )

    assert cp.status == CheckpointStatus.SUSPENDED
    assert cp.waiting_delegation_id == "del_task_999"

    # 验证活跃挂起列表
    suspended_list = orchestrator.list_active_suspensions()
    assert len(suspended_list) == 1
    assert suspended_list[0].waiting_delegation_id == "del_task_999"

    # 2. 模拟专家完成任务，事件到达并唤醒
    resumed_cp = orchestrator.resume_from_delegation(
        delegation_id="del_task_999",
        result_content="已查明慢查询由于缺少索引导致",
        source_model_id="expert_db_01",
    )

    assert resumed_cp is not None
    assert resumed_cp.status == CheckpointStatus.ACTIVE
    assert resumed_cp.waiting_delegation_id is None
    assert "last_delegation_result" in resumed_cp.state
    assert "缺少索引" in resumed_cp.state["last_delegation_result"]["result"]

    # 唤醒后挂起列表应清空
    assert len(orchestrator.list_active_suspensions()) == 0


def test_process_restart_self_healing(temp_checkpoint_dir):
    """验证模拟系统进程崩溃重启后，挂起的工作流依然可以从磁盘自愈恢复"""
    # 阶段 1：进程 A 挂起工作流
    mgr_a = WorkflowCheckpointManager(base_dir=temp_checkpoint_dir)
    mgr_a.suspend(
        session_id="sess_crash_test",
        model_id="large_commander",
        node_id="eval_node",
        state={"goal": "长期复杂任务", "progress": 50},
        waiting_delegation_id="probe_del_888",
    )

    # 阶段 2：模拟进程崩溃（销毁 mgr_a），启动全新进程 B
    del mgr_a

    mgr_b = WorkflowCheckpointManager(base_dir=temp_checkpoint_dir)
    # 启动时自动从磁盘扫描恢复
    suspended = mgr_b.list_suspended()
    assert len(suspended) == 1
    assert suspended[0].checkpoint_id == "sess_crash_test_large_commander"
    assert suspended[0].waiting_delegation_id == "probe_del_888"

    # 阶段 3：进程 B 成功处理来自消息总线的延迟回复并唤醒
    resumed = mgr_b.resume("probe_del_888", {"status": "success", "data": "专家交付物"})
    assert resumed is not None
    assert resumed.status == CheckpointStatus.ACTIVE
    assert resumed.state["progress"] == 50
    assert resumed.state["last_delegation_result"]["data"] == "专家交付物"
