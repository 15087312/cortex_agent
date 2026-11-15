"""桌宠 HTTP API — 路由 / SSE 帧 / 配置与人设 / 语音通道

pet/api.py 325 行里此前 224 行没跑过。这些端点是 Qt 桌宠窗口与网页唯一的
入口，返回结构（success/data/error）一旦漂移，前端就是白屏或静默失败。
这里用真 FastAPI + TestClient 打真实路由，存储用 tmp 库，引擎/记忆库用替身。
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config.settings import settings
from modules.desktop_pet import api as pet_api
from modules.desktop_pet.pet_engine import PetEngine
from modules.desktop_pet.store import PetStore


class FakeEngine:
    def __init__(self, tokens=None, error=None):
        self.enabled = True
        self.last_reply = "喵"
        self.last_fallback_reason = ""
        self.tokens = tokens if tokens is not None else ["你", "好"]
        self.error = error
        self.refresh_calls = 0
        self.stream_calls = []

    def voice_status(self):
        return {"running": True, "state": "idle", "last_error": "", "pending": 0, "cooldown": 0}

    def refresh_voice(self):
        self.refresh_calls += 1

    def stream_chat(self, text, extra_system="", on_reasoning=None):
        self.stream_calls.append({"text": text, "extra_system": extra_system})

        async def gen():
            if on_reasoning:
                on_reasoning("想一想")
            if self.error:
                raise RuntimeError(self.error)
            for t in self.tokens:
                yield t

        return gen()


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = PetStore(str(tmp_path / "pet.db"))
    monkeypatch.setattr(PetStore, "_instance", s)
    return s


@pytest.fixture
def engine(monkeypatch):
    e = FakeEngine()
    monkeypatch.setattr(PetEngine, "_instance", e, raising=False)
    return e


@pytest.fixture
def client(store, engine):
    app = FastAPI()
    app.include_router(pet_api.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_move_state(monkeypatch):
    monkeypatch.setattr(pet_api, "_pet_move", {"dx": 0.0, "dy": 0.0, "active": False})
    monkeypatch.setattr(pet_api, "_pet_move_queues", set())


# ── 基础查询 ──

def test_last_reply_returns_engine_value(client):
    r = client.get("/stream/pet/last-reply")
    assert r.json() == {"success": True, "data": "喵"}


def test_last_reply_degrades_when_engine_broken(monkeypatch, store):
    class Boom:
        @property
        def last_reply(self):
            raise RuntimeError("engine crashed")

    monkeypatch.setattr(PetEngine, "_instance", Boom(), raising=False)
    app = FastAPI()
    app.include_router(pet_api.router)
    with TestClient(app) as c:
        assert c.get("/stream/pet/last-reply").json() == {"success": True, "data": None}


def test_actions_come_from_action_catalog(client):
    data = client.get("/stream/pet/actions").json()
    assert data["success"] is True
    assert data["data"]["categories"]
    assert isinstance(data["data"]["actions"], list)


# ── 拖动位移 ──

def test_move_accumulates_and_resets(client):
    assert client.post("/stream/pet/move", json={"dx": 3, "dy": -2, "active": True}).json()["success"]
    q = asyncio.Queue()
    pet_api._pet_move_queues.add(q)
    client.post("/stream/pet/move", json={"dx": 1, "dy": 1})

    assert q.get_nowait() == {"dx": 1.0, "dy": 1.0, "active": True}
    # 位移累积到下一次推送，随后归零
    client.post("/stream/pet/move", json={"dx": 0, "dy": 0})
    assert pet_api._pet_move["dx"] == 0.0


def test_move_survives_broken_subscriber(client):
    bad = MagicMock()
    bad.put_nowait.side_effect = RuntimeError("client gone")
    pet_api._pet_move_queues.add(bad)
    assert client.post("/stream/pet/move", json={"dx": 5}).json()["success"] is True


def test_move_stream_yields_sse_event():
    async def run():
        resp = await pet_api.pet_move_stream()
        agen = resp.body_iterator
        q = next(iter(pet_api._pet_move_queues))
        assert q is not None
        q.put_nowait({"dx": 7, "dy": 8, "active": False})
        item = await asyncio.wait_for(agen.__anext__(), timeout=5)
        await agen.aclose()
        return item

    item = asyncio.run(run())
    body = item["data"]
    assert json.loads(body) == {"dx": 7, "dy": 8, "active": False}
    assert pet_api._pet_move_queues == set()       # 断连后清理订阅


# ── 对话流 ──

def _frames(resp):
    out = []
    for line in resp.iter_lines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


def test_chat_returns_409_when_disabled(client, engine):
    engine.enabled = False
    r = client.post("/stream/pet/chat", json={"text": "你好"})
    assert r.status_code == 409
    assert "desktop pet disabled" in r.json()["detail"]


def test_chat_streams_tokens_and_done(client):
    with client.stream("POST", "/stream/pet/chat", json={"text": "你好"}) as r:
        body = "".join(r.iter_text())
    assert "event: token" in body
    assert '"token": "你"' in body
    assert "event: done" in body


def test_chat_action_id_maps_to_prompt(client, engine):
    from modules.desktop_pet.actions import public_actions

    target = public_actions()[0]
    with client.stream("POST", "/stream/pet/chat", json={"action_id": target["id"]}) as r:
        "".join(r.iter_text())
    assert engine.stream_calls[-1]["text"] == target["prompt"]


def test_chat_empty_text_triggers_proactive_system_prompt(client, engine):
    with client.stream("POST", "/stream/pet/chat", json={}) as r:
        "".join(r.iter_text())
    assert "主动思考" in engine.stream_calls[-1]["extra_system"]


def test_chat_round_complete_and_fallback_flag(client, engine):
    engine.tokens = ["好", "\x00ROUND_DONE\x00"]
    engine.last_fallback_reason = "api down"
    with client.stream("POST", "/stream/pet/chat", json={"text": "hi"}) as r:
        frames = _frames(r)
    assert any(f.get("round_complete") for f in frames)
    assert frames[-1]["fallback"] is True
    assert frames[-1]["audio_url"] == ""


def test_chat_reports_engine_error(client, engine):
    engine.error = "llm quota"
    with client.stream("POST", "/stream/pet/chat", json={"text": "hi"}) as r:
        frames = _frames(r)
    assert any("llm quota" in f.get("error", "") for f in frames)


def test_chat_synthesizes_voice_when_enabled(client, store, engine, monkeypatch, tmp_path):
    store.update_config({"voice_enabled": True})
    audio = tmp_path / "reply.mp3"
    audio.write_bytes(b"ID3")

    fake_tts = MagicMock()
    fake_tts.synthesize_sync.return_value = str(audio)
    monkeypatch.setattr("modules.output_system.tts.TTSEngine", lambda: fake_tts)

    engine.tokens = ["<think>想</think>", "你好 *世界*"]
    with client.stream("POST", "/stream/pet/chat", json={"text": "hi"}) as r:
        frames = _frames(r)
    assert frames[-1]["audio_url"] == "/audio/reply.mp3"
    spoken = fake_tts.synthesize_sync.call_args.args[0]
    assert "think" not in spoken and "*" not in spoken
    assert "你好" in spoken and "世界" in spoken


def test_chat_tts_failure_keeps_text_reply(client, store, engine, monkeypatch):
    store.update_config({"voice_enabled": True})

    def boom():
        raise RuntimeError("tts down")

    monkeypatch.setattr("modules.output_system.tts.TTSEngine", boom)
    with client.stream("POST", "/stream/pet/chat", json={"text": "hi"}) as r:
        frames = _frames(r)
    assert frames[-1]["audio_url"] == ""
    assert "".join(str(f) for f in frames)


# ── 配置 ──

def test_get_config_masks_api_key(client, store):
    store.update_config({"api_key": "sk-secret"})
    data = client.get("/stream/pet/config").json()["data"]
    assert data["api_key_set"] is True
    assert "api_key" not in data


def test_put_config_updates_only_given_fields(client, store):
    before = store.get_config()
    data = client.put("/stream/pet/config", json={"loop_type": "custom", "max_history": 3}).json()
    assert data["success"] is True
    assert data["data"]["loop_type"] == "custom"
    assert data["data"]["max_history"] == 3
    assert data["data"]["persona"] == before["persona"]


def test_put_config_with_unknown_persona_fails(client):
    r = client.put("/stream/pet/config", json={"active_persona_id": 9999}).json()
    assert r == {"success": False, "error": "persona not found"}


def test_put_config_switches_persona(client, store):
    pid = store.add_persona("猫娘", "你是猫娘")["id"]
    assert client.put("/stream/pet/config", json={"active_persona_id": pid}).json()["success"]
    assert store.get_active_persona_id() == pid


def test_put_config_refreshes_voice_only_for_existing_engine(client, engine):
    client.put("/stream/pet/config", json={"realtime_voice": True})
    assert engine.refresh_calls == 1


def test_put_config_without_engine_instance_does_not_create_one(client, monkeypatch, store):
    monkeypatch.setattr(PetEngine, "_instance", None, raising=False)
    r = client.put("/stream/pet/config", json={"realtime_voice": True}).json()
    assert r["success"] is True
    assert PetEngine._instance is None


def test_put_config_voice_refresh_error_is_swallowed(client, engine):
    engine.refresh_voice = MagicMock(side_effect=RuntimeError("mic busy"))
    assert client.put("/stream/pet/config", json={"realtime_voice": False}).json()["success"]


# ── 人设 ──

def test_persona_crud_flow(client, store, monkeypatch):
    created = client.post("/stream/pet/personas", json={"name": "猫娘", "prompt": "你是猫娘"}).json()
    assert created["success"] is True
    pid = created["data"]["id"]

    dup = client.post("/stream/pet/personas", json={"name": "猫娘", "prompt": "x"}).json()
    assert dup == {"success": False, "error": "duplicate name"}

    assert client.put("/stream/pet/personas/{}".format(pid), json={"name": "喵"}).json()["success"]
    missing = client.put("/stream/pet/personas/9999", json={"name": "x"}).json()
    assert missing["success"] is False

    listing = client.get("/stream/pet/personas").json()["data"]
    assert listing["active_id"] == store.get_active_persona_id()
    assert any(p["id"] == pid for p in listing["items"])

    assert client.post("/stream/pet/personas/{}/activate".format(pid)).json()["success"]
    assert store.get_active_persona_id() == pid
    assert client.post("/stream/pet/personas/9999/activate").json()["success"] is False


def test_persona_delete_cleans_memory_and_protects_last(client, store, monkeypatch):
    bank = MagicMock()
    monkeypatch.setattr("modules.desktop_pet.memory_bank.PetMemoryBank.get_instance", classmethod(lambda cls: bank))

    pid = store.add_persona("猫娘", "你是猫娘")["id"]
    assert client.delete("/stream/pet/personas/{}".format(pid)).json()["success"] is True
    bank.delete_persona.assert_called_once_with(pid)

    last = store.get_active_persona_id()
    r = client.delete("/stream/pet/personas/{}".format(last)).json()
    assert r == {"success": False, "error": "cannot delete last persona or not found"}


def test_persona_delete_survives_memory_cleanup_error(client, store, monkeypatch):
    monkeypatch.setattr(
        "modules.desktop_pet.memory_bank.PetMemoryBank.get_instance",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("file busy"))),
    )
    pid = store.add_persona("猫娘", "你是猫娘")["id"]
    assert client.delete("/stream/pet/personas/{}".format(pid)).json()["success"] is True


# ── 自动记忆（MemoryBank） ──

def test_memory_events_maps_fields_and_clamps_limit(client, store, monkeypatch):
    ev = MagicMock()
    ev.id, ev.fact, ev.thought, ev.lesson = 1, "喜欢猫", "想法", "教训"
    ev.keywords, ev.importance, ev.type, ev.time = ["猫"], 0.9, "fact", 123
    ev.causal_node_ids = [7]
    bank = MagicMock()
    bank.store.return_value.list_events.return_value = [ev]
    monkeypatch.setattr("modules.desktop_pet.memory_bank.PetMemoryBank.get_instance", classmethod(lambda cls: bank))

    data = client.get("/stream/pet/memory/events?limit=9999").json()["data"]
    bank.store.return_value.list_events.assert_called_once_with(limit=200)
    assert data["items"][0]["fact"] == "喜欢猫"
    assert data["items"][0]["causal_node_ids"] == [7]
    assert data["persona_id"] == store.get_active_persona_id()


def test_memory_events_degrades_to_empty(client, store, monkeypatch):
    monkeypatch.setattr(
        "modules.desktop_pet.memory_bank.PetMemoryBank.get_instance",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    assert client.get("/stream/pet/memory/events").json()["data"]["items"] == []


def test_memory_stats_merges_persona_id(client, store, monkeypatch):
    bank = MagicMock()
    bank.stats.return_value = {"events": 3, "nodes": 2, "edges": 1}
    monkeypatch.setattr("modules.desktop_pet.memory_bank.PetMemoryBank.get_instance", classmethod(lambda cls: bank))
    data = client.get("/stream/pet/memory/stats").json()["data"]
    assert data["events"] == 3
    assert data["persona_id"] == store.get_active_persona_id()


# ── 历史 ──

def test_history_list_delete_clear(client, store):
    store.add_history("user", "你好")
    mid = store.get_history()[0]["id"]
    assert client.get("/stream/pet/history").json()["data"]["items"][0]["content"] == "你好"

    assert client.delete("/stream/pet/history/{}".format(mid)).json()["success"] is True
    assert client.delete("/stream/pet/history/999999").json() == {"success": False, "error": "not found"}

    store.add_history("user", "再来一条")
    assert client.delete("/stream/pet/history").json()["success"] is True
    assert store.get_history() == []


# ── 独立记忆 ──

def test_manual_memory_add_list_delete(client, store):
    assert client.post("/stream/pet/memory", json={"text": "  "}).json() == {
        "success": False, "error": "empty text",
    }
    created = client.post("/stream/pet/memory", json={"kind": "fact", "text": "喜欢猫"}).json()
    assert created["success"] is True
    mid = created["data"]["id"]

    items = client.get("/stream/pet/memory").json()["data"]["items"]
    assert items[0]["text"] == "喜欢猫"
    assert client.delete("/stream/pet/memory/{}".format(mid)).json()["success"] is True
    assert client.delete("/stream/pet/memory/999999").json() == {"success": False, "error": "not found"}


# ── 窗口进程 ──

def test_window_status_start_stop(client, monkeypatch):
    sup = MagicMock()
    sup.status.return_value = {"running": False, "pid": None}
    sup.start.return_value = {"ok": True, "pid": 123}
    sup.stop.return_value = {"ok": True}
    monkeypatch.setattr("modules.desktop_pet.window_supervisor.get_pet_window_supervisor", lambda: sup)

    assert client.get("/stream/pet/window").json()["data"]["running"] is False
    assert client.post("/stream/pet/window/start").json()["data"]["pid"] == 123
    assert client.post("/stream/pet/window/stop").json()["success"] is True


def test_window_start_failure_reports_reason(client, monkeypatch):
    sup = MagicMock()
    sup.start.return_value = {"ok": False, "reason": "no display"}
    monkeypatch.setattr("modules.desktop_pet.window_supervisor.get_pet_window_supervisor", lambda: sup)
    r = client.post("/stream/pet/window/start").json()
    assert r == {"success": False, "error": "no display"}


# ── 语音状态 / WebSocket ──

def test_voice_status_without_engine_instance(client, monkeypatch):
    monkeypatch.setattr(PetEngine, "_instance", None, raising=False)
    data = client.get("/stream/pet/voice/status").json()["data"]
    assert data["running"] is False
    assert PetEngine._instance is None            # 高频轮询不得创建单例


def test_voice_status_from_engine(client):
    data = client.get("/stream/pet/voice/status").json()["data"]
    assert data["running"] is True
    assert "tts_backend" in data


def test_voice_status_engine_exception(client, monkeypatch):
    class Broken:
        def voice_status(self):
            raise RuntimeError("mic exploded")

    monkeypatch.setattr(PetEngine, "_instance", Broken(), raising=False)
    data = client.get("/stream/pet/voice/status").json()["data"]
    assert data["running"] is False
    assert "mic exploded" in data["last_error"]


def test_voice_websocket_rejected_when_disabled(client, store, monkeypatch):
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_REALTIME", False)
    store.update_config({"realtime_voice": False})
    with pytest.raises(Exception):
        with client.websocket_connect("/stream/pet/voice") as ws:
            ws.receive_text()


def test_voice_websocket_hands_off_to_handler(client, store, monkeypatch):
    store.update_config({"realtime_voice": True})
    seen = {}

    class FakeHandler:
        def __init__(self, ws):
            seen["ws"] = ws

        async def handle(self):
            await seen["ws"].accept()
            await seen["ws"].send_text("hello")

    monkeypatch.setattr(
        "modules.desktop_pet.voice.ws_handler.VoiceWebSocketHandler", FakeHandler
    )
    with client.websocket_connect("/stream/pet/voice") as ws:
        assert ws.receive_text() == "hello"
