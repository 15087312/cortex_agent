"""
Tests for identity system — model identities and capability tables.
"""
import pytest
from modules.thinking.identity import (
    ModelIdentity,
    ModelTier,
    DEFAULT_IDENTITIES,
    build_expert_capability_list,
    build_supervisor_capability_list,
    list_persistent_experts,
)


class TestDefaultIdentities:
    def test_large_exists(self):
        assert "large" in DEFAULT_IDENTITIES

    def test_all_have_identity_key(self):
        for key, ident in DEFAULT_IDENTITIES.items():
            assert hasattr(ident, 'identity_key') or isinstance(ident, dict)


class TestCapabilityTables:
    def test_expert_list_string(self):
        s = build_expert_capability_list()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_supervisor_list_string(self):
        s = build_supervisor_capability_list()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_persistent_experts(self):
        experts = list_persistent_experts()
        assert isinstance(experts, list)
