"""泄漏测试 H：模型客户端场景泄漏（client/会话实例累积）

模块域: infra/model —— 模拟反复创建模型 client/session 实例不释放
预期: 检测系统报告 ⚠ 疑似内存泄漏
"""
import pytest

pytestmark = pytest.mark.leak

_INSTANCES: list = []


class _FakeModelClient:
    """模拟模型客户端：持配置、会话、统计等对象"""

    def __init__(self, idx: int):
        self.model_name = "large"
        self.api_url = "http://localhost:1/v1"
        self.api_key = "test-key"
        self.max_tokens = 4096
        self.messages = [{"role": "user", "content": f"msg {idx}"} for _ in range(50)]
        self._stats = {"calls": idx, "tokens": idx * 100}


@pytest.mark.parametrize("i", range(60))
def test_model_client_instances(i):
    for j in range(200):
        _INSTANCES.append(_FakeModelClient(i * 200 + j))
