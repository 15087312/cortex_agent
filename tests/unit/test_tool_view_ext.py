"""工具卡片投影（Phase 2）扩展测试 — 纯函数边界

补充 `test_tool_view.py` 未覆盖的分支：结构化结果直传、非法 JSON、超长输出/行数
截断、read 错误结果、diff 的字符串/列表两种形态与截断、web/search 的脏条目、
meta 超限的两级降级。
"""
from modules.thinking import tool_view as tv


# ── 解析 / 截断 ──────────────────────────────────────────────────────────

def test_build_meta_accepts_structured_result():
    """工具结果已经是 dict → 直接使用（不再经过 str 解析）"""
    meta = tv.build_meta("exec_command", {"command": "pytest -q"},
                         {"exit_code": 1, "stdout": "boom"})
    assert meta["exit_code"] == 1 and "boom" in meta["output"]


def test_build_meta_invalid_json_and_literal_falls_back_to_text():
    """既不是 JSON 也不是 python 字面量 → 按纯文本展示（不抛异常）"""
    view = tv.present_result("read_file", {"path": "a.py"}, "{不是合法 JSON")
    assert view["card"] == tv.CARD_READ
    assert view["lines"][0][1] == "{不是合法 JSON"


def test_terminal_output_truncated_with_marker():
    meta = tv.build_meta("exec_command", {"command": "cat big"},
                         {"exit_code": 0, "stdout": "x" * (tv.MAX_OUTPUT + 50)})
    assert meta["output"].endswith(f"…(+{tv.MAX_OUTPUT + 50 - tv.MAX_OUTPUT} chars)")


def test_read_error_result_yields_empty_lines():
    """读取失败（只有 error）→ 空行集，前端显示错误而不是伪造内容"""
    view = tv.present_result("read_file", {"path": "a.py"}, {"path": "a.py", "error": "no such file"})
    assert view["lines"] == [] and view["total_lines"] == 0 and view["truncated"] is False


def test_read_truncates_to_max_lines():
    content = "\n".join(f"line{i}" for i in range(tv.MAX_LINES + 10))
    view = tv.present_result("read_file", {"path": "a.py"}, {"path": "a.py", "content": content})
    assert len(view["lines"]) == tv.MAX_LINES
    assert view["total_lines"] == tv.MAX_LINES + 10
    assert view["truncated"] is True


# ── diff ────────────────────────────────────────────────────────────────

def test_diff_from_string_payload():
    """diff 是字符串（unified diff）→ 按行统计增删"""
    diff = "--- a\n+++ b\n-old\n+new\n+extra\n"
    view = tv.present_result("write_text_file", {"path": "a.py"},
                             {"path": "a.py", "action": "updated", "diff": diff})
    assert view["additions"] == 2 and view["deletions"] == 1


def test_diff_from_list_of_dicts():
    """diff 是 [{path, lines}] 列表 → 摊平所有行统计"""
    payload = {"path": "a.py", "diffs": [{"path": "a.py", "lines": ["+x"]},
                                         {"path": "a.py", "lines": ["-y"]}]}
    view = tv.present_result("write_text_file", {}, payload)
    assert (view["additions"], view["deletions"]) == (1, 1)
    assert view["diffs"][0]["lines"] == ["+x", "-y"]


def test_diff_truncated_beyond_max_lines():
    diff = "\n".join(["+x"] * (tv.MAX_DIFF_LINES + 5))
    view = tv.present_result("write_text_file", {"path": "a.py"},
                             {"path": "a.py", "diff": diff})
    assert view["diffs"][0]["lines"][-1].startswith("…(diff 已截断")
    assert view["additions"] == tv.MAX_DIFF_LINES + 5


# ── web / search ─────────────────────────────────────────────────────────

def test_web_search_skips_non_dict_results():
    meta = tv.build_meta("web_search", {"query": "q"},
                         {"results": [{"title": "a", "url": "u"}, "b", 3]})
    assert [s["title"] for s in meta["sources"]] == ["a"]
    assert meta["total"] == 3


def test_web_fetch_plain_text_excerpt():
    """抓取结果只是纯文本 → 截成摘要，不猜结构"""
    view = tv.present_result("web_fetch", {"url": "https://x"}, "x" * 1000)
    assert view["kind"] == "page"
    assert view["url"] == "https://x"
    assert view["excerpt"].endswith("…")


def test_search_accepts_string_hits():
    view = tv.present_result("grep", {"pattern": "TODO"}, {"matches": ["a.py:12", "b.py:3"]})
    assert [m["text"] for m in view["matches"]] == ["a.py:12", "b.py:3"]
    assert all(m["line"] is None for m in view["matches"])


def test_search_truncated_flag_passthrough():
    view = tv.present_result("grep", {"pattern": "x"}, {"matches": [], "truncated": True})
    assert view["truncated"] is True


# ── _bounded 两级降级 ────────────────────────────────────────────────────

def test_bounded_returns_truncated_dict_when_unserializable():
    """meta 无法序列化（非基础类型键）→ 只回 {truncated: True}，保证事件可发"""
    assert tv._bounded({(1, 2): "x"}) == {"truncated": True}


def test_bounded_shortens_output_then_drops_lists():
    """超过 9000 字符 → 先砍 output，再清空列表；仍超限则只留白名单短字段"""
    meta = {"output": "x" * 4000, "matches": [{"text": "y" * 200} for _ in range(40)]}
    bounded = tv._bounded(meta)
    assert len(bounded["output"]) <= 2001
    assert "matches" not in bounded or len(bounded["matches"]) <= 20
    huge = {"path": "p" * 12000, "matches": [{"text": "y"}]}
    assert set(tv._bounded(huge)) <= {"path", "truncated"}


def test_present_result_unknown_tool_returns_none():
    assert tv.present_result("calc", {}, "2") is None
