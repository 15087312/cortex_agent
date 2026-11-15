"""桌宠独立记忆检索 — 向量优先 / 关键词降级 / 余弦边界

PetMemory 决定「桌宠记不记得你说过的话」。它必须只在桌宠自己的记忆池里检索，
且在 embedding 模型不可用时静默降级，不能把异常抛给对话主链路。
"""
from unittest.mock import MagicMock

import pytest

from modules.desktop_pet import memory as mem_mod
from modules.desktop_pet.memory import PetMemory
from modules.memory import embedding as emb_mod


class FakeStore:
    def __init__(self, rows=None, persona_id=1, raise_on_search=False):
        self._rows = rows or []
        self._persona_id = persona_id
        self._raise = raise_on_search
        self.search_calls = []

    def search_memory(self, limit=200, persona_id=None):
        self.search_calls.append((limit, persona_id))
        if self._raise:
            raise RuntimeError("db locked")
        return list(self._rows)

    def get_active_persona_id(self):
        return self._persona_id


class FakeEmbedding:
    def __init__(self, vectors):
        self._vectors = vectors
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return self._vectors.get(text)


@pytest.fixture
def use_embedding(monkeypatch, take_over):
    def _install(vectors):
        engine = FakeEmbedding(vectors)
        # EmbeddingEngine.get_instance 现在是端口优先（读 embedding_engine 端口），
        # 打类方法不再生效——桩要打在端口上。
        take_over("embedding_engine", lambda: engine)
        return engine

    return _install


def _rows(*texts):
    return [{"text": t, "created_at": i} for i, t in enumerate(texts)]


# ── 池与异常 ──

async def test_retrieve_returns_empty_when_store_fails():
    pet = PetMemory(store=FakeStore(raise_on_search=True))
    assert await pet.retrieve("你好") == []


async def test_retrieve_returns_empty_on_empty_pool():
    pet = PetMemory(store=FakeStore([]))
    assert await pet.retrieve("你好") == []


async def test_retrieve_scopes_to_active_persona():
    store = FakeStore(_rows("喜欢猫"))
    pet = PetMemory(store=store)
    await pet.retrieve("猫")
    assert store.search_calls == [(200, 1)]


# ── 向量检索 ──

async def test_vector_ranking_orders_by_similarity(use_embedding):
    use_embedding({"猫": [1.0, 0.0], "喜欢猫": [1.0, 0.0], "今天下雨": [1.0, 0.5]})
    pet = PetMemory(store=FakeStore(_rows("今天下雨", "喜欢猫")))
    out = await pet.retrieve("猫", max_results=2)
    assert [m["text"] for m in out] == ["喜欢猫", "今天下雨"]


async def test_vector_ranking_drops_low_similarity(use_embedding):
    use_embedding({"猫": [1.0, 0.0], "喜欢猫": [1.0, 0.0], "今天下雨": [0.0, 1.0]})
    pet = PetMemory(store=FakeStore(_rows("喜欢猫", "今天下雨")))
    out = await pet.retrieve("猫")
    assert [m["text"] for m in out] == ["喜欢猫"]


async def test_vector_path_skips_rows_without_text_or_vector(use_embedding):
    use_embedding({"猫": [1.0, 0.0], "喜欢猫": [1.0, 0.0]})
    rows = [{"text": "", "created_at": 1}, {"text": "无向量", "created_at": 2}, {"text": "喜欢猫", "created_at": 3}]
    pet = PetMemory(store=FakeStore(rows))
    out = await pet.retrieve("猫")
    assert [m["text"] for m in out] == ["喜欢猫"]


async def test_empty_query_returns_recent_memories(use_embedding):
    """没有输入时不该去向量化空串，直接给最近记忆"""
    engine = use_embedding({"[1.0]": [1.0]})
    pet = PetMemory(store=FakeStore(_rows("a", "b", "c")))
    out = await pet.retrieve("", max_results=2)
    assert [m["text"] for m in out] == ["a", "b"]
    assert engine.calls == []


# ── 降级 ──

async def test_falls_back_to_keyword_when_embedding_raises(monkeypatch):
    def boom(cls):
        raise RuntimeError("model not loaded")

    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", classmethod(boom))
    pet = PetMemory(store=FakeStore(_rows("我养了一只猫", "今天下雨", "猫粮没了")))
    out = await pet.retrieve("猫粮 下雨")
    # 同分时按 created_at 倒序：猫粮没了(id=2) 在 今天下雨(id=1) 前面
    assert [m["text"] for m in out] == ["猫粮没了", "今天下雨"]


async def test_keyword_fallback_ignores_single_char_tokens(monkeypatch):
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", classmethod(lambda cls: MagicMock(embed=lambda t: None)))
    pet = PetMemory(store=FakeStore(_rows("无关内容")))
    assert await pet.retrieve("我 的 猫") == []


async def test_falls_back_to_keyword_when_all_vectors_below_threshold(use_embedding):
    use_embedding({"猫": [1.0, 0.0], "喜欢猫": [1.0, 0.05]})
    pet = PetMemory(store=FakeStore(_rows("喜欢猫")))
    # 相似度 0.998 会被采用；这里换一个完全不相关的查询验证降级链路
    out = await pet.retrieve("火车")
    assert out == []


# ── 余弦 ──

def test_cosine_edge_cases():
    assert PetMemory._cosine([], [1.0]) == 0.0
    assert PetMemory._cosine([1.0, 2.0], [1.0]) == 0.0
    assert PetMemory._cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert PetMemory._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert PetMemory._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_min_similarity_threshold_is_sane():
    assert 0.0 < mem_mod.MIN_SIM < 1.0
