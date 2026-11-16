"""ModelRunner 思考内核双轨平替测试 (Phase 1 验收)

验证 ModelRunner 能够根据配置开关平滑在 ContinuousThinker 和 WorkflowThinker 之间切换，
且契约、属性、生命周期完全一致。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import settings
from modules.thinking.core.continuous_thinker import ContinuousThinker
from modules.thinking.core.model_runner import ModelRunner
from modules.thinking.core.workflow_thinker import WorkflowThinker


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_model_runner():
    runner = ModelRunner.__new__(ModelRunner)
    runner.model_id = "test_large_01"
    runner.tier = "large"
    runner.session_id = "test_session_dual"
    runner.MAX_ROUNDS = 5
    runner._thinker = None
    runner._task_id = "task_01"
    runner._task_description = "测试双轨思考"
    runner._return_to_model_id = None
    runner._return_to_session_id = None
    runner.identity_key = "large_primary"
    runner._build_runner_prompt = AsyncMock(return_value="test_prompt")
    runner.blackboard = MagicMock()
    runner._generate = AsyncMock(return_value="思考完毕")
    runner._running = False
    return runner


def test_model_runner_defaults_to_continuous(fake_model_runner, monkeypatch):
    """验证默认配置下使用 ContinuousThinker"""
    monkeypatch.setattr(settings, "THINKING_ENGINE_MODE", "continuous")
    
    # 触发 _think_loop 的初始化部分（通过局部运行）
    async def init_runner():
        # mock _think_loop 后续循环，仅测试 thinker 创建
        with patch.object(ContinuousThinker, "continuous_think", AsyncMock(return_value=[])):
            fake_model_runner._running = False  # 启动后立刻退出
            await fake_model_runner._think_loop()

    _run(init_runner())
    assert isinstance(fake_model_runner._thinker, ContinuousThinker)
    assert fake_model_runner._thinker.max_rounds == 5
    assert fake_model_runner._thinker.min_rounds == 1


def test_model_runner_switches_to_workflow(fake_model_runner, monkeypatch):
    """验证开启 workflow 模式后平滑切换为 WorkflowThinker"""
    monkeypatch.setattr(settings, "THINKING_ENGINE_MODE", "workflow")

    async def init_runner():
        with patch.object(WorkflowThinker, "continuous_think", AsyncMock(return_value=[])):
            fake_model_runner._running = False
            await fake_model_runner._think_loop()

    _run(init_runner())
    assert isinstance(fake_model_runner._thinker, WorkflowThinker)
    assert fake_model_runner._thinker.max_rounds == 5
    assert fake_model_runner._thinker.min_rounds == 1


def test_workflow_thinker_runner_lifecycle_contract(fake_model_runner, monkeypatch):
    """验证 WorkflowThinker 具备 ModelRunner 所需的全部接口契约与生命周期方法"""
    monkeypatch.setattr(settings, "THINKING_ENGINE_MODE", "workflow")

    async def test_contract():
        with patch.object(WorkflowThinker, "continuous_think", AsyncMock(return_value=[])):
            fake_model_runner._running = False
            await fake_model_runner._think_loop()

        thinker = fake_model_runner._thinker
        assert thinker is not None

        # 1. 委托与控制调用
        thinker.record_delegation("expert", "编写代码", {"task_id": "t1", "success": True})
        assert "t1" in thinker._pending_delegations

        thinker._process_delegation_response("代码编写完成", "t1")
        assert thinker._pending_delegations["t1"]["status"] == "completed"

        thinker.record_control_decision({"continue": False})
        assert thinker._last_control_data == {"continue": False}

        # 2. 上下文与外部引导
        thinker._context_tokens = 500
        assert thinker._context_tokens == 500
        thinker.add_external_prompt("系统引导")
        assert len(thinker.get_external_prompts()) == 1

        # 3. 唤醒重置与关闭
        thinker.history_thoughts = ["思考1", "思考2", "思考3", "思考4", "思考5", "思考6"]
        thinker.reset_for_continuation()
        assert len(thinker.history_thoughts) == 5  # 保留最近 5 条

        await thinker.close()
        assert len(thinker.get_external_prompts()) == 0

    _run(test_contract())
