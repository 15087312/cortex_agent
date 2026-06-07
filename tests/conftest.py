"""
共享测试 fixtures
"""
import pytest
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    """提供测试用 MemoryManager 实例"""
    from modules.memory.core.memory_manager import MemoryManager
    return MemoryManager()
