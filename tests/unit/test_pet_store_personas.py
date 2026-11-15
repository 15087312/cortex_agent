"""PetStore 人设 + 按人设隔离的会话历史。"""
import pytest

from modules.desktop_pet.store import PetStore


@pytest.fixture
def store(tmp_path):
    return PetStore(str(tmp_path / "pet.db"))


def test_fresh_db_seeds_default_persona(store):
    personas = store.list_personas()
    assert len(personas) == 1
    assert store.get_active_persona_id() == personas[0]["id"]
    assert personas[0]["prompt"]


def test_add_switch_and_history_isolation(store):
    p1 = store.get_active_persona_id()
    cat = store.add_persona("猫娘", "你是猫娘")
    assert cat and cat["id"] != p1

    store.add_history("user", "默认人设的消息")
    store.add_history("assistant", "默认回复")
    assert [m["content"] for m in store.get_history()] == ["默认人设的消息", "默认回复"]

    store.set_active_persona(cat["id"])
    assert store.get_history() == []  # 新会话从空开始
    store.add_history("user", "猫娘你好")
    store.add_history("assistant", "喵")
    assert [m["content"] for m in store.get_history()] == ["猫娘你好", "喵"]

    store.set_active_persona(p1)
    assert [m["content"] for m in store.get_history()] == ["默认人设的消息", "默认回复"]
    assert store.get_history(persona_id=cat["id"])[0]["content"] == "猫娘你好"


def test_clear_history_only_current_persona(store):
    p1 = store.get_active_persona_id()
    p2 = store.add_persona("猫娘", "你是猫娘")
    store.add_history("user", "p1")
    store.set_active_persona(p2["id"])
    store.add_history("user", "p2")

    store.clear_history()
    assert store.get_history() == []
    store.set_active_persona(p1)
    assert store.get_history()[0]["content"] == "p1"


def test_update_active_persona_syncs_config_prompt(store):
    pid = store.get_active_persona_id()
    updated = store.update_persona(pid, {"prompt": "新提示词"})
    assert updated["prompt"] == "新提示词"
    assert store.get_config()["persona"] == "新提示词"


def test_delete_persona_removes_history_and_keeps_last(store):
    p1 = store.get_active_persona_id()
    p2 = store.add_persona("猫娘", "你是猫娘")
    store.set_active_persona(p2["id"])
    store.add_history("user", "只有猫娘")

    assert store.delete_persona(p2["id"]) is True
    assert store.get_persona(p2["id"]) is None
    assert store.get_history(persona_id=p2["id"]) == []
    assert store.get_active_persona_id() == p1

    assert store.delete_persona(p1) is False  # 最后一个人设不可删


def test_duplicate_persona_name_rejected(store):
    store.add_persona("猫娘", "a")
    assert store.add_persona("猫娘", "b") is None
    assert store.add_persona(" 猫娘 ", "b") is None


def test_rename_persona_keeps_prompt_and_active(store):
    """新页面允许改名：改名不能顺手把提示词/启用状态改掉（旧页面的坑）"""
    pid = store.get_active_persona_id()
    before = store.get_persona(pid)
    renamed = store.update_persona(pid, {"name": "新名字"})
    assert renamed["name"] == "新名字"
    assert renamed["prompt"] == before["prompt"]
    assert store.get_active_persona_id() == pid
    assert store.get_config()["persona"] == before["prompt"]


def test_rename_to_existing_name_rejected(store):
    store.add_persona("猫娘", "a")
    pid = store.get_active_persona_id()
    assert store.update_persona(pid, {"name": "猫娘"}) is None
    assert store.get_persona(pid)["name"] == "默认人设"
    # 大小写/空白视为同名
    assert store.update_persona(pid, {"name": " 猫娘 "}) is None


def test_manual_memory_isolated_per_persona(store):
    p1 = store.get_active_persona_id()
    p2 = store.add_persona("猫娘", "你是猫娘")
    store.add_memory("fact", "属于默认人设")
    store.set_active_persona(p2["id"])
    store.add_memory("fact", "属于猫娘")
    assert [m["text"] for m in store.list_memory()] == ["属于猫娘"]
    store.set_active_persona(p1)
    assert [m["text"] for m in store.list_memory()] == ["属于默认人设"]
    assert [m["text"] for m in store.search_memory(persona_id=p2["id"])] == ["属于猫娘"]


def test_delete_persona_removes_manual_memory(store):
    p2 = store.add_persona("猫娘", "你是猫娘")
    store.set_active_persona(p2["id"])
    store.add_memory("fact", "只有猫娘")
    store.delete_persona(p2["id"])
    assert store.list_memory(persona_id=p2["id"]) == []
