"""cortex/watchdog 测试：父进程存活检测 / enable 各分支"""
import os

import cortex.watchdog as wd


def test_parent_alive_low_pid():
    assert wd._parent_alive(0) is False
    assert wd._parent_alive(1) is False


def test_parent_alive_process_lookup_error(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    assert wd._parent_alive(100) is False


def test_parent_alive_permission_error(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert wd._parent_alive(100) is True


def test_parent_alive_oserror(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))
    assert wd._parent_alive(100) is True


def test_parent_alive_ok(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: None)
    assert wd._parent_alive(100) is True


def test_enable_no_env(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "")
    wd._started = False
    assert wd.enable() is False


def test_enable_invalid_env(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "not-a-pid")
    wd._started = False
    assert wd.enable() is False


def test_enable_self_pid(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": str(os.getpid()))
    wd._started = False
    assert wd.enable() is False


def test_enable_low_pid(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "1")
    wd._started = False
    assert wd.enable() is False


def test_enable_starts_thread(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "99999")
    started = []
    monkeypatch.setattr(wd.threading.Thread, "start", lambda self: started.append(True))
    wd._started = False
    assert wd.enable() is True
    assert started
    assert wd.enable() is True  # 已启动 → 直接返回 True，不重复起线程
    assert len(started) == 1
    wd._started = False


# ── 后端锚定 bind_backend ──


def _reset_backend_binding(monkeypatch, env="", pidfile=0):
    """重置绑定状态并固定 env / PID 文件读取"""
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": env if k == "CORTEX_BACKEND_PID" else d)
    monkeypatch.setattr(wd, "_read_backend_pid_file", lambda: pidfile)
    monkeypatch.setattr(wd.threading.Thread, "start", lambda self: None)
    wd._bound = False


def test_bind_backend_env_pid(monkeypatch):
    _reset_backend_binding(monkeypatch, env="99999")
    assert wd.bind_backend(fail_after=0) is True
    assert wd._bound is True
    wd._bound = False


def test_bind_backend_pid_file(monkeypatch):
    _reset_backend_binding(monkeypatch, env="", pidfile=88888)
    assert wd.bind_backend(fail_after=0) is True
    assert wd._bound is True
    wd._bound = False


def test_bind_backend_env_overrides_file(monkeypatch):
    """环境变量优先于 PID 文件"""
    _reset_backend_binding(monkeypatch, env="99999", pidfile=88888)
    seen = []
    orig = wd._bind_watch
    monkeypatch.setattr(wd.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": staticmethod(lambda: seen.append(k)), "daemon": True}))
    wd.bind_backend(fail_after=0)
    wd._bound = False


def test_bind_backend_no_backend_no_wait(monkeypatch):
    """无 env 无文件且 fail_after=0 → 不启用"""
    _reset_backend_binding(monkeypatch, env="", pidfile=0)
    assert wd.bind_backend(fail_after=0) is False
    assert wd._bound is False


def test_bind_backend_self_pid_rejected(monkeypatch):
    _reset_backend_binding(monkeypatch, env=str(os.getpid()))
    assert wd.bind_backend(fail_after=0) is False
    wd._bound = False


def test_bind_backend_waits_for_backend(monkeypatch):
    """fail_after>0 时等待后端出现（PID 文件稍后写入的场景）"""
    calls = {"n": 0}
    def _fake_read():
        calls["n"] += 1
        return 99999 if calls["n"] >= 2 else 0
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "")
    monkeypatch.setattr(wd, "_read_backend_pid_file", _fake_read)
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    monkeypatch.setattr(wd.threading.Thread, "start", lambda self: None)
    wd._bound = False
    assert wd.bind_backend(fail_after=30) is True
    wd._bound = False


def test_bind_backend_idempotent(monkeypatch):
    _reset_backend_binding(monkeypatch, env="99999")
    assert wd.bind_backend(fail_after=0) is True
    assert wd.bind_backend(fail_after=0) is True  # 已绑定 → 直接返回
    wd._bound = False


def test_pid_file_roundtrip(tmp_path, monkeypatch):
    """write/clear backend_pid.json 往返 + clear 的归属保护"""
    pid_file = tmp_path / "backend_pid.json"
    monkeypatch.setattr(wd, "_BACKEND_PID_FILE", str(pid_file))
    wd.write_backend_pid_file(4242)
    assert wd._read_backend_pid_file() == 4242
    # 文件里是别的 PID 时不许删（新后端已接管）
    wd.clear_backend_pid_file(1111)
    assert wd._read_backend_pid_file() == 4242
    # PID 匹配才删
    wd.clear_backend_pid_file(4242)
    assert wd._read_backend_pid_file() == 0


def test_bind_watch_exits_when_backend_dies(monkeypatch):
    """env 绑定: 后端死亡 → os._exit"""
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "99999" if k == "CORTEX_BACKEND_PID" else d)
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    monkeypatch.setattr(wd, "_parent_alive", lambda pid: False)
    exited = []
    monkeypatch.setattr(wd.os, "_exit", lambda code: exited.append(code))
    wd._bind_watch(99999, 0.01)
    assert exited == [0]


def test_bind_watch_follows_new_backend_pid(monkeypatch):
    """PID 文件绑定: 后端重启换 PID → 跟随新 PID；新 PID 也死后才退出"""
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "")
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    # 99999 死亡 → 跟随文件里的 88888；88888 也死亡 → 文件已空 → 退出
    alive = {99999: False, 88888: False}
    monkeypatch.setattr(wd, "_parent_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(wd, "_read_backend_pid_file", lambda: 88888)
    exited = []
    monkeypatch.setattr(wd.os, "_exit", lambda code: exited.append(code))
    wd._bind_watch(99999, 0.01)
    assert exited == [0]


def test_bind_watch_env_no_follow(monkeypatch):
    """env 绑定精确: 死了就退，不跟随文件里的新 PID"""
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "99999" if k == "CORTEX_BACKEND_PID" else d)
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    monkeypatch.setattr(wd, "_parent_alive", lambda pid: False)
    monkeypatch.setattr(wd, "_read_backend_pid_file", lambda: 88888)
    exited = []
    monkeypatch.setattr(wd.os, "_exit", lambda code: exited.append(code))
    wd._bind_watch(99999, 0.01)
    assert exited == [0]
