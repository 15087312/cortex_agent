"""PetState 测试 — 持久化/时间衰减/心情惩罚/互动效果/描述文案分支"""
import json

import pytest

import modules.desktop_pet.pet_state as ps
from modules.desktop_pet.pet_state import PetState


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    old = PetState._instance
    PetState._instance = None
    yield
    PetState._instance = old


def _state(path):
    st = PetState.__new__(PetState)
    st._path = str(path)
    st._values = dict(ps.DEFAULTS)
    st._updated_at = 1000000.0
    return st


# ── 路径 / 单例 ──────────────────────────────────────────────────────────

def test_state_file_default(monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda _: "/home/tester")
    assert ps._state_file() == "/home/tester/.cortex/pet_state.json"


def test_get_instance_singleton(tmp_path):
    s1 = PetState.get_instance()
    s2 = PetState.get_instance()
    assert s1 is s2


def test_init_default_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda _: str(tmp_path))
    st = PetState()
    assert st._path == str(tmp_path / ".cortex" / "pet_state.json")


# ── 持久化 ───────────────────────────────────────────────────────────────

def test_load_from_file(tmp_path):
    (tmp_path / "st.json").write_text(json.dumps({
        "values": {"mood": 99, "satiety": 12.5},
        "updated_at": 12345.0,
    }), encoding="utf-8")
    st = PetState(path=str(tmp_path / "st.json"))
    assert st._values["mood"] == 99.0
    assert st._values["satiety"] == 12.5
    assert st._values["energy"] == ps.DEFAULTS["energy"]  # 未写 → 默认
    assert st._updated_at == 12345.0


def test_load_ignores_non_numeric(tmp_path):
    (tmp_path / "st.json").write_text(json.dumps({
        "values": {"mood": "不是数字", "satiety": None},
        "updated_at": "bad",
    }), encoding="utf-8")
    st = PetState(path=str(tmp_path / "st.json"))
    assert st._values == dict(ps.DEFAULTS)
    assert st._updated_at != 12345.0


def test_load_missing_file(tmp_path):
    st = PetState(path=str(tmp_path / "nope.json"))
    assert st._values == dict(ps.DEFAULTS)


def test_load_invalid_json(tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    st = PetState(path=str(tmp_path / "bad.json"))
    assert st._values == dict(ps.DEFAULTS)


def test_save_writes_file(tmp_path):
    st = _state(tmp_path / "pet" / "state.json")
    st._save()
    data = json.loads((tmp_path / "pet" / "state.json").read_text(encoding="utf-8"))
    assert data["values"] == ps.DEFAULTS
    assert not (tmp_path / "pet" / "state.tmp").exists()


def test_save_failure_swallowed(tmp_path, monkeypatch):
    st = _state(tmp_path / "st.json")
    monkeypatch.setattr(ps.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("no perm")))
    st._save()  # 不抛异常


# ── read：衰减 + 惩罚 + clamp ─────────────────────────────────────────────

def test_read_defaults(tmp_path):
    st = _state(tmp_path / "st.json")
    out = st.read(now=3600)
    assert isinstance(out, dict)
    assert set(out) == set(ps.DEFAULTS)
    assert out["mood"] == 60 and out["satiety"] == 70


def test_read_decay_by_elapsed(tmp_path):
    st = _state(tmp_path / "st.json")
    st._updated_at = 0.0
    out = st.read(now=10 * 3600)  # 10 小时后
    assert out["satiety"] == 40  # 70 - 3*10
    assert out["energy"] == 60    # 80 - 2*10
    assert out["cleanliness"] == 45  # 75 - 3*10
    assert out["mood"] == 55      # 60 - 0.5*10


def test_read_never_negative_time(tmp_path):
    st = _state(tmp_path / "st.json")
    st._updated_at = 1000.0
    assert st.read(now=500.0) == {k: int(v) for k, v in ps.DEFAULTS.items()}


def test_read_mood_penalty(tmp_path):
    st = _state(tmp_path / "st.json")
    st._values = {"mood": 60, "satiety": 10, "energy": 10, "cleanliness": 10}
    st._updated_at = 0.0
    out = st.read(now=2 * 3600)
    assert out["mood"] == 49  # 60-1 - (2.0+1.5+1.5)*2


def test_read_clamp_low(tmp_path):
    st = _state(tmp_path / "st.json")
    st._values = {"mood": -10, "satiety": -100, "energy": 80, "cleanliness": 75}
    st._updated_at = 0.0
    out = st.read(now=1 * 3600)
    assert out["mood"] == 0
    assert out["satiety"] == 0


def test_read_clamp_high(tmp_path):
    st = _state(tmp_path / "st.json")
    st._values = {"mood": 200, "satiety": 150, "energy": 80, "cleanliness": 75}
    out = st.read(now=3600)
    assert out["mood"] == 100
    assert out["satiety"] == 100


# ── apply：互动效果 ───────────────────────────────────────────────────────

def test_apply_action_updates_and_saves(tmp_path):
    st = _state(tmp_path / "pet.json")
    st._updated_at = 1000000.0
    out = st.apply("cake", now=1000000.0)  # satiety+25, mood+8
    assert out["satiety"] == 95
    assert out["mood"] == 68
    assert st._updated_at == 1000000.0
    saved = json.loads((tmp_path / "pet.json").read_text(encoding="utf-8"))
    assert saved["values"]["satiety"] == 95


def test_apply_clamps_to_max(tmp_path):
    st = _state(tmp_path / "st.json")
    st._values = {"mood": 95, "satiety": 90, "energy": 80, "cleanliness": 75}
    st._updated_at = 1000000.0
    out = st.apply("cake", now=1000000.0)
    assert out["satiety"] == 100
    assert out["mood"] == 100


def test_apply_unknown_action_no_effect(tmp_path):
    st = _state(tmp_path / "st.json")
    st._updated_at = 1000000.0
    out = st.apply("nope", now=1000000.0)
    assert out["satiety"] == 70  # 无 effects
    assert out["mood"] == 60


def test_apply_decays_before_effect(tmp_path):
    st = _state(tmp_path / "st.json")
    st._updated_at = 1000000.0
    out = st.apply("cake", now=1000000.0 + 10 * 3600)  # 先衰减再 +25
    assert out["satiety"] == 65  # (70-30)+25


def test_apply_ignores_non_default_effect_keys(tmp_path, monkeypatch):
    import modules.desktop_pet.actions as actions_mod
    monkeypatch.setattr(
        actions_mod, "get_action",
        lambda _: {"id": "custom", "effects": {"mood": 5, "magic": 999}},
    )
    st = _state(tmp_path / "st.json")
    st._updated_at = 1000000.0
    out = st.apply("custom", now=1000000.0)
    assert out["mood"] == 65  # 仅 mood 生效，magic 被忽略


def test_apply_with_mood_penalty(tmp_path):
    st = _state(tmp_path / "st.json")
    st._values = {"mood": 60, "satiety": 10, "energy": 10, "cleanliness": 10}
    st._updated_at = 1000000.0
    out = st.apply("cake", now=1000000.0 + 2 * 3600)
    # 低饱食/精力/清洁 各惩罚一次 mood：60-1-(2+1.5+1.5)*2=49；cake +8 → 57
    assert out["mood"] == 57
    assert out["satiety"] == 29  # (10-6)+25


# ── describe ──────────────────────────────────────────────────────────────

def test_describe_high(tmp_path):
    st = _state(tmp_path / "st.json")
    desc = st.describe({"mood": 90, "satiety": 90, "energy": 90, "cleanliness": 90})
    assert desc.startswith("你现在心情很好、吃得很饱、精力充沛、身上干干净净。")


def test_describe_mid(tmp_path):
    st = _state(tmp_path / "st.json")
    desc = st.describe({"mood": 50, "satiety": 50, "energy": 50, "cleanliness": 50})
    assert "心情不错" in desc and "肚子半饱" in desc and "精力一般" in desc and "身上有点脏" in desc


def test_describe_low(tmp_path):
    st = _state(tmp_path / "st.json")
    desc = st.describe({"mood": 29, "satiety": 29, "energy": 39, "cleanliness": 39})
    assert "心情很差" in desc and "饿坏了" in desc and "很疲惫" in desc and "身上脏兮兮" in desc


def test_describe_depressed_hungry(tmp_path):
    st = _state(tmp_path / "st.json")
    desc = st.describe({"mood": 30, "satiety": 30, "energy": 60, "cleanliness": 60})
    assert "心情有点低落" in desc and "有点饿" in desc


def test_describe_default_reads(tmp_path):
    st = _state(tmp_path / "st.json")
    st._updated_at = 0.0
    desc = st.describe()  # values=None → 用 read()
    assert desc.count("、") == 3
    assert desc.endswith("。")
