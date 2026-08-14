"""泄漏测试 A：对象累积泄漏（全局 list/dict 无界增长）

类型: 对象引用泄漏（最常见的 Python 泄漏）
预期: 检测系统报告 ⚠ 疑似内存泄漏（每测试新增存活对象/字节持续增长）
"""
import pytest

pytestmark = pytest.mark.leak

_LEAK: list = []  # 全局累积，模拟模块级无界缓存


@pytest.mark.parametrize("i", range(80))
def test_object_growth(i):
    for _ in range(5000):
        _LEAK.append({f"key_{i}": i, "payload": "x" * 64})
