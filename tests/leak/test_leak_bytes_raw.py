"""泄漏测试 B：原始内存泄漏（bytes/bytearray 大块，非 GC 跟踪的引用对象）

类型: 原始内存泄漏（bytes/numpy buffer）——对象个数不变，字节持续增长
预期: 检测系统报告 ⚠ 疑似内存泄漏（每测试新增 KiB 持续增长）
"""
import pytest

pytestmark = pytest.mark.leak

_LEAK: list = []


@pytest.mark.parametrize("i", range(60))
def test_bytes_raw(i):
    _LEAK.append(b"x" * (1024 * 1024))  # 每测试 1MB bytes
