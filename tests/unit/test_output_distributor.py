"""OutputDistributor 测试：路径校验、流式输出、分发"""
import pytest

from pathlib import Path
from modules.output_system.distributor import OutputDistributor


def test_validate_file_path_allowed(tmp_path):
    """白名单目录（data/output）内的路径应通过校验（返回 True）"""
    d = OutputDistributor()
    allowed = str(Path("data/output").resolve() / "x.txt")
    assert d._validate_file_path(allowed) is True


def test_validate_file_path_outside_whitelist():
    """白名单外路径 → 校验失败（返回 False）"""
    d = OutputDistributor()
    import tempfile
    assert d._validate_file_path(str(Path(tempfile.gettempdir()) / "evil.txt")) is False


def test_validate_file_path_empty():
    """空路径 → 校验失败（返回 False）"""
    d = OutputDistributor()
    assert d._validate_file_path("") is False


def test_stream_output_non_console_yields_full(monkeypatch):
    d = OutputDistributor()
    out = list(d.stream_output("整段文本", channel="file"))
    assert "".join(out) == "整段文本"


def test_stream_output_console_streams_chars(monkeypatch):
    """console 通道逐字符输出 + 回调"""
    d = OutputDistributor()
    d.streaming_speed = 0
    seen = []
    monkeypatch.setattr("modules.output_system.distributor.sys.stdout", type("S", (), {
        "write": lambda self, s: seen.append(s),
        "flush": lambda self: None,
    })())
    out = list(d.stream_output("ab\n", channel="console"))
    assert "".join(out) == "ab\n"


def test_register_callback():
    d = OutputDistributor()
    calls = []
    d.register_callback(lambda s: calls.append(s))
    assert len(d.callbacks) == 1
    assert d.distribute("你好", channel="console") is True
    assert calls == ["你好"]


def test_distribute_file_valid(monkeypatch, tmp_path):
    """分发到白名单内文件 → 写文件返回 True"""
    d = OutputDistributor()
    import os
    os.makedirs("data/output", exist_ok=True)
    target = str(Path("data/output").resolve() / "e2e_test_out.txt")
    try:
        ok = d.distribute("内容", channel="file", target=target)
        assert ok is True
        assert open(target, encoding="utf-8").read() == "内容"
    finally:
        if os.path.exists(target):
            os.remove(target)


def test_distribute_invalid_path():
    """分发到白名单外路径 → 返回 False"""
    d = OutputDistributor()
    assert d.distribute("内容", channel="file", target="../../etc/passwd") is False


def test_distribute_api_voice(monkeypatch):
    """api / voice 通道分发不崩，返回 True"""
    d = OutputDistributor()
    assert d.distribute("x", channel="api") is True
    assert d.distribute("x", channel="voice") is True
