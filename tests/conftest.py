"""
共享测试 fixtures
"""
import os

# macOS 双 libomp 兜底（OMP: Error #15）：根因修复见 scripts/fix_macos_libomp.py，
# 此变量仅兜底未跑脚本的环境。必须在任何重库导入前设置。
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 测试关闭后台向量化 worker：EventStore 保存事件后会在后台线程延迟加载 embedding
# 模型并推理，与主线程并发触发双 libomp 段错误（见 docs/ERRORS_AND_FIXES.md §27）。
# 测试用按需加载即可，不需要后台异步向量化。
os.environ.setdefault("EMBEDDING_BACKGROUND_WORKER", "false")

import pytest
import sys

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session", autouse=True)
def block_real_native_libs():
    """unit 测试不真实加载重量级原生库。

    torch / transformers / onnxruntime / mlx-vlm 初始化会创建 OpenMP/线程池线程，
    与已加载的 faiss 等原生库双 OpenMP 冲突（见 docs/ERRORS_AND_FIXES.md §27），
    表现为测试进程随机 GIL 死锁（sample 确认：主线程卡 take_gil，libtorch_cpu
    线程持 GIL 死锁）。unit 测试这些库一律用假模块（sys.modules 注入）测分支。
    """
    for mod in (
        "torch",
        "transformers",
        "sentence_transformers",
        "mlx_vlm",
        "paddleocr",
        "rapidocr_onnxruntime",
    ):
        sys.modules[mod] = None
    yield
    for mod in (
        "torch",
        "transformers",
        "sentence_transformers",
        "mlx_vlm",
        "paddleocr",
        "rapidocr_onnxruntime",
    ):
        sys.modules.pop(mod, None)


@pytest.fixture(scope="session", autouse=True)
def register_capabilities():
    """注册业务能力到 infra 端口。

    生产环境由 api/main → bootstrap 注册；测试会话开始时统一注册，
    否则工具层 get_capability() 返回 None 会走降级分支。
    测试需要 mock 具体能力时：unregister_capability(name) + register_capability(name, fake)。
    """
    from bootstrap import register_business_capabilities
    register_business_capabilities()
    yield
    from infra.tool_manager.service_registry import _capabilities
    _capabilities.clear()


@pytest.fixture
def settings():
    """提供测试用 Settings 实例"""
    from config.settings import Settings
    return Settings(_env_file=None)


@pytest.fixture
def mock_model_runner():
    """模拟 ModelRunner"""
    from unittest.mock import MagicMock, AsyncMock
    runner = MagicMock()
    runner.is_running = False
    runner.model_id = "test-model"
    runner.config = MagicMock()
    runner.config.model_name = "test"
    runner.config.api_key = "test-key"
    runner.process_input = AsyncMock(return_value="test response")
    return runner


@pytest.fixture
def blackboard():
    """提供测试用 CognitiveBlackboard 实例"""
    from modules.thinking.cognition.blackboard import CognitiveBlackboard
    return CognitiveBlackboard(max_entries=100)


@pytest.fixture
def app_state():
    """提供测试用 AppState 实例"""
    from cli_tui.state import AppState
    return AppState(api_url="http://localhost:8080")


@pytest.fixture
def memory_manager():
    """提供测试用 MemoryManager 实例（新系统暂存根）"""
    return None
