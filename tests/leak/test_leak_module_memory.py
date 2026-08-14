"""泄漏测试 F：记忆模块场景泄漏（记忆事件无界累积）

模块域: modules/memory —— 模拟记忆事件/黑板观测持续追加不清理
预期: 检测系统报告 ⚠ 疑似内存泄漏
"""
import pytest

pytestmark = pytest.mark.leak

# 模拟 EventStore/黑板观测的全局事件表无界增长
_EVENT_STORE: list = []


@pytest.mark.parametrize("i", range(60))
def test_memory_events(i):
    for j in range(3000):
        _EVENT_STORE.append({
            "event_id": f"ev_{i}_{j}",
            "type": "observation",
            "fact": f"事实内容-{i}-{j}",
            "thought": "思考过程" * 10,
            "importance": 0.5,
            "session_id": f"sess_{i}",
        })
