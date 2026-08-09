"""result_fusion 测试：结果格式化"""
from modules.memory.result_fusion import format_retrieve_result, format_deep_recall_result


class FakeEvent:
    def __init__(self, fact="测试事件", importance=0.8):
        self.fact = fact
        self.importance = importance


class FakeChain:
    class Node:
        def __init__(self, label):
            self.label = label

    def __init__(self, labels, direction="forward", confidence=0.9):
        self.nodes = [self.Node(l) for l in labels]
        self.direction = direction
        self.confidence = confidence


def _result(success=True, fallback=False):
    class R:
        pass
    r = R()
    r.anchor = None
    r.success = success
    r.fallback = fallback
    r.causal_conclusion = "结论"
    r.causal_chains = []
    r.shared_factors = []
    r.supporting_events = []
    r.counter_examples = []
    return r


def test_format_retrieve_result_empty():
    assert format_retrieve_result([]) == ""


def test_format_retrieve_result_with_events():
    out = format_retrieve_result([FakeEvent()], max_events=5)
    assert "测试事件" in out
    assert "重要性" in out


def test_format_deep_recall_result_success():
    r = _result()
    out = format_deep_recall_result(r)
    assert "结论" in out
    assert "无" in out  # 无链路/无佐证


def test_format_deep_recall_result_full():
    r = _result()
    r.causal_chains = [FakeChain(["A", "B"], direction="forward")]
    r.shared_factors = ["F1"]
    r.supporting_events = [FakeEvent("佐证")]
    r.counter_examples = [FakeEvent("反例")]
    out = format_deep_recall_result(r)
    assert "A → B" in out
    assert "F1" in out
    assert "佐证" in out
    assert "反例" in out


def test_format_deep_recall_result_fail_and_fallback():
    assert format_deep_recall_result(_result(success=False)) == ""
    assert format_deep_recall_result(_result(fallback=True)) == ""
