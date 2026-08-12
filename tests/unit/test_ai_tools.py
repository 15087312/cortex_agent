"""ai_tools 测试（此前 13% 覆盖）：代码校验 / 创建 / 列表 / 删除"""
from unittest.mock import MagicMock, patch

from infra.tool_manager.tools import ai_tools as at


GOOD_CODE = "def my_tool(a, b):\n    return a + b\n"


# ── _validate_tool_code ─────────────────────────────────────────────────────

def test_validate_empty():
    assert at._validate_tool_code("", "f") == "代码不能为空"


def test_validate_syntax_error():
    r = at._validate_tool_code("def f(:\n", "f")
    assert "语法错误" in r


def test_validate_missing_function():
    r = at._validate_tool_code("def other():\n    pass\n", "my_tool")
    assert "必须包含函数定义" in r


def test_validate_forbids_import():
    r = at._validate_tool_code("import os\ndef my_tool():\n    return 1\n", "my_tool")
    assert "import" in r


def test_validate_forbids_eval():
    r = at._validate_tool_code("def my_tool():\n    return eval('1')\n", "my_tool")
    assert "eval" in r


def test_validate_forbids_open():
    r = at._validate_tool_code("def my_tool():\n    return open('f')\n", "my_tool")
    assert "open" in r


def test_validate_forbids_os_system():
    r = at._validate_tool_code("def my_tool():\n    import os\n    os.system('ls')\n", "my_tool")
    assert "import" in r or "system" in r


def test_validate_ok():
    assert at._validate_tool_code(GOOD_CODE, "my_tool") is None


# ── create_tool ──────────────────────────────────────────────────────────────

def test_create_tool_success(monkeypatch):
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": True})
    r = at.create_tool(tool_name="my_tool", description="测试工具", code=GOOD_CODE)
    assert "创建成功" in r


def test_create_tool_missing_name():
    assert "tool_name 不能为空" in at.create_tool(tool_name="", description="d", code=GOOD_CODE)


def test_create_tool_missing_description():
    assert "description 不能为空" in at.create_tool(tool_name="x", description="", code=GOOD_CODE)


def test_create_tool_bad_params(monkeypatch):
    r = at.create_tool(tool_name="x", description="d", code=GOOD_CODE, params="not-json")
    assert "JSON" in r


def test_create_tool_invalid_code():
    # 无对应函数定义 → 校验失败
    r = at.create_tool(tool_name="x", description="d", code="def other():\n    return 1\n")
    assert "代码验证失败" in r


def test_create_tool_register_fail(monkeypatch):
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": False, "error": "冲突"})
    r = at.create_tool(tool_name="my_tool", description="d", code=GOOD_CODE)
    assert "注册失败" in r


# ── list / delete ────────────────────────────────────────────────────────────

def test_list_my_tools_empty(monkeypatch):
    monkeypatch.setattr(at.ToolRegistry, "list_tools", lambda: {})
    r = at.list_my_tools()
    assert "尚未创建" in r


def test_delete_tool_missing_name():
    assert "不能为空" in at.delete_tool("")


def test_delete_tool_not_exists(monkeypatch):
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: None)
    r = at.delete_tool("ghost")
    assert "不存在" in r


def test_delete_tool_success(monkeypatch):
    info = MagicMock()
    info.source = "dynamic"
    info.tags = ["ai_tool"]
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    monkeypatch.setattr(at.ToolRegistry, "unregister", lambda n: None)
    monkeypatch.setattr(at, "_remove_persisted", lambda n: True)
    r = at.delete_tool("my_tool")
    assert "已成功删除" in r


# ── edit_tool / 持久化 ──────────────────────────────────────────────────────

def test_edit_tool_success(monkeypatch):
    info = MagicMock()
    info.source = "dynamic"
    info.tags = ["ai_tool"]
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    monkeypatch.setattr(at, "_update_persisted", lambda *a, **k: None)
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": True})
    r = at.edit_tool(tool_name="my_tool", description="新描述", code=GOOD_CODE)
    assert "成功" in r or "更新" in r


def test_edit_tool_missing_name():
    assert "不能为空" in at.edit_tool(tool_name="")


def test_edit_tool_not_exists(monkeypatch):
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: None)
    r = at.edit_tool(tool_name="ghost", description="d", code=GOOD_CODE)
    assert "不存在" in r


def test_add_remove_persisted(monkeypatch):
    from infra.tool_manager.tools import ai_tools as _at
    store = {}
    def fake_save(tools):
        store.clear()
        store.update(tools)
    monkeypatch.setattr(_at, "_load_persisted", lambda: dict(store))
    monkeypatch.setattr(_at, "_save_persisted", fake_save)
    _at._add_persisted("t1", {"name": "t1"})
    assert "t1" in store
    assert _at._remove_persisted("t1") is True
    assert "t1" not in store
