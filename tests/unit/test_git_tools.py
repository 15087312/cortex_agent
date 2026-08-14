"""git_tools 测试（mock subprocess，验证参数防护与输出解析）"""
from unittest.mock import MagicMock, patch

from infra.tool_manager.tools import git_tools


class _Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ── _run_git 基础 ───────────────────────────────────────────────────────────

def test_run_git_success():
    with patch("subprocess.run", return_value=_Result(stdout=" M file.py\n", returncode=0)) as m:
        r = git_tools._run_git(["status"], "/tmp")
        assert r["success"] is True
        assert r["stdout"] == "M file.py"
        m.assert_called_once()


def test_run_git_timeout():
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
        r = git_tools._run_git(["status"])
        assert r["success"] is False
        assert "超时" in r["error"]


def test_run_git_not_installed():
    import subprocess
    with patch("subprocess.run", side_effect=FileNotFoundError):
        r = git_tools._run_git(["status"])
        assert r["success"] is False
        assert "未安装" in r["error"]


# ── git_status 输出解析 ─────────────────────────────────────────────────────

def test_git_status_parses_categories():
    # porcelain XY 格式：MM=双重修改、前导空格 M=仅工作区修改、??=未跟踪
    porcelain = " M 未暂存.py\nMM 已暂存.py\n?? 未跟踪.py\n"
    with patch("infra.tool_manager.tools.git_tools._run_git",
               return_value={"stdout": porcelain, "success": True, "stderr": "", "exit_code": 0}):
        r = git_tools.git_status()
    assert "已暂存.py" in r["staged"]
    assert "未暂存.py" in r["unstaged"]
    assert "未跟踪.py" in r["untracked"]
    assert r["total"] == 3


def test_git_status_error():
    with patch("infra.tool_manager.tools.git_tools._run_git", return_value={"success": False, "error": "x"}):
        r = git_tools.git_status()
    assert r["success"] is False


# ── 参数防护 ────────────────────────────────────────────────────────────────

def test_git_add_empty_path():
    r = git_tools.git_add("")
    assert r["success"] is False


def test_git_commit_empty_message():
    r = git_tools.git_commit("   ")
    assert r["success"] is False


def test_git_push_rejects_force_injection():
    r = git_tools.git_push(remote="-f")
    assert r["success"] is False
    r2 = git_tools.git_push(remote="origin", branch="--force")
    assert r2["success"] is False
    r3 = git_tools.git_push(remote="origin", branch="main --force")
    assert r3["success"] is False


def test_git_push_valid_builds_cmd():
    captured = {}

    def fake_run(args, workdir=None):
        captured["args"] = args
        return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

    with patch("infra.tool_manager.tools.git_tools._run_git", side_effect=fake_run):
        r = git_tools.git_push(remote="origin", branch="main")
    assert r["success"] is True
    assert captured["args"] == ["push", "origin", "main"]


def test_git_pull_builds_cmd():
    captured = {}

    def fake_run(args, workdir=None):
        captured["args"] = args
        return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

    with patch("infra.tool_manager.tools.git_tools._run_git", side_effect=fake_run):
        git_tools.git_pull(remote="origin", branch="main")
    assert captured["args"] == ["pull", "origin", "main"]


# ── git_diff 统计 ───────────────────────────────────────────────────────────

def test_git_diff_counts_lines():
    diff = "--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,3 @@\n+新增行\n-删除行\n+另一行\n"
    with patch("infra.tool_manager.tools.git_tools._run_git",
               return_value={"stdout": diff, "success": True, "stderr": "", "exit_code": 0}):
        r = git_tools.git_diff(staged=True, path="f.py")
    assert r["added_lines"] == 2
    assert r["removed_lines"] == 1


def test_git_diff_marks_only_pure_add_remove():
    # +++/--- 头行不应计入
    diff = "+++ b/f.py\n--- a/f.py\n+实际新增\n-实际删除\n"
    with patch("infra.tool_manager.tools.git_tools._run_git",
               return_value={"stdout": diff, "success": True, "stderr": "", "exit_code": 0}):
        r = git_tools.git_diff()
    assert r["added_lines"] == 1
    assert r["removed_lines"] == 1


# ── 防御性分支：异常回退 / 成功路径 / 失败透传 ───────────────────────────────

def test_run_git_generic_exception():
    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        r = git_tools._run_git(["status"])
        assert r["success"] is False
        assert "boom" in r["error"]


def test_git_add_success():
    captured = {}

    def fake_run(args, workdir=None):
        captured["args"] = args
        return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

    with patch("infra.tool_manager.tools.git_tools._run_git", side_effect=fake_run):
        r = git_tools.git_add("x.py")
    assert r["success"] is True
    assert captured["args"] == ["add", "x.py"]


def test_git_commit_success():
    captured = {}

    def fake_run(args, workdir=None):
        captured["args"] = args
        return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

    with patch("infra.tool_manager.tools.git_tools._run_git", side_effect=fake_run):
        r = git_tools.git_commit("  message  ")
    assert r["success"] is True
    assert captured["args"] == ["commit", "-m", "message"]


def test_git_diff_failure_passthrough():
    with patch("infra.tool_manager.tools.git_tools._run_git",
               return_value={"success": False, "error": "not a repo"}):
        r = git_tools.git_diff()
    assert r["success"] is False
    assert "added_lines" not in r
