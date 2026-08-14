"""泄漏测试 G：感知模块场景泄漏（事件订阅/队列累积）

模块域: modules/perception —— 模拟事件订阅 handler、感知事件队列无界累积
预期: 检测系统报告 ⚠ 疑似内存泄漏
"""
import pytest

pytestmark = pytest.mark.leak

# 模拟 PerceptionEventBus 的订阅表 / 事件队列无界增长
_SUBSCRIPTIONS: dict = {}
_EVENT_QUEUE: list = []


@pytest.mark.parametrize("i", range(60))
def test_perception_subs_and_events(i):
    # 订阅累积
    for j in range(2000):
        _SUBSCRIPTIONS[f"sub_{i}_{j}"] = {"handler": lambda: None, "event_type": "screen"}
    # 感知事件队列累积
    for j in range(2000):
        _EVENT_QUEUE.append({"type": "screen_diff", "payload": {"ts": i, "idx": j}})
