"""process_collector 测试：思考过程收集器 / 工厂"""
from modules.thinking.core.process_collector import (
    InMemoryThinkingProcessCollector,
    DefaultThinkingProcessCollectorFactory,
    create_thinking_process_collector,
    get_thinking_process_collector_factory,
    set_thinking_process_collector_factory,
    ThinkingProcessSnapshot,
    ThinkingStepRecord,
)
from modules.thinking.core.control_tools import ThinkingControlDecision


def test_collector_flow():
    c = InMemoryThinkingProcessCollector()
    c.reset(session_id="s1", model_id="m1", tier="large")
    c.record_step(round_num=1, content="第一步", duration_ms=10.5, metadata={"k": "v"})
    c.record_step(round_num=2, content="第二步")
    snap = c.complete(final_result="结果", control_decision=ThinkingControlDecision(
        should_continue=False, reason="完成", result_summary="总结",
    ))
    assert snap.session_id == "s1"
    assert len(snap.steps) == 2
    assert snap.steps[0].duration_ms == 10.5
    assert snap.steps[0].metadata == {"k": "v"}
    assert snap.final_result == "结果"
    assert snap.stopped_by_continue_false is True
    assert snap.metadata["control_reason"] == "完成"


def test_collector_complete_no_decision():
    c = InMemoryThinkingProcessCollector()
    c.reset(session_id="s", model_id="m", tier="expert")
    c.record_step(round_num=1, content="x")
    snap = c.complete(final_result="r", metadata={"extra": 1})
    assert snap.stopped_by_continue_false is False
    assert snap.metadata["extra"] == 1


def test_collector_snapshot_copies():
    c = InMemoryThinkingProcessCollector()
    c.reset(session_id="s", model_id="m", tier="large")
    c.record_step(round_num=1, content="x")
    snap1 = c.snapshot()
    c.record_step(round_num=2, content="y")
    snap2 = c.snapshot()
    assert len(snap1.steps) == 1  # snapshot 不共享可变列表
    assert len(snap2.steps) == 2


def test_factory():
    f = DefaultThinkingProcessCollectorFactory()
    c = f.create_collector()
    assert isinstance(c, InMemoryThinkingProcessCollector)


def test_set_get_factory():
    class FakeFactory:
        def create_collector(self):
            return InMemoryThinkingProcessCollector()

    original = get_thinking_process_collector_factory()
    try:
        set_thinking_process_collector_factory(FakeFactory())
        assert isinstance(get_thinking_process_collector_factory(), FakeFactory)
        assert isinstance(create_thinking_process_collector(), InMemoryThinkingProcessCollector)
    finally:
        set_thinking_process_collector_factory(original)


def test_snapshot_dataclasses():
    rec = ThinkingStepRecord(round_num=1, content="x", duration_ms=2.0)
    assert rec.round_num == 1
    snap = ThinkingProcessSnapshot(session_id="s", model_id="m", tier="t", task_context=None, steps=[rec])
    assert snap.final_result == ""
    assert snap.control_decision is None
