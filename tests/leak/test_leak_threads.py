"""泄漏测试 D：线程对象泄漏（已结束的线程对象被引用不回收）

类型: 线程/资源对象累积——threading.Thread 结束但对象被全局引用，无法 GC
预期: 检测系统报告 ⚠ 疑似内存泄漏
"""
import threading

import pytest

pytestmark = pytest.mark.leak

_LEAK: list = []


@pytest.mark.parametrize("i", range(60))
def test_threads(i):
    for _ in range(30):
        t = threading.Thread(target=lambda: None)  # 立即结束的线程
        t.start()
        t.join()
        _LEAK.append(t)  # 对象被引用，Thread 对象 + 其内部状态不被回收
