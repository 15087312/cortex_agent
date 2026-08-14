"""泄漏测试 C：引用循环泄漏（带 __del__ 的循环，GC 无法回收）

类型: 引用循环（uncollectable）——Python 循环 GC 收集器无法回收带 __del__ 的环
预期: 检测系统报告 ⚠ 疑似内存泄漏（存活对象持续增长）
"""
import pytest

pytestmark = pytest.mark.leak

_LEAK: list = []


class _Cycle:
    """自引用 + __del__：形成 GC 无法回收的环"""

    def __init__(self, i: int):
        self.self = self  # 自引用环
        self.i = i

    def __del__(self):
        pass  # 有 __del__ 的环不可被循环 GC 回收


@pytest.mark.parametrize("i", range(60))
def test_reference_cycle(i):
    for _ in range(2000):
        _LEAK.append(_Cycle(i))
