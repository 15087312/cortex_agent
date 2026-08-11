"""ModelRunnerManager 测试（此前整类零覆盖）——容量/命名/生命周期"""
import asyncio
from unittest.mock import MagicMock

import pytest

import modules.thinking.core.model_runner as mr_mod
from modules.thinking.core.model_runner import ModelRunnerManager


def _run(coro):
    return asyncio.run(coro)


class _FakeBus:
    def send(self, msg):
        pass


def _make_manager(monkeypatch, identities=None, permissions=None):
    """构造 manager，mock 依赖链"""
    import modules.thinking.communication.interface as iface_mod
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: _FakeBus())

    identities = identities or {
        "expert_implementer": {"tier": "expert", "model_id": "expert_implementer"},
        "supervisor_code": {"tier": "supervisor", "model_id": "supervisor_code"},
        "large_primary": {"tier": "large", "model_id": "large_primary"},
    }

    def _fake_get_identities():
        return identities

    class _FakeIdentity:
        def __init__(self, key):
            self.model_id = identities[key]["model_id"]
            self.tier = identities[key]["tier"]
            self.name = key
            self.role = key
            self.default_skill = ""

    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", _fake_get_identities)
    monkeypatch.setattr(ident_mod.ModelIdentity, "from_template", staticmethod(_FakeIdentity))

    perms = permissions or MagicMock()
    perms.max_concurrent_runners = 2
    monkeypatch.setattr(ident_mod, "get_permissions", lambda key: perms)

    factory = MagicMock()
    import modules.thinking.model_factory as mf_mod
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)

    # skill_manager mock
    import modules.thinking.skills as sk_mod
    skill_mgr = MagicMock()
    skill_mgr.get_skill.return_value = None
    monkeypatch.setattr(sk_mod, "skill_manager", skill_mgr)

    return ModelRunnerManager(session_id="s1"), factory


def _patch_runner(monkeypatch):
    class FakeRunner(MagicMock):
        def __init__(self, *a, **k):
            super().__init__()
            self.tier = k.get("model_instance").identity.tier
            self.model_id = k.get("model_instance").identity.model_id
            self.identity = k.get("model_instance").identity
            self._thinker = None
            self._running = False
            self._status = "idle"
            self._status_detail = ""
            self.MAX_CHAT_TOOL_TURNS = 25
            self._react_loop = None
            self._think_loop_state = None
            self._task_description = ""
            self._started_at = 0.0
            self.supervisor = ""
            self._active_skill = None

        async def start(self, *a, **k):
            self._running = True

        async def stop(self):
            self._running = False

    monkeypatch.setattr(mr_mod, "ModelRunner", FakeRunner)
    return FakeRunner


def test_start_runner_success(monkeypatch):
    m, factory = _make_manager(monkeypatch)
    _patch_runner(monkeypatch)
    factory.create_expert.return_value.identity = MagicMock(tier="expert", model_id="x", name="n", role="r", default_skill="")
    model_id = _run(m.start_runner("expert_implementer", "任务", probe_id="p1"))
    assert model_id is not None
    assert model_id in m._runners
    assert m._probe_map["p1"] == model_id
    assert m._count_by_tier["expert"] == 1


def test_start_runner_unknown_identity(monkeypatch):
    m, _ = _make_manager(monkeypatch)
    assert _run(m.start_runner("不存在", "任务")) is None


def test_start_runner_exceeds_tier_limit(monkeypatch):
    m, factory = _make_manager(monkeypatch)
    _patch_runner(monkeypatch)
    m.MAX_RUNNERS = {"large": 1, "supervisor": 1, "expert": 1}
    # 先占满 expert 名额
    factory.create_expert.return_value.identity = MagicMock(tier="expert", model_id="e", name="n", role="r", default_skill="")
    m._count_by_tier["expert"] = 1
    assert _run(m.start_runner("expert_implementer", "任务")) is None


def test_model_id_unique_suffix(monkeypatch):
    m, factory = _make_manager(monkeypatch)
    _patch_runner(monkeypatch)
    factory.create_expert.return_value.identity = MagicMock(tier="expert", model_id="e", name="n", role="r", default_skill="")
    ids = set()
    for _ in range(2):
        mid = _run(m.start_runner("expert_implementer", "任务"))
        ids.add(mid)
    assert len(ids) == 2
    assert all(mid.startswith("expert_implementer_") for mid in ids)


def test_stop_runner(monkeypatch):
    m, factory = _make_manager(monkeypatch)
    _patch_runner(monkeypatch)
    factory.create_expert.return_value.identity = MagicMock(tier="expert", model_id="e", name="n", role="r", default_skill="")
    model_id = _run(m.start_runner("expert_implementer", "任务", probe_id="p1"))
    assert _run(m.stop_runner(model_id)) is True
    assert model_id not in m._runners
    assert m._count_by_tier["expert"] == 0


def test_stop_runner_missing():
    m = ModelRunnerManager.__new__(ModelRunnerManager)
    m._runners = {}
    m._probe_map = {}
    m._lock = __import__("threading").RLock()
    m._count_by_tier = {"expert": 0}
    assert _run(m.stop_runner("不存在")) is False


def test_list_runners_empty():
    m = ModelRunnerManager.__new__(ModelRunnerManager)
    m._runners = {}
    assert m.list_runners() == []


def test_runner_manager_factory_registry(monkeypatch):
    """get_runner_manager 全局注册表：同 session 返回同一 manager"""
    m, _ = _make_manager(monkeypatch)
    # 用 _make_manager 的依赖已经 mock；直接测注册表函数
    from modules.thinking.core.model_runner import get_runner_manager
    from unittest.mock import patch as _patch
    with _patch.object(mr_mod, "_runner_managers", {}), _patch.object(mr_mod, "_runner_managers_lock", __import__("threading").RLock()):
        m1 = get_runner_manager("sid-abc")
        m2 = get_runner_manager("sid-abc")
        assert m1 is m2
