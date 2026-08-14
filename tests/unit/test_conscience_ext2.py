"""conscience 扩展测试：补齐 _get_causal_knowledge 全分支 / analyze_feedback 各路径 / think 分支"""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.memory.event_reducer as er_mod
import modules.memory.causal_tree as ct_mod
import modules.thinking.conscience as cons_mod
from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.event_reducer import EventReducer
from modules.thinking.conscience import Conscience, get_conscience


def _client(text="（内心独白）"):
    c = MagicMock()
    c.generate = AsyncMock(return_value=text)
    return c


# ── 假因果树：覆盖循环内所有分支 ────────────────────────────────────────────

class _ET:
    def __init__(self, label, confidence=0.8, parents=None, children=None, evidence=None):
        self.node = SimpleNamespace(label=label)
        self.confidence = confidence
        self.parent_chain = [SimpleNamespace(label=p) for p in (parents or [])]
        self.child_chains = [[SimpleNamespace(label=c) for c in ch] for ch in (children or [])]
        self.evidence = [SimpleNamespace(fact=f) for f in (evidence or [])]


def _make_tree(map_fn):
    class FakeTree:
        def __init__(self, graph):
            pass

        def expand_node(self, nid):
            return map_fn(nid)

    return FakeTree


def _patch_tree(monkeypatch, map_fn):
    monkeypatch.setattr(ct_mod, "CausalTree", _make_tree(map_fn))


# ── _get_causal_knowledge：事件节点循环全分支 ────────────────────────────────

def test_get_causal_knowledge_loop_full(monkeypatch):
    """ValueError 跳过 + 重复标签跳过 + 原因链/后果链/证据（103-120, 118->100）"""
    tree_map = {
        "n1": _ET("性能", parents=["原因A"], children=[["后果B", "后果C"]],
                  evidence=["事实1", "事实2"]),
        "n2": _ET("性能"),      # 标签重复 → 跳过
        "n3": _ET("内存"),      # 无证据 → 118->100（证据空分支）
    }

    def expand(nid):
        if nid == "bad":
            raise ValueError("no node")
        return tree_map[nid]

    _patch_tree(monkeypatch, expand)
    graph = MagicMock()
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))

    c = Conscience()
    c._get_node_ids_from_events = MagicMock(return_value=["bad", "n1", "n2", "n3"])
    out = c._get_causal_knowledge("性能问题")
    assert "性能" in out
    assert "原因A" in out
    assert "后果B → 后果C" in out
    assert "事实1" in out
    assert c._last_analyzed_node_ids == ["n1", "n3"]


def test_get_causal_knowledge_fallback_full(monkeypatch):
    """事件无关联 → 回退锚点分支全字段（124-137）"""
    tree_map = {
        "a1": _ET("延期", parents=["工期紧"], children=[["加班"]], evidence=["上个月延期了"]),
        "a2": _ET("延期"),
    }
    _patch_tree(monkeypatch, tree_map.get)
    graph = MagicMock()
    graph.find_anchor_nodes = MagicMock(return_value=[
        (SimpleNamespace(id="a1"), 0.9),
        (SimpleNamespace(id="a2"), 0.8),
    ])
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))

    c = Conscience()
    c._get_node_ids_from_events = MagicMock(return_value=[])
    out = c._get_causal_knowledge("项目延期")
    assert "延期" in out
    assert "工期紧" in out
    assert "加班" in out
    assert c._last_analyzed_node_ids == ["a1", "a2"]


def test_get_node_ids_events_empty(monkeypatch):
    from modules.memory.event_retrieval import EventRetrieval

    class FakeRetrieval:
        async def retrieve(self, query, **kw):
            return None

    monkeypatch.setattr(EventRetrieval, "get_instance", staticmethod(lambda: FakeRetrieval()))
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    c = Conscience()
    try:
        assert c._get_node_ids_from_events("q") == []  # 169-170
    finally:
        loop.close()


# ── _extract_keywords 分支 ───────────────────────────────────────────────────

def test_extract_keywords_short_chinese(monkeypatch):
    assert set(Conscience._extract_keywords("财务")) == {"财务"}  # 198->197


def test_extract_keywords_bigram_duplicate(monkeypatch):
    kws = set(Conscience._extract_keywords("问题问题"))
    assert {"问题问题", "问题", "题问"} <= kws  # 201->199 重复 bigram 跳过


# ── analyze_feedback：各路径 ─────────────────────────────────────────────────

def _mk_graph(tmp_path, nodes):
    g = CausalGraph(db_path=str(tmp_path / "cg.db"))
    for label, conf in nodes:
        g.save_node(CausalNode(label=label, confidence=conf))
    return g


async def test_analyze_feedback_confirmed_with_neighbors(monkeypatch, tmp_path):
    g = _mk_graph(tmp_path, [("A", 0.5), ("B", 0.5), ("C", 0.5)])
    ids = {n.label: n for n in g.list_nodes()}
    g.save_edge(CausalEdge(from_id=ids["B"].id, to_id=ids["A"].id))
    g.save_edge(CausalEdge(from_id=ids["A"].id, to_id=ids["C"].id))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))

    reducer = EventReducer(model_client=_client(
        '{"confirmed": ["%s"], "contradicted": []}' % ids["A"].id))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)

    c = Conscience()
    c._last_analyzed_node_ids = [ids["A"].id]
    await c.analyze_feedback("q", "r")
    assert g.get_node(ids["A"].id).confidence == pytest.approx(0.55)
    assert g.get_node(ids["B"].id).confidence == pytest.approx(0.52)
    assert g.get_node(ids["C"].id).confidence == pytest.approx(0.52)
    assert c._last_analyzed_node_ids == []


async def test_analyze_feedback_unknown_node_skipped(monkeypatch, tmp_path):
    """graph.get_node 返回 None 的节点跳过（226->224），且 known 集非空"""
    g = _mk_graph(tmp_path, [("A", 0.5)])
    ids = {n.label: n for n in g.list_nodes()}
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    reducer = EventReducer(model_client=_client('{"confirmed": [], "contradicted": []}'))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [ids["A"].id, "missing"]
    await c.analyze_feedback("q", "r")


async def test_analyze_feedback_no_known_nodes(monkeypatch, tmp_path):
    g = _mk_graph(tmp_path, [("A", 0.5)])
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    reducer = EventReducer(model_client=_client("x"))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = ["missing"]
    await c.analyze_feedback("q", "r")  # 229 return


async def test_analyze_feedback_generate_error(monkeypatch, tmp_path):
    g = _mk_graph(tmp_path, [("A", 0.5)])
    ids = {n.label: n for n in g.list_nodes()}
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    client = MagicMock()
    client.generate = AsyncMock(side_effect=RuntimeError("llm down"))
    reducer = EventReducer(model_client=client)
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [ids["A"].id]
    await c.analyze_feedback("q", "r")  # 252-253 return


async def test_analyze_feedback_code_fence(monkeypatch, tmp_path):
    g = _mk_graph(tmp_path, [("A", 0.5)])
    ids = {n.label: n for n in g.list_nodes()}
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    text = '```json\n{"confirmed": ["%s"], "contradicted": []}\n```' % ids["A"].id
    reducer = EventReducer(model_client=_client(text))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [ids["A"].id]
    await c.analyze_feedback("q", "r")  # 258-261 解析代码围栏
    assert g.get_node(ids["A"].id).confidence > 0.5


async def test_analyze_feedback_code_fence_single(monkeypatch, tmp_path):
    """代码围栏无收尾 ``` → 260->262（rsplit 不再执行）"""
    g = _mk_graph(tmp_path, [("A", 0.5)])
    ids = {n.label: n for n in g.list_nodes()}
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    text = 'prefix```json{"confirmed": ["%s"], "contradicted": []}' % ids["A"].id
    reducer = EventReducer(model_client=_client(text))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [ids["A"].id]
    await c.analyze_feedback("q", "r")
    assert g.get_node(ids["A"].id).confidence > 0.5


async def test_analyze_feedback_bad_json(monkeypatch, tmp_path):
    """有花括号但 JSON 无效 → JSONDecodeError 分支（269-270）"""
    g = _mk_graph(tmp_path, [("A", 0.5)])
    ids = {n.label: n for n in g.list_nodes()}
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    reducer = EventReducer(model_client=_client('{confirmed}'))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [ids["A"].id]
    await c.analyze_feedback("q", "r")
    assert c._last_analyzed_node_ids == []


async def test_analyze_feedback_confirmed_not_tracked(monkeypatch, tmp_path):
    """confirmed 节点存在于图但不在本轮分析集 → 不调整（278->276, 296->302）"""
    g = _mk_graph(tmp_path, [("A", 0.5), ("X", 0.6)])
    ids = {n.label: n for n in g.list_nodes()}
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    reducer = EventReducer(model_client=_client(
        '{"confirmed": ["%s"], "contradicted": []}' % ids["A"].id))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [ids["X"].id]  # A 不在本轮 → 不调整
    await c.analyze_feedback("q", "r")
    assert g.get_node(ids["A"].id).confidence == pytest.approx(0.5)


async def test_analyze_feedback_contradicted_not_tracked(monkeypatch, tmp_path):
    """contradicted 节点存在于图但不在本轮分析集 → 不调整（291->289）"""
    g = _mk_graph(tmp_path, [("A", 0.8), ("X", 0.6)])
    ids = {n.label: n for n in g.list_nodes()}
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    reducer = EventReducer(model_client=_client(
        '{"confirmed": [], "contradicted": ["%s"]}' % ids["A"].id))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [ids["X"].id]
    await c.analyze_feedback("q", "r")
    assert g.get_node(ids["A"].id).confidence == pytest.approx(0.8)


async def test_analyze_feedback_outer_error(monkeypatch, tmp_path):
    """内部抛异常 → 外层 except + finally 清理（299-302）"""
    g = _mk_graph(tmp_path, [("A", 0.5)])
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    reducer = EventReducer(model_client=_client("x"))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = ["n1"]
    monkeypatch.setattr(g, "get_node", MagicMock(side_effect=RuntimeError("boom")))
    await c.analyze_feedback("q", "r")
    assert c._last_analyzed_node_ids == []


# ── think：values.txt 各分支 ─────────────────────────────────────────────────

def _fake_open(content):
    class _F:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return content

        def __call__(self, *a, **k):
            return self

    return _F()


async def test_think_values_file_exists(monkeypatch, tmp_path):
    """values.txt 存在且有内容 → 直接读取，不落到默认值（323-324, 325->331）"""
    from modules.memory.causal_graph import CausalGraph
    g = CausalGraph(db_path=str(tmp_path / "cgv.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr("builtins.open", lambda *a, **k: _fake_open("我的价值观")(*a, **k))
    c = Conscience(model_client=_client("独白"))
    c._get_node_ids_from_events = MagicMock(return_value=[])
    await c.think("q")
    prompt = c._model_client.generate.call_args.args[0]
    assert "我的价值观" in prompt
    assert "诚实、负责" not in prompt


async def test_think_values_file_read_error(monkeypatch, tmp_path):
    """values.txt 读取抛异常 → 默认值（327-328）"""
    from modules.memory.causal_graph import CausalGraph
    g = CausalGraph(db_path=str(tmp_path / "cgv2.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    def boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("builtins.open", boom)
    c = Conscience(model_client=_client("独白"))
    c._get_node_ids_from_events = MagicMock(return_value=[])
    await c.think("q")
    prompt = c._model_client.generate.call_args.args[0]
    assert "诚实、负责、安全、有益" in prompt


async def test_think_spatial_enhancement_error(monkeypatch, tmp_path):
    """空间增强配置读取抛异常 → except pass（349-350）"""
    import sys
    from modules.memory.causal_graph import CausalGraph
    g = CausalGraph(db_path=str(tmp_path / "cgv3.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: g))
    c = Conscience(model_client=_client("独白"))
    c._get_node_ids_from_events = MagicMock(return_value=[])

    class BoomSettings:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    monkeypatch.setattr(sys.modules["config.settings"], "settings", BoomSettings())
    out = await c.think("q")
    assert out == "独白"


async def test_think_outer_error(monkeypatch):
    """think 顶层异常 → 返回空串（377-379）"""
    c = Conscience(model_client=_client("x"))
    c._get_causal_knowledge = MagicMock(side_effect=RuntimeError("boom"))
    assert await c.think("q") == ""


def test_get_conscience_singleton(monkeypatch):
    monkeypatch.setattr(cons_mod, "_conscience_instance", None)
    a = get_conscience()
    b = get_conscience()
    assert a is b
