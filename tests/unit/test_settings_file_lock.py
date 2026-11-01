"""config/settings.py — 配置文件跨进程写锁（docs/ERRORS_AND_FIXES.md §96）

验证 `_modify_personas_yaml` / `save_user_config` 在 read-modify-write 期间
同时持有进程内锁与跨进程文件锁，且 faiss_lock 不可用时降级为仅线程锁、不抛异常。

所有 ~/.cortex/* 路径均重定向到 tmp_path，不触碰真实用户配置。
"""
import contextlib
import json
import os
import sys
from pathlib import Path

import pytest

from config.settings import Settings


@pytest.fixture(autouse=True)
def _sys_shim(monkeypatch):
    """_ensure_user_config 用到 sys.stderr 但模块未 import sys（生产既有现象）。"""
    _cs = sys.modules["config.settings"]
    if not hasattr(_cs, "sys"):
        monkeypatch.setattr(_cs, "sys", sys, raising=False)


def _new_settings(tmp_path, monkeypatch, personas_yaml=None):
    """构建完全隔离的 Settings：personas.yaml / settings.json 均落在 tmp_path。"""
    cortex = tmp_path / ".cortex"
    cortex.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Settings, "_USER_CONFIG_PATH", cortex / "settings.json")
    # conftest 已把 _personas_yaml_path 重定向到别的临时目录，这里再覆盖为本用例的 tmp_path
    monkeypatch.setattr(
        Settings, "_personas_yaml_path",
        property(lambda self: cortex / "personas.yaml"),
    )
    if personas_yaml is not None:
        (cortex / "personas.yaml").write_text(personas_yaml, encoding="utf-8")
    return Settings(_env_file=None)


# ---------------------------------------------------------------------------
# personas.yaml：跨进程锁
# ---------------------------------------------------------------------------

def test_modify_personas_yaml_holds_file_lock(tmp_path, monkeypatch):
    """写入期间锁文件存在（说明持有跨进程锁），写入后释放。"""
    import utils.faiss_lock as fl

    s = _new_settings(tmp_path, monkeypatch, personas_yaml="personas:\n  user: hi\n")
    data_path = str(s._personas_yaml_path)
    lock_path = data_path + ".lock"
    seen = {}

    def mut(data):
        seen["lock_exists"] = os.path.exists(lock_path)
        data.setdefault("personas", {})["user"] = "changed"
        return "done"

    assert s._modify_personas_yaml(mut) == "done"
    assert seen.get("lock_exists") is True

    # 释放后 fd 关闭、深度归零（可再次获取）
    pl = fl._get_path_lock(data_path)
    assert pl._depth == 0
    assert pl._fd is None

    # 数据确实落盘
    import yaml
    assert yaml.safe_load(Path(data_path).read_text(encoding="utf-8"))["personas"]["user"] == "changed"


def test_file_lock_reentrant_inside_modify(tmp_path, monkeypatch):
    """mutator 内部再次获取同一路径的锁应可重入，不死锁。"""
    from utils.faiss_lock import faiss_file_lock

    s = _new_settings(tmp_path, monkeypatch, personas_yaml="personas: {}\n")
    flag = {}

    def mut(data):
        with faiss_file_lock(str(s._personas_yaml_path)):  # 重入，不阻塞
            flag["reentered"] = True
        data["touched"] = True
        return None

    s._modify_personas_yaml(mut)
    assert flag.get("reentered") is True


def test_modify_personas_yaml_falls_back_when_lock_unavailable(tmp_path, monkeypatch):
    """faiss_lock 导入失败 → 降级为仅线程锁，写入仍成功且不抛异常。"""
    s = _new_settings(tmp_path, monkeypatch, personas_yaml="personas:\n  user: hi\n")
    lock_path = str(s._personas_yaml_path) + ".lock"

    monkeypatch.setitem(sys.modules, "utils.faiss_lock", None)  # import 时抛 ImportError

    def mut(data):
        data.setdefault("personas", {})["user"] = "fallback"
        return True

    assert s._modify_personas_yaml(mut) is True
    assert s.get_persona("user") == "fallback"
    assert not os.path.exists(lock_path)  # 降级路径不产生锁文件


# ---------------------------------------------------------------------------
# settings.json：跨进程锁
# ---------------------------------------------------------------------------

def test_save_user_config_holds_file_lock(tmp_path, monkeypatch):
    import utils.faiss_lock as fl

    s = _new_settings(tmp_path, monkeypatch)
    path = s._USER_CONFIG_PATH
    lock_path = str(path) + ".lock"
    real = fl.faiss_file_lock
    seen = {}

    @contextlib.contextmanager
    def spy(p):
        with real(p):
            seen["lock_exists"] = os.path.exists(lock_path)
            yield

    monkeypatch.setattr(fl, "faiss_file_lock", spy)

    assert s.save_user_config(keys=[]) is None
    assert seen.get("lock_exists") is True
    assert fl._get_path_lock(str(path))._fd is None
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)  # 原子写产物可解析


def test_save_user_config_falls_back_when_lock_unavailable(tmp_path, monkeypatch):
    s = _new_settings(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "utils.faiss_lock", None)

    assert s.save_user_config(keys=[]) is None
    assert s._USER_CONFIG_PATH.exists()
    assert not os.path.exists(str(s._USER_CONFIG_PATH) + ".lock")
