"""tool_discovery 测试（此前 0% 覆盖）：关键词/标签/分类/相关度"""
from infra.tool_manager.tool_discovery import ToolDiscoveryEngine
from infra.tool_manager.tool_registry import ToolRegistry


def _engine():
    return ToolDiscoveryEngine()


def test_search_exact_name():
    e = _engine()
    r = e.search("calc", limit=5)
    assert r and r[0].tool_name == "calc"
    assert r[0].match_reason == "exact_name"
    assert r[0].relevance_score == 1.0


def test_search_by_keyword():
    e = _engine()
    # "web" 应命中 web_search 等工具
    r = e.search("web search", limit=10)
    names = [x.tool_name for x in r]
    assert any("web" in n for n in names)


def test_search_limit_and_sort():
    e = _engine()
    r = e.search("calc", limit=3)
    assert len(r) <= 3
    # 相关度降序
    scores = [x.relevance_score for x in r]
    assert scores == sorted(scores, reverse=True)


def test_search_min_relevance_filters():
    e = _engine()
    r = e.search("完全不相关的词xyz", limit=10)
    assert all(x.relevance_score >= 0.3 for x in r)


def test_calculate_relevance_exact():
    e = _engine()
    info = ToolRegistry._tools.get("calc")
    assert info is not None, "calc 工具应已注册"
    score, reason = e._calculate_relevance("calc", ["calc"], "calc", info)
    assert score == 1.0 and reason == "exact_name"


def test_get_tools_by_category():
    e = _engine()
    q = e.get_tools_by_category("query")
    assert isinstance(q, list) and len(q) > 0
    admin = e.get_tools_by_category("admin")
    assert isinstance(admin, list)


def test_get_tools_by_tag():
    e = _engine()
    tagged = e.get_tools_by_tag("system")
    assert isinstance(tagged, list)
    assert e.get_tools_by_tag("不存在的标签") == []


def test_recommend_tools_for_task():
    e = _engine()
    rec = e.recommend_tools_for_task("计算 2+2", max_tools=5)
    assert isinstance(rec, list)
    assert len(rec) <= 5
    assert any("calc" in n for n in rec)


def test_build_indexes_populates():
    e = _engine()
    assert len(e._tool_keywords_cache) > 0
    assert "calc" in e._tool_keywords_cache
    assert any(kw.startswith("category:") for kw in e._tool_keywords_cache["calc"])


# ── 防御性分支：缓存过期重建 / 标签 / 分类 / 单例 ────────────────────────────

def test_maybe_rebuild_indexes_when_stale(monkeypatch):
    e = _engine()
    e._last_build_time = 0
    built = []
    monkeypatch.setattr(e, "_build_indexes", lambda: built.append(1))
    e._maybe_rebuild_indexes()
    assert len(built) == 1


def test_tag_match_relevance():
    e = _engine()
    info = ToolRegistry._tools.get("tools_search")
    assert info is not None
    score, reason = e._calculate_relevance("system", ["system"], "tools_search", info)
    assert score >= 0.7
    assert reason == "tag_match"


def test_category_match_relevance():
    e = _engine()
    info = ToolRegistry._tools.get("calc")
    score, reason = e._calculate_relevance("category:query", ["category:query"], "calc", info)
    assert score >= 0.3
    assert reason == "category_match"


def test_discovery_engine_singleton():
    from infra.tool_manager import tool_discovery as td
    a = td.get_tool_discovery_engine()
    b = td.get_tool_discovery_engine()
    assert a is b
    assert isinstance(a, ToolDiscoveryEngine)


def test_discovery_engine_creates_when_none(monkeypatch):
    from infra.tool_manager import tool_discovery as td
    monkeypatch.setattr(td, "_discovery_engine", None)
    e = td.get_tool_discovery_engine()
    assert isinstance(e, ToolDiscoveryEngine)


def test_discovery_engine_lock_reentry(monkeypatch):
    from infra.tool_manager import tool_discovery as td
    sentinel = object()

    class FakeLock:
        def __enter__(self):
            td._discovery_engine = sentinel
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(td, "_discovery_engine", None)
    monkeypatch.setattr(td, "_discovery_engine_lock", FakeLock())
    assert td.get_tool_discovery_engine() is sentinel
