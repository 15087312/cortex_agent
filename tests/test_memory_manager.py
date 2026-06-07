"""
Tests for MemoryManager — instance caching and basic operations.
"""
import pytest
from unittest.mock import MagicMock, patch

from modules.memory.core.memory_manager import MemoryManager


class TestInstanceCache:
    def setup_method(self):
        MemoryManager.clear_cache()

    def test_get_instance_returns_same(self):
        a = MemoryManager.get_instance(model_id="test_model")
        b = MemoryManager.get_instance(model_id="test_model")
        assert a is b

    def test_different_model_id_different_instance(self):
        a = MemoryManager.get_instance(model_id="model_a")
        b = MemoryManager.get_instance(model_id="model_b")
        assert a is not b

    def test_default_model_id(self):
        a = MemoryManager.get_instance()
        b = MemoryManager.get_instance()
        assert a is b

    def test_clear_cache(self):
        a = MemoryManager.get_instance(model_id="clear_test")
        MemoryManager.clear_cache()
        b = MemoryManager.get_instance(model_id="clear_test")
        assert a is not b


class TestMemoryManagerInit:
    def test_has_core(self):
        mm = MemoryManager()
        assert hasattr(mm, 'core')
        assert hasattr(mm, 'short_term')
        assert hasattr(mm, 'long_term')

    def test_set_session_id(self):
        mm = MemoryManager()
        mm.set_session_id("session_123")
        assert mm.session_id == "session_123"

    def test_set_owner(self):
        mm = MemoryManager()
        mm.set_owner("model_1")
        assert mm.owner == "model_1"
