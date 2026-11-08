"""modules.desktop_pet.actions — 桌宠动作模板表与索引查询"""
from modules.desktop_pet.actions import (
    ACTIONS,
    CATEGORIES,
    actions_by_category,
    get_action,
    public_actions,
)


class TestActionsTable:
    def test_categories_have_required_fields(self):
        assert CATEGORIES
        for c in CATEGORIES:
            assert set(c) == {"id", "label", "icon"}
            assert c["id"] and c["label"] and c["icon"]

    def test_category_ids_unique(self):
        ids = [c["id"] for c in CATEGORIES]
        assert len(ids) == len(set(ids))

    def test_actions_have_required_fields(self):
        assert ACTIONS
        for a in ACTIONS:
            assert set(a) == {"id", "label", "category", "icon", "prompt"}
            assert a["prompt"].strip()

    def test_action_ids_unique(self):
        ids = [a["id"] for a in ACTIONS]
        assert len(ids) == len(set(ids))

    def test_every_action_belongs_to_known_category(self):
        known = {c["id"] for c in CATEGORIES}
        assert {a["category"] for a in ACTIONS} <= known

    def test_every_category_has_actions(self):
        used = {a["category"] for a in ACTIONS}
        assert {c["id"] for c in CATEGORIES} <= used


class TestLookup:
    def test_get_action_hit(self):
        assert get_action("cake")["label"] == "送蛋糕"

    def test_get_action_miss_returns_none(self):
        assert get_action("not-exist") is None

    def test_actions_by_category_groups_all_actions(self):
        grouped = actions_by_category()
        assert sum(len(v) for v in grouped.values()) == len(ACTIONS)
        assert [a["id"] for a in grouped["feed"]][0] == "cake"
        for cat, items in grouped.items():
            assert all(a["category"] == cat for a in items)

    def test_public_actions_whitelist_fields(self):
        rows = public_actions()
        assert len(rows) == len(ACTIONS)
        for r in rows:
            assert set(r) == {"id", "label", "category", "icon", "prompt"}

    def test_public_actions_returns_copy_not_internal_table(self):
        rows = public_actions()
        rows[0]["label"] = "mutated"
        assert get_action(rows[0]["id"])["label"] != "mutated"
