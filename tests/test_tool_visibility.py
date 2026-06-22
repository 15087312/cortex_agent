#!/usr/bin/env python3
"""工具可见性测试 — 核心/非核心工具的分层展示验证"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.tool_manager.tool_registry import ToolRegistry
from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS


def _get_tool_info(name):
    t = ToolRegistry.get_tool(name)
    if t:
        return {"name": t.name, "description": t.description, "category": t.category}
    return None


# ======================================================================
# 测试 1: 核心工具 — 带完整 schema
# ======================================================================
def test_core_tools():
    whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
    assert whitelist, "DEFAULT_TOOL_WHITELISTS['large'] 不应为空"

    core_tools = ToolRegistry.get_core_tools_for_api(whitelist)
    assert isinstance(core_tools, list), "get_core_tools_for_api 应返回 list"
    assert len(core_tools) > 0, "核心工具数量应大于 0"

    for t in core_tools:
        assert "function" in t, f"核心工具缺少 function 字段: {t}"
        f = t["function"]
        assert "name" in f and f["name"], "工具缺少 name"
        assert "description" in f and f["description"], (
            f"工具 {f.get('name')} 缺少 description"
        )
        assert "parameters" in f, f"工具 {f['name']} 缺少 parameters"
        assert "properties" in f["parameters"], (
            f"工具 {f['name']} 的 parameters 缺少 properties"
        )


# ======================================================================
# 测试 2: 非核心工具 — 仅名称列表
# ======================================================================
def test_non_core_tools():
    whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
    non_core = ToolRegistry.list_non_core_tools(whitelist)

    assert isinstance(non_core, list), "list_non_core_tools 应返回 list"

    core_tools = ToolRegistry.get_core_tools_for_api(whitelist)
    core_names = {t["function"]["name"] for t in core_tools}
    non_core_names = {t["name"] for t in non_core}
    assert core_names.isdisjoint(non_core_names), (
        f"核心与非核心工具不应重叠: {core_names & non_core_names}"
    )

    for t in non_core:
        assert "name" in t and t["name"], "非核心工具缺少 name"
        info = _get_tool_info(t["name"])
        assert info is not None, f"非核心工具 {t['name']} 在 ToolRegistry 中查不到"


# ======================================================================
# 测试 3: query_tool_details — 查询非核心工具参数
# ======================================================================
def test_query_details():
    whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
    non_core = ToolRegistry.list_non_core_tools(whitelist)

    if not non_core:
        import pytest
        pytest.skip("无非核心工具，无法验证 query_tool_details")

    sample = non_core[0]
    info = _get_tool_info(sample["name"])
    assert info is not None, f"非核心工具 {sample['name']} 信息查询失败"
    assert info["name"] == sample["name"]
    assert info["description"], f"{sample['name']} 缺少 description"
