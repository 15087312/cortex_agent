"""工具展示投影（Phase 2）：卡片视图 / meta 有界 / 实时与回放同构。"""
import json

from modules.thinking.tool_view import (
    CARD_DIFF,
    CARD_READ,
    CARD_SEARCH,
    CARD_TERMINAL,
    CARD_WEB,
    MAX_META_CHARS,
    build_meta,
    present_call,
    present_result,
)


# ── 调用瞬间的卡片骨架 ─────────────────────────────────────────────

def test_present_call_terminal():
    view = present_call("exec_command", {"command": "pytest -q", "workdir": "/repo"})
    assert view["card"] == CARD_TERMINAL
    assert view["title"] == "pytest -q"
    assert view["command"] == "pytest -q"
    assert view["cwd"] == "/repo"
    assert view["exit_code"] is None
    assert view["output"] == ""


def test_present_call_read_and_search():
    read = present_call("read_text_file", {"path": "/repo/modules/a.py"})
    assert read == {
        "card": CARD_READ, "title": "a.py", "path": "/repo/modules/a.py",
        "lines": [], "total_lines": 0, "truncated": False,
    }
    search = present_call("knowledge_search", {"query": "检索协议"})
    assert search["card"] == CARD_SEARCH
    assert search["title"] == "检索协议"
    assert search["matches"] == []


def test_present_call_write_is_diff_skeleton():
    view = present_call("write_text_file", {"path": "/repo/x.py", "content": "a\nb"})
    assert view["card"] == CARD_DIFF
    assert view["title"] == "x.py"
    assert view["diffs"] == [{"path": "/repo/x.py", "lines": []}]


def test_present_call_web_search_and_fetch():
    ws = present_call("web_search", {"query": "vue 3.5"})
    assert ws["card"] == CARD_WEB and ws["kind"] == "search" and ws["title"] == "vue 3.5"
    wf = present_call("web_fetch", {"url": "https://example.com/doc"})
    assert wf["card"] == CARD_WEB and wf["kind"] == "page"
    assert wf["url"] == "https://example.com/doc"


def test_present_call_unknown_returns_none():
    assert present_call("calc", {"expression": "1+1"}) is None
    assert present_call("", {}) is None


# ── 结果卡片：解析工具返回（本项目经 str(dict) 传递） ────────────────

def test_terminal_result_from_python_dict_repr():
    result = str({"stdout": "3 passed", "stderr": "", "exit_code": 0, "elapsed_seconds": 1.2})
    view = present_result("exec_command", {"command": "pytest -q", "workdir": "/repo"},
                          result=result, status="ok")
    assert view["card"] == CARD_TERMINAL
    assert view["exit_code"] == 0
    assert "3 passed" in view["output"]
    assert view["cwd"] == "/repo"


def test_terminal_result_error_only():
    view = present_result("exec_command", {"command": "false"},
                          result=str({"error": "超时（60秒）", "exit_code": -1}))
    assert view["exit_code"] == -1
    assert "超时" in view["output"]


def test_terminal_result_plain_text_fallback():
    view = present_result("run_script", {"code": "print(1)"}, result="1\n")
    assert view["card"] == CARD_TERMINAL
    assert view["output"] == "1"


def test_read_result_lines_and_total():
    content = "\n".join(f"line{i}" for i in range(100))
    result = str({"success": True, "path": "/repo/a.py", "content": content, "truncated": False})
    view = present_result("read_text_file", {"path": "/repo/a.py"}, result=result)
    assert view["card"] == CARD_READ
    assert view["title"] == "a.py"
    assert view["total_lines"] == 100
    assert len(view["lines"]) == 60
    assert view["lines"][0] == [1, "line0"]
    assert view["truncated"] is True


def test_diff_result_counts_and_keeps_lines():
    diff = [
        "--- a/x.py", "+++ b/x.py", "@@ -1,1 +1,2 @@",
        "-old", "+new", "+more",
    ]
    result = str({"success": True, "path": "/repo/x.py", "action": "updated", "diff": diff})
    view = present_result("write_text_file", {"path": "/repo/x.py"}, result=result)
    assert view["card"] == CARD_DIFF
    assert view["additions"] == 2
    assert view["deletions"] == 1
    assert view["diffs"][0]["lines"] == diff
    assert view["action"] == "updated"


def test_diff_result_without_diff_falls_back_to_generic():
    result = str({"success": True, "path": "/repo/x.py"})
    assert present_result("write_file", {"path": "/repo/x.py"}, result=result) is None


def test_web_search_sources_are_bounded():
    results = [
        {"title": f"t{i}", "url": f"https://e.com/{i}", "snippet": "s" * 400}
        for i in range(20)
    ]
    result = json.dumps({"query": "q", "results_count": 20, "results": results})
    view = present_result("web_search", {"query": "q"}, result=result)
    assert view["card"] == CARD_WEB and view["kind"] == "search"
    assert view["total"] == 20
    assert len(view["sources"]) == 8
    assert len(view["sources"][0]["snippet"]) <= 201


def test_web_fetch_page():
    result = json.dumps({"url": "https://e.com/a", "status_code": 200, "content": "body " * 400})
    view = present_result("web_fetch", {"url": "https://e.com/a"}, result=result)
    assert view["kind"] == "page"
    assert view["status_code"] == 200
    assert view["url"] == "https://e.com/a"
    assert len(view["excerpt"]) <= 601


def test_knowledge_search_hits():
    result = json.dumps({
        "hits": [
            {"title": "设计文档", "source_path": "docs/A.md", "heading_path": "4.1 事件表"},
            {"title": "接口", "source_path": "docs/B.md"},
        ],
        "count": 2,
        "truncated": True,
    })
    view = present_result("knowledge_search", {"query": "事件表"}, result=result)
    assert view["card"] == CARD_SEARCH
    assert view["total"] == 2
    assert view["matches"][0]["path"] == "docs/A.md"
    assert view["matches"][0]["text"] == "4.1 事件表"
    assert view["truncated"] is True


# ── meta：落库用，必须有界且可重建卡片 ───────────────────────────────

def test_build_meta_unknown_tool_is_none():
    assert build_meta("calc", {}, "2") is None


def test_build_meta_is_bounded_for_huge_read():
    result = str({"content": "\n".join("x" * 500 for _ in range(5000))})
    meta = build_meta("read_text_file", {"path": "/repo/big.txt"}, result)
    assert len(json.dumps(meta, ensure_ascii=False)) <= MAX_META_CHARS


def test_replay_from_meta_equals_live_view():
    args = {"command": "ls -la", "workdir": "/repo"}
    result = str({"stdout": "total 0\nfile", "exit_code": 0})
    live = present_result("exec_command", args, result=result)
    meta = build_meta("exec_command", args, result)
    replay = present_result("exec_command", args, meta=meta)
    assert replay == live


def test_replay_read_view_from_meta_only():
    content = "\n".join(f"l{i}" for i in range(5))
    meta = build_meta("read_text_file", {"path": "/repo/a.py"},
                      str({"path": "/repo/a.py", "content": content}))
    replay = present_result("read_text_file", {"path": "/repo/a.py"}, meta=meta)
    assert replay["lines"][-1] == [5, "l4"]
    assert replay["total_lines"] == 5


def test_unparsable_result_falls_back_to_generic():
    # 外部 MCP 工具可能返回纯文本；卡片不猜来源/命中，交回 generic
    meta = build_meta("grep", {"pattern": "TODO"}, "a.py:12: TODO fix")
    assert meta["unparsed"] is True
    assert present_result("grep", {"pattern": "TODO"}, meta=meta) is None
    wmeta = build_meta("web_search", {"query": "q"}, "1. something")
    assert present_result("web_search", {"query": "q"}, meta=wmeta) is None


def test_status_recorded_for_failure_without_output():
    meta = build_meta("web_search", {"query": "q"}, "{'error': 'boom'}", status="timeout")
    assert meta["error_status"] == "timeout"
