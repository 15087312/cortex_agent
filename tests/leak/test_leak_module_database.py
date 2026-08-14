"""泄漏测试 I：数据库场景泄漏（DB 连接/session 累积）

模块域: modules/database —— 模拟创建连接/session 对象未关闭，对象累积
预期: 检测系统报告 ⚠ 疑似内存泄漏
"""
import pytest

pytestmark = pytest.mark.leak

_CONNECTIONS: list = []


class _FakeSession:
    """模拟未关闭的 DB session 对象"""

    def __init__(self, idx: int):
        self._engine = f"sqlite:///tmp/db_{idx}.db"
        self._transactions = [{"op": "write", "ts": idx} for _ in range(20)]
        self._closed = False

    def close(self):
        self._closed = True


@pytest.mark.parametrize("i", range(60))
def test_db_sessions(i):
    for j in range(500):
        _CONNECTIONS.append(_FakeSession(i * 500 + j))  # 未 close 且被引用
