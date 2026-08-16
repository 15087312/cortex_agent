"""tools_search 测试：关键词/类别/风险/来源过滤"""
from infra.tool_manager.tools.tools_search import tools_search


def test_tools_search_all_non_empty():
    out = tools_search()
    assert out.get("count", 0) > 0


def test_tools_search_keyword():
    out = tools_search(keyword="搜索")
    assert out.get("count", 0) >= 1
    for r in out.get("results", []):
        assert "搜索" in r["name"] or "搜索" in r["description"]


def test_tools_search_category():
    out = tools_search(category="query")
    assert out.get("count", 0) > 0
    for r in out.get("results", []):
        assert r["category"] == "query"


def test_tools_search_risk_level():
    out = tools_search(risk_level="LOW")
    assert out.get("count", 0) > 0
    for r in out.get("results", []):
        assert r["risk_level"] == "LOW"


def test_tools_search_source():
    out = tools_search(source="builtin")
    assert out.get("count", 0) > 0
    for r in out.get("results", []):
        assert r["source"] == "builtin"


def test_tools_search_no_match():
    out = tools_search(keyword="绝对不存在的工具xyz")
    assert out.get("count", 0) == 0


def test_tools_search_combined_filters():
    out = tools_search(category="query", risk_level="LOW", source="builtin")
    assert out.get("count", 0) > 0
    for r in out.get("results", []):
        assert r["category"] == "query"
        assert r["risk_level"] == "LOW"
        assert r["source"] == "builtin"
