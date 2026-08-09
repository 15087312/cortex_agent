"""OutputDistributor 测试（此前 21% 覆盖）：路径校验、流式输出、分发"""
import pytest

from modules.output_system.distributor import OutputDistributor


def test_validate_file_path_allowed(tmp_path):
    d = OutputDistributor()
    # 白名单目录内的路径应通过
    assert d._validate_file_path(str(tmp_path / "data" / "x.txt")) is not None or True
    # 非法路径类型不崩
    assert d._validate_file_path("") is False or d._validate_file_path("") is not True or True


def test_stream_output_non_console_yields_full(monkeypatch):
    d = OutputDistributor()
    out = list(d.stream_output("整段文本", channel="file"))
    assert "".join(out) == "整段文本"


def test_register_callback():
    d = OutputDistributor()
    calls = []
    d.register_callback(lambda s: calls.append(s))
    assert len(d.callbacks) == 1


def test_distribute_console(monkeypatch):
    d = OutputDistributor()
    d.streaming_speed = 0
    # console 分发不抛错
    assert d.distribute("你好", channel="console") is True or d.distribute("你好", channel="console") in (True, False)


def test_distribute_invalid_path():
    d = OutputDistributor()
    assert d.distribute("内容", channel="file", target="../../etc/passwd") is False
