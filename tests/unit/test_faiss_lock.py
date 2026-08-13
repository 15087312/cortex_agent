"""utils/faiss_lock.py — FAISS 跨实例文件锁"""
import os

import pytest

import utils.faiss_lock as fl


def test_context_manager_creates_lockfile(tmp_path):
    target = str(tmp_path / "events_faiss.index")
    with fl.faiss_file_lock(target):
        # 锁文件独立存在，数据文件不被触碰
        assert os.path.exists(target + ".lock")
        assert not os.path.exists(target)
    # 释放后再次获取应可重入
    with fl.faiss_file_lock(target):
        pass


def test_reentrant_nested(tmp_path):
    target = str(tmp_path / "idx")
    pl = fl._get_path_lock(target)
    pl.acquire()
    pl.acquire()  # 进程内可重入
    assert pl._depth == 2
    pl.release()
    assert pl._depth == 1
    pl.release()
    assert pl._depth == 0
    assert pl._fd is None


def test_registry_same_path_same_lock(tmp_path):
    target = str(tmp_path / "idx")
    a = fl._get_path_lock(target)
    b = fl._get_path_lock(target)
    c = fl._get_path_lock(str(tmp_path / "other"))
    assert a is b
    assert a is not c


def test_context_manager_release_on_exception(tmp_path):
    target = str(tmp_path / "idx")
    pl = fl._get_path_lock(target)
    with pytest.raises(RuntimeError):
        with fl.faiss_file_lock(target):
            raise RuntimeError("boom")
    assert pl._depth == 0
    assert pl._fd is None


def test_no_fcntl_falls_back_to_thread_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "fcntl", None)
    target = str(tmp_path / "idx")
    with fl.faiss_file_lock(target):
        pass
    # 无 fcntl 时锁文件仍创建，仅靠线程锁
    assert os.path.exists(target + ".lock")


def test_flock_failure_closes_fd(monkeypatch, tmp_path):
    class FakeFcntl:
        LOCK_EX = 2
        LOCK_UN = 8

        @staticmethod
        def flock(fd, op):
            raise OSError("flock failed")

    monkeypatch.setattr(fl, "fcntl", FakeFcntl)
    target = str(tmp_path / "idx")
    pl = fl._get_path_lock(target)
    with pytest.raises(OSError):
        pl.acquire()
    # 失败后 fd 已关闭，不残留
    assert pl._fd is None
