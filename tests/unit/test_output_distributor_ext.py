"""OutputDistributor 补测：路径校验 / 流式输出 / 多渠道分发 / 异常分支"""
import io

from modules.output_system.distributor import OutputDistributor, ALLOWED_OUTPUT_DIRS


def _distributor(tmp_path, monkeypatch):
    d = OutputDistributor()
    d.streaming_speed = 0
    allowed = [tmp_path / "data_out", tmp_path / "logs"]
    for p in allowed:
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "modules.output_system.distributor.ALLOWED_OUTPUT_DIRS",
        [p.resolve() for p in allowed],
    )
    return d


# ── 路径校验 ───────────────────────────────────────────────────────────

def test_validate_file_path_allowed(tmp_path, monkeypatch):
    d = _distributor(tmp_path, monkeypatch)
    target = tmp_path / "data_out" / "sub" / "x.txt"
    assert d._validate_file_path(str(target)) is True


def test_validate_file_path_outside(tmp_path, monkeypatch):
    d = _distributor(tmp_path, monkeypatch)
    assert d._validate_file_path(str(tmp_path / "evil" / "x.txt")) is False


def test_validate_file_path_invalid(tmp_path, monkeypatch):
    """Path.resolve() 抛 OSError → 返回 False"""
    d = _distributor(tmp_path, monkeypatch)
    with monkeypatch.context() as mp:
        mp.setattr("pathlib.Path.resolve", lambda self, *a, **k: (_ for _ in ()).throw(OSError("bad")))
        assert d._validate_file_path("/whatever") is False


# ── 流式输出 ───────────────────────────────────────────────────────────

def test_stream_output_console_chars(monkeypatch):
    d = OutputDistributor()
    d.streaming_speed = 0
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    out = list(d.stream_output("a\n b\t", channel="console"))
    assert "".join(out) == "a\n b\t"
    assert buf.getvalue() == "a\n b\t"


def test_stream_output_non_console_yields_full(monkeypatch):
    d = OutputDistributor()
    out = list(d.stream_output("整段文本", channel="file"))
    assert "".join(out) == "整段文本"


# ── 回调注册 ───────────────────────────────────────────────────────────

def test_register_callback():
    d = OutputDistributor()
    calls = []
    d.register_callback(lambda s: calls.append(s))
    assert len(d.callbacks) == 1


# ── distribute ─────────────────────────────────────────────────────────

def test_distribute_console_with_callbacks():
    d = OutputDistributor()
    calls = []
    d.register_callback(lambda s: calls.append(s))
    assert d.distribute("你好", channel="console") is True
    assert calls == ["你好"]


def test_distribute_file(tmp_path, monkeypatch):
    d = _distributor(tmp_path, monkeypatch)
    target = tmp_path / "data_out" / "out.txt"
    assert d.distribute("内容", channel="file", target=str(target)) is True
    assert target.read_text(encoding="utf-8") == "内容"


def test_distribute_file_bad_path(tmp_path, monkeypatch):
    d = _distributor(tmp_path, monkeypatch)
    assert d.distribute("内容", channel="file", target=str(tmp_path / "bad" / "x.txt")) is False


def test_distribute_api_and_voice():
    d = OutputDistributor()
    assert d.distribute("x", channel="api") is True
    assert d.distribute("x", channel="voice") is True


def test_distribute_exception(tmp_path, monkeypatch):
    d = _distributor(tmp_path, monkeypatch)
    target = tmp_path / "data_out" / "out.txt"
    with monkeypatch.context() as mp:
        def boom(path, *a, **k):
            raise RuntimeError("disk full")
        mp.setattr("builtins.open", boom)
        assert d.distribute("x", channel="file", target=str(target)) is False


# ── distribute_stream ──────────────────────────────────────────────────

def test_distribute_stream():
    d = OutputDistributor()
    calls = []
    d.register_callback(lambda s: calls.append(s))
    d.distribute_stream((c for c in ["你", "好"]))
    assert calls == ["你好"]
