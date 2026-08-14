"""causal_graph 补测：迁移 / 环路访问去重 / 语义降级 / 共现边界 / 合并跳过等防御分支"""
import sqlite3

import pytest

from modules.memory.causal_graph import (
    CausalGraph,
    CausalNode,
    CausalEdge,
)
from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def graph(tmp_path):
    return CausalGraph(db_path=str(tmp_path / "cg.db"))


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "ev.db"),
        faiss_index_path=str(tmp_path / "ev.faiss"),
        id_map_path=str(tmp_path / "ev.json"),
    )


def _triangle(graph):
    n = {l: CausalNode(label=l) for l in ["A", "B", "C", "D"]}
    for node in n.values():
        graph.save_node(node)
    graph.save_edge(CausalEdge(from_id=n["A"].id, to_id=n["B"].id, confidence=0.9))
    graph.save_edge(CausalEdge(from_id=n["A"].id, to_id=n["C"].id, confidence=0.5))
    graph.save_edge(CausalEdge(from_id=n["B"].id, to_id=n["D"].id, confidence=0.8))
    return n


# ── get_instance 双检锁 ────────────────────────────────────────────────

def test_get_instance_inner_check(monkeypatch):
    monkeypatch.setattr(CausalGraph, "_instance", None)
    fake = CausalGraph(db_path=":memory:")

    class RacingLock:
        def __enter__(self):
            CausalGraph._instance = fake  # 进入锁后另一线程已初始化
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(CausalGraph, "_lock", RacingLock())
    assert CausalGraph.get_instance() is fake


# ── DB 迁移（旧 schema 补列）───────────────────────────────────────────

def test_migrate_old_schema(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, node_type TEXT DEFAULT 'cause',
            description TEXT DEFAULT '', keywords TEXT DEFAULT '[]',
            importance REAL DEFAULT 0.5, confidence REAL DEFAULT 0.5,
            event_count INTEGER DEFAULT 0
        );
        CREATE TABLE edges (
            id TEXT PRIMARY KEY, from_id TEXT NOT NULL REFERENCES nodes(id),
            to_id TEXT NOT NULL REFERENCES nodes(id), relation TEXT DEFAULT 'causes',
            confidence REAL DEFAULT 0.5, label TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()
    g = CausalGraph(db_path=str(db))
    g._get_conn()
    ecols = {r[1] for r in g._get_conn().execute("PRAGMA table_info(edges)")}
    assert {"edge_type", "version"} <= ecols
    ncols = {r[1] for r in g._get_conn().execute("PRAGMA table_info(nodes)")}
    assert {"version", "updated_at"} <= ncols


# ── 指标 ───────────────────────────────────────────────────────────────

def test_update_metrics_error(graph, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(graph, "_get_conn", boom)
    graph._update_metrics()  # 静默降级


def test_record_query_time_overflow(graph):
    for _ in range(101):
        graph.record_query_time(0.01)
    m = graph.get_metrics()
    assert m["causal_graph_query_count"] == 101
    assert m["causal_graph_avg_query_time_seconds"] == pytest.approx(0.01)  # 浮点累积误差


# ── 环路检测：visited 去重 continue ───────────────────────────────────

def test_has_cycle_visited_continue(graph):
    n = {l: CausalNode(label=l) for l in ["A", "B", "C", "D"]}
    for node in n.values():
        graph.save_node(node)
    graph.save_edge(CausalEdge(from_id=n["B"].id, to_id=n["C"].id))
    graph.save_edge(CausalEdge(from_id=n["B"].id, to_id=n["D"].id))
    graph.save_edge(CausalEdge(from_id=n["C"].id, to_id=n["D"].id))
    assert graph._has_cycle(n["A"].id, n["B"].id) is False  # D 经两条路径到达 → visited continue


# ── find_anchor_nodes：拆分/语义降级 ──────────────────────────────────

def test_find_anchor_english_keyword(graph):
    graph.save_node(CausalNode(label="性能优化"))
    anchors = graph.find_anchor_nodes("performance issue", top_k=5)  # 无中文 → 跳过 bigram
    assert isinstance(anchors, list)


def test_find_anchor_bigram_boundary(graph):
    graph.save_node(CausalNode(label="性能"))
    anchors = graph.find_anchor_nodes("性能x", top_k=5)  # 边界 bigram 非中文 → 跳过
    assert anchors


def test_find_anchor_semantic_none_vec(graph, monkeypatch):
    class FakeEmbedder:
        def embed(self, text):
            return [1.0, 0.0]

        def embed_batch(self, texts):
            return [None] * len(texts)  # 部分向量缺失 → continue

    import modules.memory.embedding as emb_mod
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: FakeEmbedder()))
    graph.save_node(CausalNode(label="延期"))
    anchors = graph.find_anchor_nodes("延期")
    assert anchors


def test_find_anchor_semantic_error(graph, monkeypatch):
    class BoomEmbedder:
        def embed(self, text):
            raise RuntimeError("embed down")

        def embed_batch(self, texts):
            raise RuntimeError("embed down")

    import modules.memory.embedding as emb_mod
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: BoomEmbedder()))
    graph.save_node(CausalNode(label="延期"))
    anchors = graph.find_anchor_nodes("延期")  # 语义失败 → 关键词兜底
    assert anchors


# ── 共现统计边界 ───────────────────────────────────────────────────────

def test_update_cooccurrence_below_min(graph, store):
    n1, n2 = CausalNode(label="A"), CausalNode(label="B")
    n3, n4 = CausalNode(label="C"), CausalNode(label="D")
    for n in (n1, n2, n3, n4):
        graph.save_node(n)
    store.save_event(MemoryEvent(fact="e1", causal_node_ids=[n1.id, n2.id]))
    store.save_event(MemoryEvent(fact="e2", causal_node_ids=[n3.id, n4.id]))
    result = graph.update_cooccurrence(
        event_ids=[e.id for e in store.list_events()], min_cooccur=2, store=store,
    )
    assert result == 0  # 共现次数 < min → continue


def test_update_cooccurrence_boost_without_upgrade(graph, store):
    n1, n2 = CausalNode(label="A"), CausalNode(label="B")
    graph.save_node(n1)
    graph.save_node(n2)
    graph.save_edge(CausalEdge(from_id=n1.id, to_id=n2.id, relation="causes",
                               edge_type="causal", confidence=0.5))
    for _ in range(2):
        store.save_event(MemoryEvent(fact="f", causal_node_ids=[n1.id, n2.id]))
    result = graph.update_cooccurrence(
        event_ids=[e.id for e in store.list_events()], min_cooccur=2, store=store,
    )
    assert result >= 1
    edge = graph.list_all_edges()[0]
    assert edge.confidence > 0.5  # 仅 boost，不升级


# ── get_related_events 默认 store ─────────────────────────────────────

def test_get_related_events_default_store(graph, store, monkeypatch):
    monkeypatch.setattr(EventStore, "get_instance", staticmethod(lambda: store))
    n1 = CausalNode(label="节点")
    graph.save_node(n1)
    store.save_event(MemoryEvent(fact="相关事件", causal_node_ids=[n1.id]))
    related = graph.get_related_events(n1.id)
    assert [e.fact for e in related] == ["相关事件"]


# ── list_all_edges 时间窗口 ───────────────────────────────────────────

def test_list_all_edges_time_window(graph):
    n = _triangle(graph)
    a_id = n["A"].id
    edges = graph.list_all_edges(node_ids=[a_id], time_window="30d")
    assert len(edges) == 2
    hours = graph.list_all_edges(node_ids=[a_id], time_window="24h")
    assert len(hours) == 2
    minutes = graph.list_all_edges(node_ids=[a_id], time_window="15m")
    assert len(minutes) == 2


# ── 节点合并：跳过已合并节点 ─────────────────────────────────────────

def test_merge_skip_already_merged(graph):
    graph.save_node(CausalNode(label="abc", importance=1.0))
    graph.save_node(CausalNode(label="zzz", importance=0.5))
    graph.save_node(CausalNode(label="ab", importance=0.1))
    assert graph.merge_similar_nodes() == 1  # "ab" 被合并后，i=1 再次遇到 → continue
