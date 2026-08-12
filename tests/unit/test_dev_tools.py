"""dev_tools 测试（此前 10% 覆盖）：AST 分析 / 定义/引用 / 签名 / 依赖"""
from unittest.mock import patch

from infra.tool_manager.tools import dev_tools as dt


_SRC = '''import os
from typing import List

def add(a: int, b: int) -> int:
    """加法"""
    return a + b

class Calculator:
    def multiply(self, a, b):
        return a * b
'''


def _write(tmp_path):
    p = tmp_path / "sample.py"
    p.write_text(_SRC, encoding="utf-8")
    return p


def test_parse_ast(tmp_path):
    p = _write(tmp_path)
    r = dt.parse_ast(str(p))
    assert r["success"] is True
    assert any(f["name"] == "add" for f in r["functions"])
    assert any(c["name"] == "Calculator" for c in r["classes"])
    assert any("os" in i["names"] for i in r["imports"])


def test_parse_ast_include_body(tmp_path):
    p = _write(tmp_path)
    r = dt.parse_ast(str(p), include_body=True)
    add = [f for f in r["functions"] if f["name"] == "add"][0]
    assert "return a + b" in add["body"]


def test_parse_ast_missing_file():
    assert "不存在" in dt.parse_ast("/不存在/x.py")["error"]


def test_parse_ast_syntax_error(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("def f(:\n", encoding="utf-8")
    r = dt.parse_ast(str(p))
    assert "语法错误" in r["error"]


def test_find_definition(tmp_path):
    _write(tmp_path)
    r = dt.find_definition("add", path=str(tmp_path))
    assert r["success"] is True
    assert any(res["name"] if isinstance(res, dict) and "name" in res else res.get("type") for res in r["results"]) or r["count"] >= 0
    assert r["count"] >= 1


def test_find_definition_empty():
    assert "不能为空" in dt.find_definition("")["error"]


def test_find_references(tmp_path):
    _write(tmp_path)
    r = dt.find_references("add", path=str(tmp_path))
    assert r["success"] is True


def test_get_function_signature(tmp_path):
    p = _write(tmp_path)
    r = dt.get_function_signature(str(p), "add")
    assert r["success"] is True
    assert r["args"][0]["name"] == "a"
    assert "docstring" in r


def test_get_function_signature_missing(tmp_path):
    p = _write(tmp_path)
    r = dt.get_function_signature(str(p), "not_exist")
    assert "未找到" in r["error"]


def test_check_dependency(monkeypatch):
    import importlib.metadata as imd
    monkeypatch.setattr(imd, "version", lambda name: "3.11.0" if name == "os" else (_ for _ in ()).throw(imd.PackageNotFoundError(name)))
    r = dt.check_dependency("os")
    assert r["installed"] is True
    assert r["version"] == "3.11.0"
    r2 = dt.check_dependency("nonexistent_pkg_xyz")
    assert r2["installed"] is False


def test_install_dependency(monkeypatch):
    import subprocess as sp
    with patch.object(sp, "run") as m:
        m.return_value = __import__("types").SimpleNamespace(returncode=0, stdout="ok", stderr="")
        r = dt.install_dependency("requests")
    assert r["success"] is True


def test_list_dependencies(monkeypatch):
    import subprocess as sp
    with patch.object(sp, "run") as m:
        m.return_value = __import__("types").SimpleNamespace(returncode=0, stdout="pip==25.0\n", stderr="")
        r = dt.list_dependencies()
    assert r["success"] is True


def test_run_pytest(monkeypatch):
    import subprocess as sp
    with patch.object(sp, "run") as m:
        m.return_value = __import__("types").SimpleNamespace(returncode=0, stdout="2 passed", stderr="")
        r = dt.run_pytest()
    assert r["success"] is True
    assert "2 passed" in r["stdout"]
