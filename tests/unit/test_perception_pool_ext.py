"""PerceptionPool — 事件池分支覆盖扩展

入池(空描述/去重/截断/容量/TTL清除)、快照(空/分组/低强度diff/TTL过滤/未匹配类型)、clear。
"""
from unittest.mock import patch

import modules.perception.pool as pool_mod
from modules.perception.pool import PerceptionPool


def test_add_empty_description_skips():
    pool = PerceptionPool()
    pool.add("screen.ocr", "ocr", "")
    assert pool._items == []


def test_add_dedup():
    pool = PerceptionPool()
    pool.add("screen.ocr", "ocr", "hello world")
    pool.add("screen.ocr", "ocr", "hello world")
    assert len(pool._items) == 1


def test_add_truncates_description():
    pool = PerceptionPool()
    pool.add("x", "s", "a" * 500)
    assert len(pool._items[0]["description"]) == 300


def test_add_limits_items():
    pool = PerceptionPool(max_items=5)
    for i in range(10):
        pool.add("x", "s", f"desc {i}")
    assert len(pool._items) == 5
    assert pool._items[0]["description"] == "desc 5"
    assert pool._items[-1]["description"] == "desc 9"


def test_add_hash_burst_clears():
    pool = PerceptionPool()
    pool._hashes = {f"h{i}" for i in range(200)}
    pool.add("x", "s", "new item")
    assert pool._hashes == set()


def test_add_ttl_physical_purge(monkeypatch):
    pool = PerceptionPool(ttl_seconds=10)
    now = {"v": 1000.0}
    monkeypatch.setattr(pool_mod.time, "time", lambda: now["v"])
    pool.add("x", "s", "old item")
    now["v"] = 1020.0
    pool.add("y", "s", "new item")
    assert len(pool._items) == 1
    assert pool._items[0]["description"] == "new item"


def test_add_none_payload_defaults():
    pool = PerceptionPool()
    pool.add("x", "s", "with none payload", payload=None)
    assert pool._items[0]["payload"] == {}


def test_snapshot_empty():
    pool = PerceptionPool()
    frag = pool.snapshot()
    assert "无感知数据" in frag.content
    assert frag.target_roles == ("orchestrator",)


def test_snapshot_groups_by_type(monkeypatch):
    pool = PerceptionPool(ttl_seconds=100)
    monkeypatch.setattr(pool_mod.time, "time", lambda: 1000.0)
    pool.add("screen.window", "window", "窗口: 浏览器")
    pool.add("screen.ocr", "ocr", "文字内容")
    pool.add("file.change", "file", "文件变化")
    pool.add("screen.diff", "diff", "屏幕变化", payload={"intensity": 0.8})
    pool.add("speech.detected", "voice", "语音指令")
    frag = pool.snapshot()
    assert "【窗口状态】" in frag.content
    assert "【屏幕文本】" in frag.content
    assert "【文件变化】" in frag.content
    assert "【屏幕变化】" in frag.content
    assert "【语音指令】" in frag.content
    assert frag.target_roles == ("orchestrator",)
    assert frag.ttl_turns == 1


def test_snapshot_low_intensity_diff_skips():
    pool = PerceptionPool()
    pool.add("screen.diff", "diff", "低强度变化", payload={"intensity": 0.1})
    frag = pool.snapshot()
    assert frag.content == ""
    assert frag.target_roles == ("large",)


def test_snapshot_unmatched_type():
    pool = PerceptionPool()
    pool.add("mcp.resource.update", "mcp", "资源更新")
    frag = pool.snapshot()
    assert frag.content == ""
    assert frag.target_roles == ("large",)


def test_snapshot_ttl_filters_stale(monkeypatch):
    pool = PerceptionPool(ttl_seconds=10)
    monkeypatch.setattr(pool_mod.time, "time", lambda: 1000.0)
    pool.add("screen.window", "window", "fresh")
    pool._items.append({
        "event_type": "speech.detected", "source": "v",
        "description": "stale", "payload": {}, "timestamp": 900.0,
    })
    frag = pool.snapshot()
    assert "fresh" in frag.content
    assert "stale" not in frag.content


def test_snapshot_max_items_limit(monkeypatch):
    pool = PerceptionPool(ttl_seconds=100)
    monkeypatch.setattr(pool_mod.time, "time", lambda: 1000.0)
    for i in range(10):
        pool.add("screen.window", "window", f"w{i}")
    frag = pool.snapshot(max_items=2)
    assert "w9" in frag.content
    assert "w0" not in frag.content


def test_clear():
    pool = PerceptionPool()
    pool.add("x", "s", "abc")
    pool.clear()
    assert pool._items == []
    assert pool._hashes == set()
