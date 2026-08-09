"""identity_loader 测试（此前 0% 覆盖）：YAML 身份加载与合并"""
import yaml

from modules.thinking.identity_loader import merge_identities, load_yaml_identities, load_and_merge


def test_load_yaml_identities(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump({
        "identity_key": "role_a",
        "name": "角色A",
        "tier": "expert",
        "personality": "认真",
    }, allow_unicode=True), encoding="utf-8")
    data = load_yaml_identities(str(tmp_path))
    assert "role_a" in data
    assert data["role_a"]["tier"] == "expert"


def test_load_yaml_missing_dir():
    assert load_yaml_identities("/nonexistent/dir") == {}


def test_merge_identities():
    defaults = {"a": {"name": "A", "tier": "expert"}, "b": {"name": "B"}}
    overrides = {"a": {"personality": "覆盖"}}
    merged = merge_identities(defaults, overrides)
    assert merged["a"]["name"] == "A"  # 保留默认字段
    assert merged["a"]["personality"] == "覆盖"  # 应用覆盖
    assert "b" in merged


def test_load_and_merge_empty():
    merged = load_and_merge(defaults={"x": {"name": "X"}}, directory="/nonexistent")
    assert merged["x"]["name"] == "X"
