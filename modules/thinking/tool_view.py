"""工具展示投影（Phase 2）— 把工具调用投射成类型化渲染意图。

设计见 docs/VISUALIZATION_DESIGN.md §4.4。三个纯函数，不做任何 IO：

- ``present_call(name, args)``：调用瞬间的卡片骨架（命令 / 路径 / 查询），
  前端据此立刻画出“运行中”的卡片，而不是等结果回来才知道长什么样；
- ``build_meta(name, args, result, status)``：把“卡片需要、但结果预览装不下”的
  数据压成有界 ``meta``（终端输出、文件行、来源列表、diff 行），随
  ``tool/result`` 落库；
- ``present_result(name, args, ..., meta)``：实时与回放走同一个函数，只依赖
  ``args + meta``，因此刷新后重建的卡片与当时完全一致。

渲染意图与 dsh 同构，字段按本项目工具的实际返回裁剪：

    {"card": "terminal", "title": "pytest -q", "command": ..., "cwd": ...,
     "exit_code": 0, "output": "..."}
    {"card": "diff", "title": "settings.py", "path": ..., "diffs": [{"path", "lines"}],
     "additions": 3, "deletions": 1}
    {"card": "read", "title": "settings.py", "path": ..., "lines": [[1, "..."], ...],
     "total_lines": 1375, "truncated": false}
    {"card": "search", "title": "KNOWLEDGE_", "matches": [{"path", "line", "text"}], "total": 3}
    {"card": "web", "kind": "search", "title": "vue 3.5", "sources": [{"title", "url", "snippet"}]}
    {"card": "generic", "title": ..., "content": "..."}

未知工具返回 ``None`` → 前端落 generic 卡片（不特殊化、不报错）。
"""
from __future__ import annotations

import ast
import json
import os
from typing import Any, Dict, List, Optional

CARD_TERMINAL = "terminal"
CARD_DIFF = "diff"
CARD_READ = "read"
CARD_SEARCH = "search"
CARD_WEB = "web"
CARD_GENERIC = "generic"

MAX_TITLE = 120
MAX_COMMAND = 600
MAX_OUTPUT = 4000
MAX_LINES = 60
MAX_LINE_CHARS = 200
MAX_SOURCES = 8
MAX_MATCHES = 20
MAX_DIFF_LINES = 80
MAX_DIFF_LINE_CHARS = 200
MAX_META_CHARS = 9000

# 工具名 → 卡片类型。MCP/技能引入的同名工具一并覆盖，未列出的走 generic。
TERMINAL_TOOLS = frozenset({"exec_command", "run_command", "run_script"})
READ_TOOLS = frozenset({"read_text_file", "read_file", "read_multiple_files"})
WRITE_TOOLS = frozenset({
    "write_text_file", "write_file", "edit_file", "str_replace_editor", "apply_patch",
})
WEB_SEARCH_TOOLS = frozenset({"web_search", "web_search_advanced"})
WEB_FETCH_TOOLS = frozenset({"web_fetch", "fetch_url"})
SEARCH_TOOLS = frozenset({"search_files", "grep", "rg", "knowledge_search", "tools_search"})


# ── 取值与截断 ──────────────────────────────────────────────────────────────

def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _short(value: Any, limit: int) -> str:
    """单行化 + 截断（标题/摘要用：换行会把卡片撑高）。"""
    text = " ".join(_text(value).split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _path_of(args: Dict[str, Any]) -> str:
    return _text(
        args.get("path") or args.get("file_path") or args.get("filepath")
        or args.get("filename") or args.get("target")
    )


def _basename(path: str) -> str:
    name = os.path.basename(str(path or "").replace("\\", "/").rstrip("/"))
    return name or str(path or "")


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_payload(result: Any) -> Optional[Any]:
    """把工具结果还原成结构体。

    本项目的工具结果经 ``str(dict)`` 传给事件层（单引号、非 JSON），
    因此先 json 再 ``ast.literal_eval``；都失败时返回 None，由调用方按纯文本处理。
    """
    if isinstance(result, (dict, list)):
        return result
    text = _text(result).strip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        value = ast.literal_eval(text)
    except Exception:
        return None
    return value if isinstance(value, (dict, list)) else None


# ── meta：卡片需要、结果预览装不下的数据 ─────────────────────────────────────

def _terminal_meta(args: Dict[str, Any], parsed: Any, text: str) -> Dict[str, Any]:
    command = _text(args.get("command") or args.get("cmd") or args.get("code"))
    cwd = _text(args.get("workdir") or args.get("cwd"))
    exit_code: Optional[int] = None
    chunks: List[str] = []
    if isinstance(parsed, dict):
        exit_code = _to_int(parsed.get("exit_code", parsed.get("returncode")))
        for key in ("stdout", "stderr", "output", "content"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.rstrip())
        error = parsed.get("error")
        if isinstance(error, str) and error.strip() and not chunks:
            chunks.append(f"[错误] {error.strip()}")
    elif text.strip():
        chunks.append(text.rstrip())
    output = "\n".join(chunks)
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + f"\n…(+{len(output) - MAX_OUTPUT} chars)"
    return {
        "command": _short(command, MAX_COMMAND),
        "cwd": cwd,
        "exit_code": exit_code,
        "output": output,
    }


def _read_meta(args: Dict[str, Any], parsed: Any, text: str) -> Dict[str, Any]:
    path = _path_of(args)
    content = ""
    if isinstance(parsed, dict):
        path = _text(parsed.get("path")) or path
        content = _text(parsed.get("content") or parsed.get("text") or parsed.get("data"))
        if not content and parsed.get("error"):
            return {"path": path, "total_lines": 0, "lines": [], "truncated": False}
    else:
        content = text
    all_lines = content.splitlines()
    lines = [[i, line[:MAX_LINE_CHARS]] for i, line in enumerate(all_lines[:MAX_LINES], start=1)]
    truncated = len(all_lines) > MAX_LINES or bool(
        isinstance(parsed, dict) and parsed.get("truncated")
    )
    return {
        "path": path,
        "total_lines": len(all_lines),
        "lines": lines,
        "truncated": truncated,
    }


def _write_meta(args: Dict[str, Any], parsed: Any, text: str) -> Dict[str, Any]:
    path = _path_of(args)
    action = ""
    raw_diff: List[str] = []
    if isinstance(parsed, dict):
        path = _text(parsed.get("path")) or path
        action = _text(parsed.get("action"))
        raw = parsed.get("diff") or parsed.get("diffs")
        if isinstance(raw, str):
            raw_diff = raw.splitlines()
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    raw_diff.extend(_text(ln) for ln in (item.get("lines") or []))
                else:
                    raw_diff.append(_text(item))
    additions = sum(1 for ln in raw_diff if ln.startswith("+") and not ln.startswith("+++"))
    deletions = sum(1 for ln in raw_diff if ln.startswith("-") and not ln.startswith("---"))
    lines = [ln[:MAX_DIFF_LINE_CHARS] for ln in raw_diff[:MAX_DIFF_LINES]]
    if len(raw_diff) > MAX_DIFF_LINES:
        lines.append(f"…(diff 已截断，共 {len(raw_diff)} 行)")
    return {
        "path": path,
        "action": action,
        "additions": additions,
        "deletions": deletions,
        "diff_lines": lines,
    }


def _web_search_meta(args: Dict[str, Any], parsed: Any, text: str) -> Dict[str, Any]:
    query = _text(args.get("query") or args.get("q"))
    sources: List[Dict[str, str]] = []
    total = 0
    if not isinstance(parsed, dict) and text.strip():
        # 不认识的结果格式（外部 MCP 工具）→ 不猜，交回 generic 卡片显示原文
        return {"kind": "search", "query": _short(query, 200), "total": 0,
                "sources": [], "url": "", "excerpt": "", "unparsed": True}
    if isinstance(parsed, dict):
        results = parsed.get("results") or parsed.get("items") or []
        if isinstance(results, list):
            total = _to_int(parsed.get("results_count")) or len(results)
            for item in results[:MAX_SOURCES]:
                if not isinstance(item, dict):
                    continue
                sources.append({
                    "title": _short(item.get("title") or item.get("name") or item.get("url"), 160),
                    "url": _text(item.get("url") or item.get("link")),
                    "snippet": _short(item.get("snippet") or item.get("description") or "", 200),
                })
    return {
        "kind": "search",
        "query": _short(query, 200),
        "total": total,
        "sources": sources,
        "url": "",
        "excerpt": "",
    }


def _web_fetch_meta(args: Dict[str, Any], parsed: Any, text: str) -> Dict[str, Any]:
    url = _text(args.get("url"))
    excerpt = ""
    status_code: Optional[int] = None
    if isinstance(parsed, dict):
        url = _text(parsed.get("url")) or url
        status_code = _to_int(parsed.get("status_code"))
        excerpt = _short(parsed.get("content") or parsed.get("text") or "", 600)
    elif text:
        excerpt = _short(text, 600)
    return {
        "kind": "page",
        "query": "",
        "total": 1,
        "sources": [],
        "url": url,
        "status_code": status_code,
        "excerpt": excerpt,
    }


def _search_meta(args: Dict[str, Any], parsed: Any, text: str) -> Dict[str, Any]:
    query = _text(args.get("query") or args.get("q") or args.get("pattern"))
    matches: List[Dict[str, Any]] = []
    total = 0
    truncated = False
    if not isinstance(parsed, dict) and text.strip():
        return {"query": _short(query, 200), "total": 0, "matches": [],
                "truncated": False, "unparsed": True}
    if isinstance(parsed, dict):
        hits = parsed.get("hits") or parsed.get("matches") or parsed.get("results") or []
        if isinstance(hits, list):
            total = _to_int(parsed.get("count") or parsed.get("total")) or len(hits)
            for hit in hits[:MAX_MATCHES]:
                if isinstance(hit, dict):
                    matches.append({
                        "path": _short(
                            hit.get("source_path") or hit.get("path") or hit.get("title")
                            or hit.get("doc_id") or hit.get("file") or "", 160,
                        ),
                        "line": _to_int(hit.get("line") or hit.get("line_number")),
                        "text": _short(
                            hit.get("heading_path") or hit.get("snippet") or hit.get("text")
                            or hit.get("title") or "", 240,
                        ),
                    })
                elif isinstance(hit, str):
                    matches.append({"path": "", "line": None, "text": _short(hit, 240)})
        truncated = bool(parsed.get("truncated"))
    return {
        "query": _short(query, 200),
        "total": total,
        "matches": matches,
        "truncated": truncated,
    }


def _bounded(meta: Dict[str, Any]) -> Dict[str, Any]:
    """meta 上限：先砍长列表，再退化为只保留短字段（保证 data JSON 可解析）。"""
    try:
        if len(json.dumps(meta, ensure_ascii=False, default=str)) <= MAX_META_CHARS:
            return meta
    except Exception:
        return {"truncated": True}
    data = dict(meta)
    for key in ("output", "excerpt"):
        value = data.get(key)
        if isinstance(value, str) and len(value) > 2000:
            data[key] = value[:2000] + "…"
    for key in ("lines", "sources", "matches", "diff_lines"):
        items = data.get(key)
        if isinstance(items, list) and items:
            for cut in (len(items) // 2, 0):
                data[key] = items[:cut]
                data["truncated"] = True
                try:
                    if len(json.dumps(data, ensure_ascii=False, default=str)) <= MAX_META_CHARS:
                        return data
                except Exception:
                    break
    keep = {"path", "total_lines", "exit_code", "total", "additions", "deletions",
            "truncated", "kind", "action", "url", "status_code", "unparsed"}
    return {k: v for k, v in data.items() if k in keep}


def build_meta(name: str, args: Any, result: Any = None, status: str = "ok") -> Optional[Dict[str, Any]]:
    """结果落库前压出有界 meta；未知工具返回 None（不写冗余字段）。"""
    name = _text(name)
    safe_args = args if isinstance(args, dict) else {}
    parsed = _parse_payload(result)
    text = result if isinstance(result, str) else ("" if result is None else str(result))
    if name in TERMINAL_TOOLS:
        meta = _terminal_meta(safe_args, parsed, text)
    elif name in READ_TOOLS:
        meta = _read_meta(safe_args, parsed, text)
    elif name in WRITE_TOOLS:
        meta = _write_meta(safe_args, parsed, text)
    elif name in WEB_SEARCH_TOOLS:
        meta = _web_search_meta(safe_args, parsed, text)
    elif name in WEB_FETCH_TOOLS:
        meta = _web_fetch_meta(safe_args, parsed, text)
    elif name in SEARCH_TOOLS:
        meta = _search_meta(safe_args, parsed, text)
    else:
        return None
    if status in ("error", "timeout", "denied", "invalid", "interrupted") and not meta.get("output"):
        meta["error_status"] = status
    return _bounded(meta)


# ── view：实时与回放共用的卡片 ────────────────────────────────────────────────

def _terminal_view(meta: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    command = _text(meta.get("command")) or _short(
        args.get("command") or args.get("cmd") or args.get("code") or "", MAX_COMMAND
    )
    return {
        "card": CARD_TERMINAL,
        "title": _short(command, MAX_TITLE) or "terminal",
        "command": command,
        "cwd": _text(meta.get("cwd")),
        "exit_code": meta.get("exit_code"),
        "output": _text(meta.get("output")),
    }


def _read_view(meta: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    path = _text(meta.get("path")) or _path_of(args)
    return {
        "card": CARD_READ,
        "title": _basename(path),
        "path": path,
        "lines": meta.get("lines") or [],
        "total_lines": _to_int(meta.get("total_lines")) or 0,
        "truncated": bool(meta.get("truncated")),
    }


def _diff_view(meta: Dict[str, Any], args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lines = meta.get("diff_lines") or []
    if not lines:
        return None  # 工具没给出 diff（如 MCP write_file）→ 前端回退 generic
    path = _text(meta.get("path")) or _path_of(args)
    return {
        "card": CARD_DIFF,
        "title": _basename(path),
        "path": path,
        "diffs": [{"path": path, "lines": lines}],
        "additions": _to_int(meta.get("additions")) or 0,
        "deletions": _to_int(meta.get("deletions")) or 0,
        "action": _text(meta.get("action")),
    }


def _web_view(meta: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    kind = _text(meta.get("kind")) or "search"
    if kind == "page":
        url = _text(meta.get("url")) or _text(args.get("url"))
        return {
            "card": CARD_WEB,
            "kind": "page",
            "title": _short(url, MAX_TITLE) or "web_fetch",
            "url": url,
            "status_code": meta.get("status_code"),
            "excerpt": _text(meta.get("excerpt")),
            "sources": [],
            "total": 1,
        }
    query = _text(meta.get("query")) or _short(args.get("query") or args.get("q") or "", 200)
    return {
        "card": CARD_WEB,
        "kind": "search",
        "title": _short(query, MAX_TITLE) or "web_search",
        "url": "",
        "excerpt": "",
        "sources": meta.get("sources") or [],
        "total": _to_int(meta.get("total")) or 0,
    }


def _search_view(meta: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    query = _text(meta.get("query")) or _short(
        args.get("query") or args.get("q") or args.get("pattern") or "", 200
    )
    return {
        "card": CARD_SEARCH,
        "title": _short(query, MAX_TITLE) or "search",
        "matches": meta.get("matches") or [],
        "total": _to_int(meta.get("total")) or 0,
        "truncated": bool(meta.get("truncated")),
    }


def present_call(name: str, args: Any) -> Optional[Dict[str, Any]]:
    """调用瞬间的卡片骨架（此时还没有结果）。"""
    name = _text(name)
    safe_args = args if isinstance(args, dict) else {}
    if name in TERMINAL_TOOLS:
        command = _short(
            safe_args.get("command") or safe_args.get("cmd") or safe_args.get("code") or "",
            MAX_COMMAND,
        )
        return _terminal_view({"command": command, "cwd": _text(
            safe_args.get("workdir") or safe_args.get("cwd")
        )}, safe_args)
    if name in READ_TOOLS:
        return _read_view({}, safe_args)
    if name in WRITE_TOOLS:
        path = _path_of(safe_args)
        return {
            "card": CARD_DIFF,
            "title": _basename(path),
            "path": path,
            "diffs": [{"path": path, "lines": []}],
            "additions": 0,
            "deletions": 0,
            "action": "",
        }
    if name in WEB_SEARCH_TOOLS or name in WEB_FETCH_TOOLS:
        return _web_view({"kind": "page" if name in WEB_FETCH_TOOLS else "search"}, safe_args)
    if name in SEARCH_TOOLS:
        return _search_view({}, safe_args)
    return None


def present_result(
    name: str,
    args: Any = None,
    result: Any = None,
    status: str = "ok",
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """结果卡片：回放时只传 ``meta``（live 时可直接传 result 现算）。"""
    name = _text(name)
    safe_args = args if isinstance(args, dict) else {}
    data = meta if isinstance(meta, dict) else build_meta(name, safe_args, result, status)
    data = data or {}
    if data.get("unparsed"):
        return None  # 结果格式不认识 → generic 卡片直接显示原文，避免“0 命中”误导
    if name in TERMINAL_TOOLS:
        return _terminal_view(data, safe_args)
    if name in READ_TOOLS:
        return _read_view(data, safe_args)
    if name in WRITE_TOOLS:
        return _diff_view(data, safe_args)
    if name in WEB_SEARCH_TOOLS or name in WEB_FETCH_TOOLS:
        return _web_view(data, safe_args)
    if name in SEARCH_TOOLS:
        return _search_view(data, safe_args)
    return None


__all__ = [
    "CARD_TERMINAL", "CARD_DIFF", "CARD_READ", "CARD_SEARCH", "CARD_WEB", "CARD_GENERIC",
    "TERMINAL_TOOLS", "READ_TOOLS", "WRITE_TOOLS", "WEB_SEARCH_TOOLS", "WEB_FETCH_TOOLS",
    "SEARCH_TOOLS", "build_meta", "present_call", "present_result",
]
