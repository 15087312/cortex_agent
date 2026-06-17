#!/usr/bin/env python3
"""
工具可见性测试 — 展示核心/非核心工具的分层展示方式

运行: python3 tests/test_tool_visibility.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.tool_manager.tool_registry import ToolRegistry
from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def header(title):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"  {title}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


# ======================================================================
# 测试 1: 核心工具 — 带完整 schema
# ======================================================================
def test_core_tools():
    header(f"核心工具 ({BOLD}完整 schema 注入 tools 数组{RESET})")

    whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
    core_tools = ToolRegistry.get_core_tools_for_api(whitelist)

    ok(f"共 {len(core_tools)} 个核心工具")
    for t in core_tools:
        f = t["function"]
        params = f["parameters"]["properties"]
        param_str = ", ".join(f"{p}: {s.get('type','str')}" for p, s in list(params.items())[:3])
        if len(params) > 3:
            param_str += ", ..."
        print(f"  📌 {BOLD}{f['name']}{RESET}({param_str})")
        print(f"     {DIM}{f['description'][:70]}...{RESET}")


# ======================================================================
# 测试 2: 非核心工具 — 仅名称列表
# ======================================================================
def test_non_core_tools():
    header(f"非核心工具 ({BOLD}仅名称 + 描述列表{RESET})")

    whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
    non_core = ToolRegistry.list_non_core_tools(whitelist)

    ok(f"共 {len(non_core)} 个非核心工具")
    print(f"  {DIM}这些工具只出现在 prompt 「其他可用工具」区域，{RESET}")
    print(f"  {DIM}模型需调用 query_tool_details(name) 获取参数后才能调用{RESET}\n")

    # 分组展示
    categories = {}
    for t in non_core:
        info = get_tool_info(t["name"])
        cat = info.get("category", "other") if info else "other"
        categories.setdefault(cat, []).append(t["name"])

    for cat, names in sorted(categories.items()):
        print(f"  [{cat}]")
        for name in names:
            info = get_tool_info(name)
            desc = info.get("description", "")[:50] if info else ""
            print(f"    🔹 {name}: {desc}")
        print()

    # 模拟模型看到的 prompt 区域
    print(f"\n  {YELLOW}↓ 模型 prompt 中看到的区域 ↓{RESET}\n")
    all_names = [t["name"] for t in non_core]
    print(f"{DIM}【其他可用工具（需先查询再调用）】")
    for n in all_names:
        print(f"  {n}")
    print(f"调用前请使用 query_tool_details(tool_name) 查询参数和用法。{RESET}")


def get_tool_info(name):
    t = ToolRegistry.get_tool(name)
    if t:
        return {"name": t.name, "description": t.description, "category": t.category}
    return None


# ======================================================================
# 测试 3: query_tool_details 示例
# ======================================================================
def test_query_details():
    header("query_tool_details 示例 — 查询非核心工具参数")

    whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
    non_core = ToolRegistry.list_non_core_tools(whitelist)

    if not non_core:
        ok("无非核心工具（所有工具已为核心）")
        return

    sample = non_core[0]
    print(f"  模型想调用: {sample['name']}")
    print(f"  → 先调 query_tool_details(tool_name=\"{sample['name']}\")")
    print(f"  → 返回完整参数定义，即可正常调用")
    print()

    # 模拟 query_tool_details 返回
    info = get_tool_info(sample["name"])
    if info:
        from infra.mcp.factory import get_mcp_tool_service
        mcp = get_mcp_tool_service()
        all_tools = mcp.list_tools()
        detail = all_tools.get(sample["name"])
        if detail:
            params = detail.parameters or {}
            props = params.get("properties", {})
            print(f"  {BOLD}{sample['name']}{RESET} 的参数定义:")
            for pname, pschema in props.items():
                ptype = pschema.get("type", "string")
                pdesc = pschema.get("description", "")
                print(f"    - {pname}: {ptype}  {DIM}{pdesc}{RESET}")
            ok(f"query_tool_details 可获取 {sample['name']} 的完整参数")
        else:
            print(f"  {DIM}(detail not available via MCP){RESET}")


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    print(f"\n{BOLD}工具可见性测试 — 大模型视角{RESET}")

    total = ToolRegistry.list_tools()
    whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
    core = ToolRegistry.get_core_tools_for_api(whitelist)
    non_core = ToolRegistry.list_non_core_tools(whitelist)

    print(f"\n  总工具: {len(total)}")
    print(f"  核心(完整schema): {len(core)}")
    print(f"  非核心(名称列表): {len(non_core)}")
    print(f"  合计可见: {len(core) + len(non_core)}")

    test_core_tools()
    test_non_core_tools()
    test_query_details()

    print(f"\n{BOLD}{GREEN}全部 {_passed} 项通过 ✓{RESET}\n")
