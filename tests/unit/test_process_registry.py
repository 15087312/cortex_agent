"""后端子进程登记表 — 登记去重 / 快照隔离 / 退出收编

后端拉起的常驻子进程（caffeinate 等）如果不在 shutdown 时收编，重启后会残留。
这里用假 Popen 钉住「先 SIGTERM、超时再 SIGKILL、异常也不外抛」的收编语义。
"""
import subprocess

import pytest

from infra import process_registry as reg


class FakeProc:
    def __init__(self, alive=True, wait_error=None):
        self.alive = alive
        self.wait_error = wait_error
        self.terminated = 0
        self.killed = 0
        self.waits = []

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.wait_error:
            raise self.wait_error
        self.alive = False
        return 0

    def kill(self):
        self.killed += 1


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(reg, "_children", [])
    yield
    reg._children.clear()


def test_register_ignores_none():
    reg.register("caffeinate", None)
    assert reg.snapshot() == []


def test_register_replaces_same_name():
    first, second = FakeProc(), FakeProc()
    reg.register("caffeinate", first)
    reg.register("caffeinate", second)
    assert reg.snapshot() == [("caffeinate", second)]


def test_register_keeps_distinct_names_in_order():
    a, b = FakeProc(), FakeProc()
    reg.register("a", a)
    reg.register("b", b)
    assert [n for n, _ in reg.snapshot()] == ["a", "b"]


def test_unregister_removes_only_named_child():
    a, b = FakeProc(), FakeProc()
    reg.register("a", a)
    reg.register("b", b)
    reg.unregister("a")
    assert reg.snapshot() == [("b", b)]


def test_snapshot_is_a_copy():
    proc = FakeProc()
    reg.register("a", proc)
    snap = reg.snapshot()
    snap.append(("fake", FakeProc()))
    assert len(reg.snapshot()) == 1


def test_shutdown_terminates_live_children_and_clears():
    live, dead = FakeProc(), FakeProc(alive=False)
    reg.register("live", live)
    reg.register("dead", dead)

    reg.shutdown_all_children(timeout=1.5)
    assert live.terminated == 1 and live.waits == [1.5]
    assert live.killed == 0
    assert dead.terminated == 0            # 已退出的不再动
    assert reg.snapshot() == []            # 幂等：登记表已清空


def test_shutdown_kills_when_wait_times_out():
    slow = FakeProc(wait_error=subprocess.TimeoutExpired("x", 1))
    reg.register("slow", slow)
    reg.shutdown_all_children()
    assert slow.terminated == 1
    assert slow.killed == 1


def test_shutdown_swallows_terminate_errors():
    class Hostile(FakeProc):
        def terminate(self):
            raise OSError("no such process")

    hostile = Hostile()
    reg.register("hostile", hostile)
    reg.shutdown_all_children()
    assert hostile.killed == 1


def test_shutdown_twice_is_noop():
    proc = FakeProc()
    reg.register("a", proc)
    reg.shutdown_all_children()
    reg.shutdown_all_children()
    assert proc.terminated == 1
