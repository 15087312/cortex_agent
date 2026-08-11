"""identity_loader 测试（此前 0% 覆盖）：外部 YAML 身份加载/合并"""
import pathlib

import pytest

from modules.thinking.identity_loader import load_yaml_identities, merge_identities, load_and_merge


def _write_yaml(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ── load_yaml_identities ────────────────────────────────────────────────────

def test_load_valid_yaml(tmp_path):
    _write_yaml(tmp_path, "expert_translator.yaml", """
identity_key: expert_translator
name: 翻译专家
tier: expert
role: translator
expertise: [Python, JavaScript]
""")
    r = load_yaml_identities(str(tmp_path))
    assert "expert_translator" in r
    assert r["expert_translator"]["tier"] == "expert"
    assert r["expert_translator"]["expertise"] == ["Python", "JavaScript"]


def test_load_missing_dir_returns_empty():
    assert load_yaml_identities("/不存在/目录") == {}


def test_load_invalid_tier_skipped(tmp_path):
    _write_yaml(tmp_path, "bad.yaml", "tier: hacker\nname: x\n")
    assert load_yaml_identities(str(tmp_path)) == {}


def test_load_missing_tier_skipped(tmp_path):
    _write_yaml(tmp_path, "no_tier.yaml", "name: x\nrole: r\n")
    assert load_yaml_identities(str(tmp_path)) == {}


def test_load_filters_unknown_fields(tmp_path):
    _write_yaml(tmp_path, "clean.yaml", "identity_key: k\ntier: expert\nname: n\nhacker_field: x\n")
    r = load_yaml_identities(str(tmp_path))
    assert "hacker_field" not in r["k"]


def test_load_uses_filename_as_key(tmp_path):
    _write_yaml(tmp_path, "file_key.yaml", "tier: expert\nname: n\n")
    r = load_yaml_identities(str(tmp_path))
    assert "file_key" in r


def test_load_non_dict_yaml_skipped(tmp_path):
    _write_yaml(tmp_path, "list.yaml", "- a\n- b\n")
    assert load_yaml_identities(str(tmp_path)) == {}


def test_load_bad_yaml_skipped(tmp_path):
    _write_yaml(tmp_path, "bad_syntax.yaml", "tier: [unclosed\n")
    assert load_yaml_identities(str(tmp_path)) == {}


# ── merge_identities ────────────────────────────────────────────────────────

def test_merge_overrides_existing():
    defaults = {"expert_code_writer": {"name": "旧名", "tier": "expert", "role": "code_writer"}}
    merged = merge_identities(defaults, {"expert_code_writer": {"name": "新名"}})
    assert merged["expert_code_writer"]["name"] == "新名"
    assert merged["expert_code_writer"]["tier"] == "expert"  # 未覆盖的保留
    assert defaults["expert_code_writer"]["name"] == "旧名"  # 不修改原字典


def test_merge_ignores_none_values():
    defaults = {"k": {"name": "A", "tier": "expert"}}
    merged = merge_identities(defaults, {"k": {"name": None, "tier": "expert"}})
    assert merged["k"]["name"] == "A"  # None 不覆盖


def test_merge_adds_new_identity():
    merged = merge_identities({}, {"expert_translator": {"tier": "expert"}})
    assert "expert_translator" in merged
    # 自动补默认字段
    assert merged["expert_translator"]["model_id"] == "expert_translator_001"
    assert merged["expert_translator"]["name"] == "expert_translator"
    assert merged["expert_translator"]["role"] == "expert_translator"


# ── load_and_merge ──────────────────────────────────────────────────────────

def test_load_and_merge_no_overrides_returns_defaults(tmp_path):
    defaults = {"k": {"tier": "expert"}}
    assert load_and_merge(defaults, str(tmp_path)) == defaults


def test_load_and_merge_with_overrides(tmp_path):
    defaults = {"expert_code_writer": {"name": "旧", "tier": "expert", "role": "code_writer"}}
    _write_yaml(tmp_path, "expert_code_writer.yaml", "tier: expert\nname: 新名\n")
    merged = load_and_merge(defaults, str(tmp_path))
    assert merged["expert_code_writer"]["name"] == "新名"
