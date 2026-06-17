#!/usr/bin/env python3
"""
AI 自创工具 — 全流程测试脚本

测试覆盖：
1. create_tool  — 创建工具（成功/失败场景）
2. list_my_tools — 列出工具
3. edit_tool    — 编辑工具（描述/代码/参数）
4. delete_tool  — 删除工具
5. 安全验证     — 危险代码拦截
6. 持久化恢复   — 重启后自动恢复
7. 工具可调用   — 注册后真正可执行
"""
import sys
import os

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")
        if detail:
            print(f"     {detail}")


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── 导入 ──────────────────────────────────────────────────────────

section("1. 系统初始化")

from infra.tool_manager.tools.ai_tools import (
    create_tool, list_my_tools, delete_tool, edit_tool, restore_ai_tools,
)
from infra.tool_manager.tool_registry import ToolRegistry

print("  模块导入成功")
initial_tool_count = len(ToolRegistry.list_tools(source="dynamic"))
check("初始自创工具数为 0", initial_tool_count == 0, f"实际: {initial_tool_count}")


# ── 创建工具 ──────────────────────────────────────────────────────

section("2. create_tool — 创建工具")

# 2.1 创建简单工具
result = create_tool(
    tool_name="greeter",
    description="打招呼工具",
    code="def greeter(name: str) -> str:\n    return f'Hello, {name}!'",
    params='{"name": "名字"}',
)
check("greeter 创建成功", "创建成功" in result)
check("返回包含工具名", "greeter" in result)
check("返回包含提示", "tools_search" in result)

# 2.2 验证已注册
t = ToolRegistry.get_tool("greeter")
check("greeter 已在 ToolRegistry 中", t is not None)
check("source = dynamic", t and t.source == "dynamic")
check("tags 包含 ai_tool", t and "ai_tool" in t.tags)
check("risk_level = MEDIUM", t and t.risk_level == "MEDIUM")

# 2.3 调用创建的工具
check("工具函数可调用", t and callable(t.func))
if t:
    func_result = t.func(name="World")
    check("工具执行结果正确", func_result == "Hello, World!", f"实际: {func_result}")

# 2.4 创建带类型注解和多参数的工具
result = create_tool(
    tool_name="calculator",
    description="简易计算器",
    code="""def calculator(a: float, b: float, op: str = '+') -> str:
    if op == '+':
        return f'{a} + {b} = {a + b}'
    elif op == '-':
        return f'{a} - {b} = {a - b}'
    elif op == '*':
        return f'{a} * {b} = {a * b}'
    elif op == '/':
        return f'{a} / {b} = {a / b}'
    return '不支持的操作符'""",
    params={"a": "第一个数", "b": "第二个数", "op": "运算符(+/-/*//)"},
)
check("calculator 创建成功", "创建成功" in result)

calc_func = ToolRegistry.get_func("calculator")
check("calculator 函数可调用", calc_func is not None)
if calc_func:
    check("3+5=8", calc_func(3, 5, "+") == "3 + 5 = 8")
    check("10/2=5", calc_func(10, 2, "/") == "10 / 2 = 5.0")


# ── 列出自创工具 ──────────────────────────────────────────────────

section("3. list_my_tools — 列出自创工具")

lst = list_my_tools()
check("列表中包含 greeter", "greeter" in lst)
check("列表中包含 calculator", "calculator" in lst)
check("显示描述信息", "打招呼工具" in lst and "简易计算器" in lst)

# 确认总数正确
from infra.tool_manager.tool_registry import ToolRegistry
dynamic_count = len(ToolRegistry.list_tools(source="dynamic"))
ai_tool_count = len([n for n, i in ToolRegistry.list_tools().items()
                     if i.get("source") == "dynamic" and "ai_tool" in i.get("tags", [])])
check(f"dynamic 源工具数: {dynamic_count}", dynamic_count == 2)
check(f"ai_tool 标签工具数: {ai_tool_count}", ai_tool_count == 2)

print(f"\n  list_my_tools 输出预览:")
for line in lst.split("\n")[:8]:
    print(f"    {line}")


# ── 编辑工具 ──────────────────────────────────────────────────────

section("4. edit_tool — 编辑工具")

# 4.1 只编辑描述
result = edit_tool(
    tool_name="greeter",
    description="更新后的打招呼工具——支持多语言",
)
check("编辑描述成功", "更新成功" in result)
check("提示包含「描述」", "描述" in result)

t = ToolRegistry.get_tool("greeter")
check("描述已更新", t and t.description == "更新后的打招呼工具——支持多语言")

# 4.2 编辑代码
result = edit_tool(
    tool_name="greeter",
    code="def greeter(name: str, lang: str = 'en') -> str:\n"
         "    if lang == 'zh':\n"
         "        return f'你好, {name}!'\n"
         "    return f'Hello, {name}!'",
    params='{"name": "名字", "lang": "语言(en/zh)"}',
)
check("编辑代码成功", "更新成功" in result)
check("提示包含「代码」", "代码" in result)

# 验证新代码生效
func = ToolRegistry.get_func("greeter")
check("新函数可调用", func is not None)
if func:
    check("en: Hello World", func("World") == "Hello, World!")
    check("zh: 你好世界", func("世界", "zh") == "你好, 世界!")

# 4.3 验证参数描述已更新
t = ToolRegistry.get_tool("greeter")
check("新参数 lang 已加入", t and "lang" in t.params)


# ── 错误场景 ──────────────────────────────────────────────────────

section("5. 错误处理与安全验证")

# 5.1 空工具名
result = create_tool(tool_name="", description="x", code="def x(): pass")
check("空名称拒绝", "不能为空" in result)

# 5.2 空描述
result = create_tool(tool_name="test", description="", code="def test(): pass")
check("空描述拒绝", "不能为空" in result)

# 5.3 语法错误
result = create_tool(
    tool_name="bad_syntax",
    description="语法错误",
    code="def bad_syntax(:",
)
check("语法错误拒绝", "语法错误" in result)

# 5.4 缺少函数定义
result = create_tool(
    tool_name="no_func",
    description="缺少函数",
    code="x = 1",
)
check("缺少函数定义拒绝", "必须包含函数定义" in result)

# 5.5 函数名不匹配
result = create_tool(
    tool_name="expected_name",
    description="函数名不匹配",
    code="def wrong_name(): pass",
)
check("函数名不匹配拒绝", "必须包含函数定义" in result)

# 5.6 危险 import
result = create_tool(
    tool_name="evil_import",
    description="危险导入",
    code="def evil_import():\n    import os\n    return os.listdir('/')",
)
check("import 被拦截", "禁止使用 import" in result)

# 5.7 危险 exec
result = create_tool(
    tool_name="evil_exec",
    description="危险 exec",
    code="def evil_exec():\n    exec('print(1)')",
)
check("exec 被拦截", "禁止使用 exec" in result)

# 5.8 危险 open
result = create_tool(
    tool_name="evil_open",
    description="危险 open",
    code="def evil_open():\n    f = open('/etc/passwd')\n    return f.read()",
)
check("open 被拦截", "禁止使用 open" in result)

# 5.9 覆盖系统内置工具
result = create_tool(
    tool_name="calc",
    description="尝试覆盖 calc",
    code="def calc(): return 1",
)
check("覆盖内置工具被拒绝", "已被系统内置工具占用" in result)

# 5.10 编辑不存在的工具
result = edit_tool(tool_name="nonexistent_tool_xyz")
check("编辑不存在工具拒绝", "不存在" in result)

# 5.11 删除不存在的工具
result = delete_tool(tool_name="nonexistent_tool_xyz")
check("删除不存在工具拒绝", "不存在" in result)

# 5.12 删除系统内置工具
result = delete_tool(tool_name="web_search")
check("删除内置工具拒绝", "不是自创工具" in result)

# 5.13 非法 params JSON
result = create_tool(
    tool_name="bad_params",
    description="参数格式错误",
    code="def bad_params(x): return x",
    params="not-json",
)
check("非法 params 拒绝", "不是有效的 JSON" in result)


# ── 删除工具 ──────────────────────────────────────────────────────

section("6. delete_tool — 删除工具")

result = delete_tool(tool_name="calculator")
check("calculator 删除成功", "已成功删除" in result)

result = delete_tool(tool_name="calculator")
check("重复删除拒绝", "不存在" in result)

# 验证已从 ToolRegistry 移除
check("calculator 已从 Registry 移除", ToolRegistry.get_tool("calculator") is None)

# 验证持久化中也移除了
from infra.tool_manager.tools.ai_tools import _load_persisted
records = _load_persisted()
check("calculator 已从持久化移除", "calculator" not in records)
check("greeter 仍在持久化中", "greeter" in records)


# ── 持久化恢复 ────────────────────────────────────────────────────

section("7. 持久化恢复测试（模拟重启）")

# 模拟重启：先清空内存注册表
ToolRegistry.clear_dynamic()
check("清空 dynamic 工具后计数为 0", len(ToolRegistry.list_tools(source="dynamic")) == 0)

# 调用 restore_ai_tools() 恢复
restored = restore_ai_tools()
check(f"恢复工具数 = 1（greeter）", restored == 1, f"实际: {restored}")

# 验证 greeter 被恢复
t = ToolRegistry.get_tool("greeter")
check("greeter 恢复后存在", t is not None)
check("恢复后描述正确", t and t.description == "更新后的打招呼工具——支持多语言")

func = ToolRegistry.get_func("greeter")
if func:
    check("恢复后工具可调用", func("Test") == "Hello, Test!")


# ── 最终清理 ──────────────────────────────────────────────────────

section("8. 测试清理")

result = delete_tool(tool_name="greeter")
check("greeter 已删除", "已成功删除" in result)
check("最终动态工具数为 0", len(ToolRegistry.list_tools(source="dynamic")) == 0)
check("持久化文件无记录", len(_load_persisted()) == 0)


# ── 结果 ──────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"  📊 全流程测试完成")
print(f"  ✅ 通过: {PASS}")
print(f"  ❌ 失败: {FAIL}")
print(f"  📈 总计: {PASS + FAIL}")
print(f"{'='*60}")

sys.exit(0 if FAIL == 0 else 1)
