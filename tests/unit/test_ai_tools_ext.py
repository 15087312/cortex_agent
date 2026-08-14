"""ai_tools 补充测试：持久化 / restore / 代码验证危险节点 / create/edit/list 分支"""
import json
from unittest.mock import MagicMock, patch

from infra.tool_manager.tools import ai_tools as at


GOOD_CODE = "def my_tool(a, b):\n    return a + b\n"


# ── 持久化读写 ────────────────────────────────────────────────────────────────

def test_load_persisted_missing_file(monkeypatch, tmp_path):
    p = tmp_path / "nope.json"
    monkeypatch.setattr(at, "_AI_TOOLS_FILE", p)
    assert at._load_persisted() == {}


def test_load_persisted_invalid_json(monkeypatch, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(at, "_AI_TOOLS_FILE", p)
    assert at._load_persisted() == {}


def test_load_persisted_non_dict(monkeypatch, tmp_path):
    p = tmp_path / "list.json"
    p.write_text(json.dumps([1, 2]), encoding="utf-8")
    monkeypatch.setattr(at, "_AI_TOOLS_FILE", p)
    assert at._load_persisted() == {}


def test_load_persisted_read_error(monkeypatch, tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    monkeypatch.setattr(at, "_AI_TOOLS_FILE", p)
    monkeypatch.setattr(at._AI_TOOLS_FILE.__class__, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("perm")))
    assert at._load_persisted() == {}


def test_load_persisted_ok(monkeypatch, tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"t": {"code": "x"}}), encoding="utf-8")
    monkeypatch.setattr(at, "_AI_TOOLS_FILE", p)
    assert at._load_persisted() == {"t": {"code": "x"}}


def test_save_persisted_write_error(monkeypatch, tmp_path):
    p = tmp_path / "w.json"
    monkeypatch.setattr(at, "_AI_TOOLS_FILE", p)
    monkeypatch.setattr(p.__class__, "write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    at._save_persisted({"a": 1})  # 不应抛出


def test_save_persisted_ok(monkeypatch, tmp_path):
    p = tmp_path / "w2.json"
    monkeypatch.setattr(at, "_AI_TOOLS_FILE", p)
    at._save_persisted({"a": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}


def test_remove_persisted_missing(monkeypatch):
    monkeypatch.setattr(at, "_load_persisted", lambda: {})
    monkeypatch.setattr(at, "_save_persisted", lambda t: None)
    assert at._remove_persisted("ghost") is False


def test_update_persisted(monkeypatch):
    store = {}
    monkeypatch.setattr(at, "_load_persisted", lambda: dict(store))
    def fake_save(tools):
        store.clear()
        store.update(tools)
    monkeypatch.setattr(at, "_save_persisted", fake_save)
    at._update_persisted("t", {"code": "x"})
    assert store == {"t": {"code": "x"}}


# ── _validate_tool_code 危险节点 ──────────────────────────────────────────────

def test_validate_forbids_exec():
    r = at._validate_tool_code("def f():\n    return exec('1')\n", "f")
    assert "exec" in r


def test_validate_forbids_compile():
    r = at._validate_tool_code("def f():\n    return compile('1','','eval')\n", "f")
    assert "compile" in r


def test_validate_forbids_importlib_attribute():
    r = at._validate_tool_code("def f():\n    return __import__('os')\n", "f")
    assert "__import__" in r


def test_validate_forbids_subprocess_call():
    r = at._validate_tool_code("def f():\n    os.system('ls')\n", "f")
    assert "os.system" in r
    r2 = at._validate_tool_code("def f():\n    subprocess.run(['ls'])\n", "f")
    assert "subprocess.run" in r2


def test_validate_bare_system_call_passes():
    # 裸 system() 调用不是 Attribute（无 os./subprocess. 前缀）→ 通过校验
    assert at._validate_tool_code("def f():\n    return system('ls')\n", "f") is None


def test_validate_whitespace_code():
    assert at._validate_tool_code("   \n", "f") == "代码不能为空"


# ── _create_and_register 分支 ─────────────────────────────────────────────────

def test_create_and_register_compile_error():
    r = at._create_and_register("bad_tool", "d", "def bad_tool(:\n")
    assert r["success"] is False
    assert "代码执行失败" in r["error"]


def test_create_and_register_missing_func():
    r = at._create_and_register("missing_tool", "d", "def other():\n    return 1\n")
    assert r["success"] is False
    assert "未找到函数定义" in r["error"]


def test_create_and_register_not_callable():
    code = "missing_tool = 42\n"
    r = at._create_and_register("missing_tool", "d", code)
    assert r["success"] is False
    assert "不是可调用的函数" in r["error"]


def test_create_and_register_register_exception(monkeypatch):
    def boom(**kw):
        raise Exception("reg fail")
    monkeypatch.setattr(at.ToolRegistry, "register_tool", boom)
    r = at._create_and_register("my_tool", "d", GOOD_CODE)
    assert r["success"] is False
    assert "工具注册失败" in r["error"]


def test_create_and_register_success_then_cleanup():
    code = "def ext_register_success(a):\n    return a * 2\n"
    r = at._create_and_register("ext_register_success", "d", code)
    assert r["success"] is True
    info = at.ToolRegistry.get_tool("ext_register_success")
    assert info is not None and info.source == "dynamic"
    at.ToolRegistry.unregister("ext_register_success")


# ── restore_ai_tools ──────────────────────────────────────────────────────────

def test_restore_empty(monkeypatch):
    monkeypatch.setattr(at, "_load_persisted", lambda: {})
    assert at.restore_ai_tools() == 0


def test_restore_missing_code(monkeypatch):
    monkeypatch.setattr(at, "_load_persisted", lambda: {"t1": {"description": "d"}})
    assert at.restore_ai_tools() == 0


def test_restore_success_and_failure(monkeypatch):
    ok_code = "def ok_tool():\n    return 1\n"
    monkeypatch.setattr(at, "_load_persisted", lambda: {
        "ok_tool": {"code": ok_code, "description": "d", "params": {}},
        "bad_tool": {"code": "def bad_tool(:\n", "description": "d", "params": {}},
    })
    monkeypatch.setattr(at, "_add_persisted", lambda *a, **k: None)
    n = at.restore_ai_tools()
    assert n == 1
    at.ToolRegistry.unregister("ok_tool")


# ── create_tool 分支 ──────────────────────────────────────────────────────────

def test_create_tool_invalid_identifier():
    r = at.create_tool(tool_name="not-valid", description="d", code=GOOD_CODE)
    assert "不是有效的 Python 标识符" in r


def test_create_tool_builtin_conflict(monkeypatch):
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: MagicMock(source="builtin"))
    r = at.create_tool(tool_name="calc", description="d", code=GOOD_CODE)
    assert "已被系统内置工具占用" in r


def test_create_tool_params_dict(monkeypatch):
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": True})
    monkeypatch.setattr(at, "_add_persisted", lambda *a, **k: None)
    r = at.create_tool(tool_name="my_tool", description="d", code=GOOD_CODE, params={"a": "左"})
    assert "创建成功" in r
    assert "参数: a" in r


def test_create_tool_params_valid_json(monkeypatch):
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": True})
    monkeypatch.setattr(at, "_add_persisted", lambda *a, **k: None)
    r = at.create_tool(tool_name="my_tool", description="d", code=GOOD_CODE,
                       params='{"x": "描述"}')
    assert "创建成功" in r
    assert "参数: x" in r


def test_create_tool_params_json_not_dict(monkeypatch):
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": True})
    r = at.create_tool(tool_name="my_tool", description="d", code=GOOD_CODE, params='[1,2]')
    assert "创建成功" in r


def test_create_tool_no_params(monkeypatch):
    captured = {}
    def fake_create(tool_name, description, code, params=None):
        captured["params"] = params
        return {"success": True}
    monkeypatch.setattr(at, "_create_and_register", fake_create)
    monkeypatch.setattr(at, "_add_persisted", lambda *a, **k: None)
    r = at.create_tool(tool_name="my_tool", description="d", code=GOOD_CODE)
    assert "创建成功" in r
    assert captured["params"] == {}


# ── list_my_tools 非空 ────────────────────────────────────────────────────────

def test_list_my_tools_with_items(monkeypatch):
    monkeypatch.setattr(at.ToolRegistry, "list_tools", lambda: {
        "t1": {"source": "dynamic", "tags": ["ai_tool"], "description": "d1", "params": {"a": "左"}, "registered_at": ""},
        "t2": {"source": "dynamic", "tags": ["ai_tool"], "description": "d2", "params": {}, "registered_at": ""},
        "calc": {"source": "builtin", "tags": [], "description": "", "params": {}},
    })
    r = at.list_my_tools()
    assert "自创工具 (2 个)" in r
    assert "🔧 t1" in r
    assert "(无参数)" in r


# ── delete_tool 分支 ──────────────────────────────────────────────────────────

def test_delete_tool_not_ai_tool(monkeypatch):
    info = MagicMock()
    info.source = "plugin"
    info.tags = ["file_rw"]
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    r = at.delete_tool("some_tool")
    assert "不是自创工具" in r


# ── edit_tool 分支 ────────────────────────────────────────────────────────────

def test_edit_tool_not_ai_tool(monkeypatch):
    info = MagicMock()
    info.source = "builtin"
    info.tags = []
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    r = at.edit_tool(tool_name="calc", description="d")
    assert "不是自创工具" in r


def test_edit_tool_bad_params(monkeypatch):
    info = MagicMock()
    info.source = "dynamic"
    info.tags = ["ai_tool"]
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    r = at.edit_tool(tool_name="my_tool", params="not-json")
    assert "JSON" in r


def test_edit_tool_params_dict_no_code(monkeypatch):
    info = MagicMock()
    info.source = "dynamic"
    info.tags = ["ai_tool"]
    info.func = lambda: 1
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    monkeypatch.setattr(at, "_load_persisted", lambda: {"my_tool": {"code": GOOD_CODE, "params": {}}})
    monkeypatch.setattr(at, "_update_persisted", lambda *a, **k: None)
    registered = {}
    def fake_register_tool(**kw):
        registered["params"] = kw.get("params")
    monkeypatch.setattr(at.ToolRegistry, "register_tool", fake_register_tool)
    r = at.edit_tool(tool_name="my_tool", description="新描述", params={"a": "左"})
    assert "更新成功" in r
    assert registered["params"] == {"a": "左"}


def test_edit_tool_code_fail_validation(monkeypatch):
    info = MagicMock()
    info.source = "dynamic"
    info.tags = ["ai_tool"]
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    r = at.edit_tool(tool_name="my_tool", code="def wrong(:\n")
    assert "代码验证失败" in r


def test_edit_tool_recreate_fail(monkeypatch):
    info = MagicMock()
    info.source = "dynamic"
    info.tags = ["ai_tool"]
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": False, "error": "boom"})
    r = at.edit_tool(tool_name="my_tool", code=GOOD_CODE)
    assert "重新注册失败" in r


def test_edit_tool_all_fields(monkeypatch):
    info = MagicMock()
    info.source = "dynamic"
    info.tags = ["ai_tool"]
    info.description = "old"
    monkeypatch.setattr(at.ToolRegistry, "get_tool", lambda n: info)
    monkeypatch.setattr(at, "_load_persisted", lambda: {"my_tool": {"code": GOOD_CODE, "params": {}}})
    monkeypatch.setattr(at, "_update_persisted", lambda *a, **k: None)
    monkeypatch.setattr(at, "_create_and_register", lambda *a, **k: {"success": True})
    r = at.edit_tool(tool_name="my_tool", description="d", code=GOOD_CODE, params="{}")
    assert "更新成功" in r
    assert "描述, 代码, 参数" in r
