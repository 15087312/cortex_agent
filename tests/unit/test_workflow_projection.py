"""工作流前端拓扑投影单元测试 (Phase 4 验收)

验证 WorkflowTopologyProjector 状态变更、session_projection 接入与拓扑快照。
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from modules.thinking.core.workflow_projection import (
    PROJECTION_KEY_WORKFLOW,
    WorkflowTopologyProjector,
)


def test_workflow_topology_projector_lifecycle():
    """验证工作流节点生命周期变迁正确更新拓扑快照"""
    projector = WorkflowTopologyProjector(session_id="test_sess_proj")
    nodes = ["prep", "inference", "tool_exec"]
    edges = [
        {"from": "prep", "to": "inference"},
        {"from": "inference", "to": "tool_exec"},
        {"from": "tool_exec", "to": "inference"},
    ]

    projector.set_topology(nodes=nodes, edges=edges)

    snap = projector.get_snapshot()
    assert snap["active_node"] is None
    assert len(snap["nodes"]) == 3
    assert snap["nodes"]["prep"]["status"] == "idle"
    assert len(snap["edges"]) == 3

    # 1. 节点启动
    with patch("modules.thinking.projections.emit_projection") as mock_emit:
        projector.on_node_start("prep")
        snap_start = projector.get_snapshot()
        assert snap_start["active_node"] == "prep"
        assert snap_start["nodes"]["prep"]["status"] == "running"
        assert mock_emit.call_count == 1
        args = mock_emit.call_args[0]
        assert args[0] == "test_sess_proj"
        assert args[1] == PROJECTION_KEY_WORKFLOW
        assert args[2]["active_node"] == "prep"

    # 2. 节点完成
    with patch("modules.thinking.projections.emit_projection") as mock_emit:
        projector.on_node_end("prep", duration_ms=150.5)
        snap_end = projector.get_snapshot()
        assert snap_end["active_node"] is None
        assert snap_end["nodes"]["prep"]["status"] == "completed"
        assert snap_end["nodes"]["prep"]["duration_ms"] == 150.5
        assert mock_emit.call_count == 1
        args = mock_emit.call_args[0]
        assert args[2]["nodes"]["prep"]["status"] == "completed"

    # 3. 节点挂起 (跨模型委托)
    with patch("modules.thinking.projections.emit_projection") as mock_emit:
        projector.on_node_suspended("inference", delegation_id="del_99")
        snap_sus = projector.get_snapshot()
        assert snap_sus["nodes"]["inference"]["status"] == "suspended"
        assert snap_sus["nodes"]["inference"]["delegation_id"] == "del_99"
        assert mock_emit.call_count == 1
        args = mock_emit.call_args[0]
        assert args[2]["nodes"]["inference"]["status"] == "suspended"


def test_default_engine_mode_is_valid(monkeypatch):
    """验证全局思考引擎配置有效且支持热切换为 workflow"""
    from config.settings import settings
    assert settings.THINKING_ENGINE_MODE in ("continuous", "workflow")
    monkeypatch.setattr(settings, "THINKING_ENGINE_MODE", "workflow")
    assert settings.THINKING_ENGINE_MODE == "workflow"


def test_workflow_thinker_emits_topology_projection_during_run():
    """验证 WorkflowThinker 运行 continuous_think 时自动触发拓扑节点投影"""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from modules.thinking.core.workflow_thinker import WorkflowThinker

    wt = WorkflowThinker(session_id="sess_auto_topo", think_fn=AsyncMock(return_value="完成"))
    wt._node_context_prep = AsyncMock()

    with patch("modules.thinking.projections.emit_projection") as mock_emit:
        asyncio.run(wt.continuous_think("测试拓扑", max_rounds=1))
        assert mock_emit.call_count >= 6  # prep/inference/control/finalize start & end
        keys = [c[0][1] for c in mock_emit.call_args_list]
        assert all(k == "workflow" for k in keys)
        last_snap = mock_emit.call_args_list[-1][0][2]
        assert last_snap["nodes"]["finalize"]["status"] == "completed"
