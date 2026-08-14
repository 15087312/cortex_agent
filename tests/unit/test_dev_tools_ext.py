"""dev_tools 补充测试 — 运行类工具/复杂 AST 分支/目录工具 全路径覆盖"""
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.tools import dev_tools as dt


def _run_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestPyRun:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            lambda *a, **k: _run_result(0, "out", ""))
        r = dt._py_run(["compileall", "."])
        assert r["success"] is True
        assert r["stdout"] == "out"

    def test_timeout(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            MagicMock(side_effect=subprocess.TimeoutExpired("x", 1)))
        r = dt._py_run(["x"], timeout=1)
        assert r["success"] is False
        assert "超时" in r["error"]

    def test_file_not_found(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            MagicMock(side_effect=FileNotFoundError()))
        r = dt._py_run(["x"])
        assert r["success"] is False
        assert "未安装" in r["error"]

    def test_generic_exception(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run", MagicMock(side_effect=RuntimeError("boom")))
        r = dt._py_run(["x"])
        assert r["success"] is False
        assert "boom" in r["error"]


_EXT_SRC = '''import os
from typing import List

@decorator
def annotated(a: int, b: str) -> int:
    """doc"""
    return a

async def afunc(x):
    return x

class Base:
    pass

class Derived(Base):
    def method(self):
        pass
'''


def _write_ext(tmp_path):
    p = tmp_path / "sample.py"
    p.write_text(_EXT_SRC, encoding="utf-8")
    return p


class TestParseAstExt:
    def test_relative_path_resolves(self):
        r = dt.parse_ast("tests/conftest.py")
        assert r["success"] is True
        assert r["path"].endswith("tests/conftest.py")

    def test_decorators_and_annotations(self, tmp_path):
        p = _write_ext(tmp_path)
        r = dt.parse_ast(str(p))
        annotated = [f for f in r["functions"] if f["name"] == "annotated"][0]
        assert annotated["decorators"] == ["decorator"]
        derived = [c for c in r["classes"] if c["name"] == "Derived"][0]
        assert derived["bases"] == ["Base"]
        assert "method" in derived["methods"]

    def test_syntax_error(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def f(:\n", encoding="utf-8")
        assert "语法错误" in dt.parse_ast(str(p))["error"]


class TestFindDefinitionExt:
    def test_async_function_type(self, tmp_path):
        _write_ext(tmp_path)
        r = dt.find_definition("afunc", path=str(tmp_path))
        assert r["success"] is True
        assert r["results"][0]["type"] == "async_function"

    def test_skips_pycache(self, tmp_path):
        _write_ext(tmp_path)
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "afunc.py").write_text("def afunc():\n    pass\n", encoding="utf-8")
        r = dt.find_definition("afunc", path=str(tmp_path))
        assert r["count"] == 1


class TestFindReferencesExt:
    def test_empty_name(self):
        assert "不能为空" in dt.find_references("")["error"]

    def test_skips_pycache(self, tmp_path):
        _write_ext(tmp_path)
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "afunc.py").write_text("afunc\n", encoding="utf-8")
        r = dt.find_references("annotated", path=str(tmp_path))
        assert r["success"] is True


class TestGetFunctionSignatureExt:
    def test_relative_path(self):
        r = dt.get_function_signature("tests/conftest.py", "register_capabilities")
        assert r["success"] is True
        assert r["function"] == "register_capabilities"

    def test_annotations(self, tmp_path):
        p = _write_ext(tmp_path)
        r = dt.get_function_signature(str(p), "annotated")
        assert r["args"][0]["name"] == "a"
        assert "type" in r["args"][0]
        assert r["returns"] == "int"
        assert r["docstring"] == "doc"

    def test_async_function(self, tmp_path):
        p = _write_ext(tmp_path)
        r = dt.get_function_signature(str(p), "afunc")
        assert r["success"] is True
        assert r["returns"] is None

    def test_missing_file(self):
        assert "不存在" in dt.get_function_signature("/不存在/x.py", "f")["error"]


class TestDependencyExt:
    def test_check_empty(self):
        assert "不能为空" in dt.check_dependency("")["error"]

    def test_install_empty(self):
        assert "不能为空" in dt.install_dependency("")["error"]

    def test_install_invalid_name(self):
        r = dt.install_dependency("rm -rf /")
        assert "格式不合法" in r["error"]

    def test_install_success_with_upgrade(self, monkeypatch):
        calls = []
        monkeypatch.setattr(dt.subprocess, "run",
                            lambda cmd, **k: (calls.append(cmd), _run_result(0, "installed", ""))[1])
        r = dt.install_dependency("requests", upgrade=True)
        assert r["success"] is True
        assert "--upgrade" in calls[0]

    def test_install_timeout(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            MagicMock(side_effect=subprocess.TimeoutExpired("x", 1)))
        r = dt.install_dependency("requests")
        assert "超时" in r["error"]

    def test_install_generic_exception(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run", MagicMock(side_effect=RuntimeError("boom")))
        r = dt.install_dependency("requests")
        assert "boom" in r["error"]

    def test_list_dependencies_real(self):
        r = dt.list_dependencies()
        assert r["success"] is True
        assert r["count"] >= 1

    def test_list_dependencies_exception(self, monkeypatch):
        import importlib.metadata as md
        monkeypatch.setattr(md, "distributions", MagicMock(side_effect=RuntimeError("boom")))
        r = dt.list_dependencies()
        assert "boom" in r["error"]


class TestRunTools:
    def test_run_pytest_with_args(self, monkeypatch):
        calls = []
        monkeypatch.setattr(dt.subprocess, "run",
                            lambda cmd, **k: (calls.append(cmd), _run_result(0, "PASSED\nPASSED\nFAILED"))[1])
        r = dt.run_pytest(path="tests", verbose=False, args="-x --tb=short")
        assert r["success"] is True
        assert r["passed"] == 2
        assert r["failed"] == 1
        assert "-v" not in calls[0]
        assert "-x" in calls[0]

    def test_run_pytest_timeout(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            MagicMock(side_effect=subprocess.TimeoutExpired("x", 1)))
        r = dt.run_pytest()
        assert "超时" in r["error"]

    def test_run_pytest_not_found(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            MagicMock(side_effect=FileNotFoundError()))
        r = dt.run_pytest()
        assert "pytest 未安装" in r["error"]

    def test_run_pytest_generic(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run", MagicMock(side_effect=RuntimeError("boom")))
        r = dt.run_pytest()
        assert "boom" in r["error"]

    def test_run_ruff_with_issues(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            lambda cmd, **k: _run_result(1, "x.py:1:2 E501 line too long\n", ""))
        r = dt.run_ruff(path="x.py")
        assert r["success"] is False
        assert r["issues_count"] == 1

    def test_run_ruff_not_found(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            MagicMock(side_effect=FileNotFoundError()))
        r = dt.run_ruff()
        assert "ruff 未安装" in r["error"]

    def test_run_ruff_generic(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run", MagicMock(side_effect=RuntimeError("boom")))
        r = dt.run_ruff()
        assert "boom" in r["error"]

    def test_run_black_empty_path(self):
        assert "路径不能为空" in dt.run_black("")["error"]

    def test_run_black_would_reformat(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            lambda cmd, **k: _run_result(1, "would reformat", ""))
        r = dt.run_black("x.py")
        assert r["would_reformat"] is True

    def test_run_black_not_found(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            MagicMock(side_effect=FileNotFoundError()))
        r = dt.run_black("x.py")
        assert "black 未安装" in r["error"]

    def test_run_black_generic(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run", MagicMock(side_effect=RuntimeError("boom")))
        r = dt.run_black("x.py")
        assert "boom" in r["error"]


class TestDebugCode:
    def test_empty_code(self):
        assert "不能为空" in dt.debug_code("")["error"]

    def test_success_with_steps(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run",
                            lambda cmd, **k: _run_result(0, '__DEBUG_RESULT__[{"line": 3}]', ""))
        r = dt.debug_code("x = 1")
        assert r["success"] is True
        assert r["steps"] == [{"line": 3}]

    def test_success_without_marker(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run", lambda cmd, **k: _run_result(0, "plain output", ""))
        r = dt.debug_code("x = 1")
        assert r["success"] is True
        assert r["stdout"] == "plain output"

    def test_exception(self, monkeypatch):
        monkeypatch.setattr(dt.subprocess, "run", MagicMock(side_effect=RuntimeError("boom")))
        r = dt.debug_code("x = 1")
        assert "boom" in r["error"]


class TestCodeQuality:
    def test_complexity(self, tmp_path):
        p = tmp_path / "c.py"
        p.write_text("""def simple():
    return 1

def complex_fn(a):
    if a:
        for i in range(a):
            while i:
                break
    return a
""", encoding="utf-8")
        r = dt.calculate_cyclomatic_complexity(str(p))
        assert r["success"] is True
        by_name = {x["name"]: x for x in r["results"]}
        assert by_name["simple"]["complexity"] == 1
        assert by_name["complex_fn"]["complexity"] >= 4

    def test_complexity_missing_file(self):
        assert "不存在" in dt.calculate_cyclomatic_complexity("/不存在/x.py")["error"]

    def test_complexity_relative(self):
        r = dt.calculate_cyclomatic_complexity("tests/conftest.py")
        assert r["success"] is True

    def test_complexity_syntax_error(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def f(:\n", encoding="utf-8")
        assert "语法错误" in dt.calculate_cyclomatic_complexity(str(p))["error"]

    def test_smells(self, tmp_path):
        p = tmp_path / "smell.py"
        p.write_text("def long(a1, a2, a3, a4, a5, a6):\n"
                     + "".join("    x = %d\n" % i for i in range(60)), encoding="utf-8")
        r = dt.detect_code_smells(str(p))
        assert r["success"] is True
        types = {s["type"] for s in r["smells"]}
        assert "long_function" in types
        assert "too_many_params" in types
        assert "missing_docstring" in types

    def test_smells_missing_file(self):
        assert "不存在" in dt.detect_code_smells("/不存在/x.py")["error"]

    def test_generate_documentation(self, tmp_path):
        p = _write_ext(tmp_path)
        r = dt.generate_documentation(str(p), function_name="annotated")
        assert r["success"] is True
        assert r["total"] == 1
        assert "annotated" in r["documentation"][0]["generated_docstring"]

    def test_generate_documentation_all(self, tmp_path):
        p = _write_ext(tmp_path)
        r = dt.generate_documentation(str(p))
        assert r["success"] is True
        assert r["total"] >= 3

    def test_generate_documentation_missing(self):
        assert "不存在" in dt.generate_documentation("/不存在/x.py")["error"]


class TestDirectoryTree:
    def _make_tree(self, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b", encoding="utf-8")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".hidden").write_text("h", encoding="utf-8")

    def test_basic(self, tmp_path):
        self._make_tree(tmp_path)
        r = dt.directory_tree(str(tmp_path), max_depth=3)
        assert r["success"] is True
        assert "a.txt" in r["tree"]
        assert "sub/" in r["tree"]
        assert "__pycache__" not in r["tree"]
        assert ".hidden" not in r["tree"]

    def test_include_hidden(self, tmp_path):
        self._make_tree(tmp_path)
        r = dt.directory_tree(str(tmp_path), include_hidden=True)
        assert ".hidden" in r["tree"]

    def test_exclude_patterns(self, tmp_path):
        self._make_tree(tmp_path)
        r = dt.directory_tree(str(tmp_path), exclude_patterns=["sub"])
        assert "sub" not in r["tree"]

    def test_max_depth_clamped(self, tmp_path):
        self._make_tree(tmp_path)
        r = dt.directory_tree(str(tmp_path), max_depth=99)
        assert r["max_depth"] == 6
        r2 = dt.directory_tree(str(tmp_path), max_depth=0)
        assert r2["max_depth"] == 3

    def test_relative_path(self):
        r = dt.directory_tree("tests")
        assert r["success"] is True

    def test_missing_path(self, tmp_path):
        r = dt.directory_tree(str(tmp_path / "missing"))
        assert "路径不存在" in r["error"]

    def test_not_dir(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("x", encoding="utf-8")
        r = dt.directory_tree(str(p))
        assert "不是目录" in r["error"]

    def test_permission_error(self, tmp_path, monkeypatch):
        self._make_tree(tmp_path)
        monkeypatch.setattr(dt.Path, "iterdir", MagicMock(side_effect=PermissionError("denied")))
        r = dt.directory_tree(str(tmp_path))
        assert r["success"] is True
        assert "[权限拒绝]" in r["tree"]

    def test_os_error(self, tmp_path, monkeypatch):
        self._make_tree(tmp_path)
        monkeypatch.setattr(dt.Path, "iterdir", MagicMock(side_effect=OSError("io")))
        r = dt.directory_tree(str(tmp_path))
        assert "[读取错误]" in r["tree"]


class TestListDirectory:
    def test_success(self, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        r = dt.list_directory(str(tmp_path))
        assert r["success"] is True
        assert r["files"] == ["a.txt"]
        assert r["directories"] == ["sub/"]
        assert r["total"] == 2

    def test_missing(self):
        assert "路径不存在" in dt.list_directory("/不存在/x")["error"]

    def test_not_dir(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("x", encoding="utf-8")
        assert "不是目录" in dt.list_directory(str(p))["error"]

    def test_permission_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dt.Path, "iterdir", MagicMock(side_effect=PermissionError("denied")))
        r = dt.list_directory(str(tmp_path))
        assert "权限拒绝" in r["error"]


class TestReadWriteTextFile:
    def test_read_success(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("content", encoding="utf-8")
        r = dt.read_text_file(str(p))
        assert r["success"] is True
        assert r["content"] == "content"
        assert r["truncated"] is False

    def test_read_truncated(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("x" * 100, encoding="utf-8")
        r = dt.read_text_file(str(p), max_length=10)
        assert r["truncated"] is True
        assert "[截断]" in r["content"]

    def test_read_missing(self):
        assert "文件不存在" in dt.read_text_file("/不存在/x.py")["error"]

    def test_read_not_file(self, tmp_path):
        assert "不是文件" in dt.read_text_file(str(tmp_path))["error"]

    def test_read_exception(self, tmp_path, monkeypatch):
        p = tmp_path / "a.py"
        p.write_text("x", encoding="utf-8")
        monkeypatch.setattr(dt.Path, "read_text", MagicMock(side_effect=OSError("boom")))
        r = dt.read_text_file(str(p))
        assert "读取失败" in r["error"]

    def test_write_empty_path(self):
        assert "path 不能为空" in dt.write_text_file("", "x")["error"]

    def test_write_no_overwrite(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("old", encoding="utf-8")
        r = dt.write_text_file(str(p), "new", overwrite=False)
        assert "overwrite=False" in r["error"]
        assert p.read_text(encoding="utf-8") == "old"

    def test_write_success(self, tmp_path):
        p = tmp_path / "sub" / "a.py"
        r = dt.write_text_file(str(p), "new", overwrite=True)
        assert r["success"] is True
        assert p.read_text(encoding="utf-8") == "new"
        assert r["action"] == "updated"

    def test_write_update(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("old", encoding="utf-8")
        r = dt.write_text_file(str(p), "new")
        assert r["action"] == "updated"

    def test_write_exception(self, tmp_path, monkeypatch):
        p = tmp_path / "a.py"
        monkeypatch.setattr(dt.Path, "write_text", MagicMock(side_effect=OSError("boom")))
        r = dt.write_text_file(str(p), "x")
        assert "写入失败" in r["error"]
