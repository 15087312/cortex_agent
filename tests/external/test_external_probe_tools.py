"""probe_tools deep_recall 真实测试（external：真实深度回忆 + 记忆库）

深度回忆走真实因果图/事件库（确定性嵌入注入，不加载真实模型）。
需显式 `pytest -m external`。不硬编码 API key。
"""
import pytest

from modules.thinking.probes import probe_tools

pytestmark = pytest.mark.external


@pytest.fixture(autouse=True)
def deterministic_embedder(monkeypatch):
    """注入确定性嵌入，避免加载真实 SentenceTransformer"""
    from modules.memory.embedding import EmbeddingEngine
    eng = EmbeddingEngine()
    eng._loaded = True
    eng._attempted = True
    eng.dim = 16

    def _embed(text):
        import hashlib
        h = hashlib.md5(text.encode()).digest()
        return [((b - 128) / 128.0) for b in h][:16]

    eng.embed = _embed
    eng.embed_batch = lambda texts: [_embed(t) for t in texts]
    monkeypatch.setattr(EmbeddingEngine, "get_instance", classmethod(lambda cls: eng))
    return eng


def test_deep_recall_real_empty():
    """真实空记忆库：深度回忆未找到因果关联"""
    out = probe_tools.deep_recall("完全不存在的话题", depth_level=1, max_events=5)
    assert out["success"] is False
    assert "未找到" in out.get("error", "")


def test_deep_recall_real_with_causal_node(tmp_path, monkeypatch):
    """真实因果节点：深度回忆返回链路"""
    from modules.memory.causal_graph import CausalGraph, CausalNode
    graph = CausalGraph.get_instance()
    graph.clear_all()
    graph.save_node(CausalNode(label="性能问题", keywords=["性能"]))
    try:
        out = probe_tools.deep_recall("性能问题", depth_level=1, max_events=5)
        assert out["success"] is True
        assert out["causal_chains"] >= 1
    finally:
        graph.clear_all()
