"""result_fusion ResultFusion.recall_and_fuse 测试"""
import pytest

from modules.memory.result_fusion import ResultFusion


class FakeDeep:
    def __init__(self, success=True, fallback=False):
        self.success = success
        self.fallback = fallback
        self.anchor = None
        self.causal_conclusion = "因果结论"
        self.causal_chains = []
        self.shared_factors = []
        self.supporting_events = []
        self.counter_examples = []
        self.error = ""


class FakeScheduler:
    def __init__(self, deep=None):
        self._deep = deep or FakeDeep()

    async def deep_recall(self, query, max_results, depth_level, task_type=""):
        return self._deep


class FakeEvent:
    def __init__(self, fact="浅层事件", importance=0.5):
        self.fact = fact
        self.importance = importance


async def test_recall_and_fuse_deep_success():
    fusor = ResultFusion(scheduler=FakeScheduler(FakeDeep(success=True)))
    out = await fusor.recall_and_fuse("为什么项目延期")
    assert "因果结论" in out


async def test_recall_and_fuse_deep_fallback_shallow():
    fusor = ResultFusion(scheduler=FakeScheduler(FakeDeep(fallback=True)))
    out = await fusor.recall_and_fuse("普通问题", shallow_events=[FakeEvent()])
    assert "浅层事件" in out


async def test_recall_and_fuse_no_trigger_uses_shallow():
    fusor = ResultFusion(scheduler=FakeScheduler())
    out = await fusor.recall_and_fuse("今天天气不错", shallow_events=[FakeEvent("天气事件")])
    assert "天气事件" in out


async def test_recall_and_fuse_no_shallow_returns_empty():
    fusor = ResultFusion(scheduler=FakeScheduler(FakeDeep(fallback=True)))
    assert await fusor.recall_and_fuse("普通问题") == ""


async def test_recall_and_fuse_deep_failure_no_shallow():
    fusor = ResultFusion(scheduler=FakeScheduler(FakeDeep(success=False, fallback=False)))
    assert await fusor.recall_and_fuse("为什么", shallow_events=None) == ""
