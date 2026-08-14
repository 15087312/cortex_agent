"""泄漏测试 E：全局缓存泄漏（模块级 dict 缓存无界增长）

类型: 缓存型泄漏——LRU 缺失/无界缓存，键持续增长
预期: 检测系统报告 ⚠ 疑似内存泄漏
"""
import pytest

pytestmark = pytest.mark.leak

_CACHE: dict = {}  # 模拟模块级无界缓存


@pytest.mark.parametrize("i", range(80))
def test_global_cache(i):
    for j in range(3000):
        _CACHE[f"key_{i}_{j}"] = {"data": j, "text": "cached" * 8}
