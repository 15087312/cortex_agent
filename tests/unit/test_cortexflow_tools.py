"""cortexflow_tools 测试：URL 归一化 / 认证 / 错误翻译 / 各工具返回结构。

这些用例全部用假 HTTP 层（monkeypatch requests.request / requests.post），
不依赖真实的 cortexflow 服务，保证 CI 可重复。
"""
import json
import time
from unittest.mock import MagicMock

import pytest

import infra.tool_manager.tools.cortexflow_tools as cf


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _fake_settings(monkeypatch, **kw):
    s = MagicMock()
    # 默认值：本机 9000，无凭据
    s.CORTEXFLOW_URL = kw.get("url", "http://127.0.0.1:9000")
    s.CORTEXFLOW_USERNAME = kw.get("username", "")
    s.CORTEXFLOW_PASSWORD = kw.get("password", "")
    s.CORTEXFLOW_TOKEN = kw.get("token", "")
    monkeypatch.setattr(cf, "_settings", lambda: s)
    # 清掉 token 缓存与可能的环境变量，避免相互污染
    monkeypatch.setattr(cf, "_TOKEN_CACHE", {})
    monkeypatch.delenv("CORTEXFLOW_TOKEN", raising=False)
    monkeypatch.delenv("CORTEXFLOW_USERNAME", raising=False)
    monkeypatch.delenv("CORTEXFLOW_PASSWORD", raising=False)
    return s


# ── URL 归一化 ─────────────────────────────────────────────

def test_base_url_appends_api(monkeypatch):
    _fake_settings(monkeypatch, url="http://127.0.0.1:9000")
    assert cf._base_url() == "http://127.0.0.1:9000/api"


def test_base_url_keeps_existing_api_suffix(monkeypatch):
    _fake_settings(monkeypatch, url="http://127.0.0.1:9000/api/")
    assert cf._base_url() == "http://127.0.0.1:9000/api"


def test_base_url_falls_back_when_unset(monkeypatch):
    _fake_settings(monkeypatch, url="")
    assert cf._base_url() == "http://127.0.0.1:9000/api"


# ── 认证 ───────────────────────────────────────────────────

def test_token_from_settings_wins(monkeypatch):
    _fake_settings(monkeypatch, token="tok-abc")
    assert cf._resolve_token() == "tok-abc"


def test_token_from_env(monkeypatch):
    _fake_settings(monkeypatch)
    monkeypatch.setenv("CORTEXFLOW_TOKEN", "tok-env")
    assert cf._resolve_token() == "tok-env"


def test_login_exchanges_credentials_and_caches(monkeypatch):
    _fake_settings(monkeypatch, username="u", password="p")
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        return _Resp(200, {"token": "tok-login", "username": "u"})

    monkeypatch.setattr(cf.requests, "post", fake_post)
    assert cf._resolve_token() == "tok-login"
    # 再取一次应命中缓存，不再登录
    assert cf._resolve_token() == "tok-login"
    assert len(calls) == 1


def test_login_failure_returns_empty(monkeypatch):
    _fake_settings(monkeypatch, username="u", password="p")
    monkeypatch.setattr(cf.requests, "post", lambda *a, **k: _Resp(401, {"detail": "bad"}))
    assert cf._resolve_token() == ""


def test_login_expired_cache_relogins(monkeypatch):
    _fake_settings(monkeypatch, username="u", password="p")
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append(url)
        return _Resp(200, {"token": f"tok-{len(posts)}"})

    monkeypatch.setattr(cf.requests, "post", fake_post)
    assert cf._resolve_token() == "tok-1"
    # 人为把缓存置为过期
    cf._TOKEN_CACHE["expires_at"] = time.time() - 1
    assert cf._resolve_token() == "tok-2"
    assert len(posts) == 2


def test_auth_headers_omit_when_no_credentials(monkeypatch):
    _fake_settings(monkeypatch)
    assert cf._auth_headers() == {}


# ── 错误翻译 ───────────────────────────────────────────────

def test_request_401_message(monkeypatch):
    _fake_settings(monkeypatch)
    monkeypatch.setattr(cf.requests, "request", lambda *a, **k: _Resp(401, {"detail": "nope"}))
    res = cf._request("GET", "/workflows")
    assert res["success"] is False
    assert "认证失败" in res["error"]


def test_request_404_message(monkeypatch):
    _fake_settings(monkeypatch)
    monkeypatch.setattr(cf.requests, "request", lambda *a, **k: _Resp(404, {"detail": "none"}))
    res = cf._request("GET", "/workflows/x")
    assert res["success"] is False
    assert "不存在" in res["error"]


def test_request_500_surfaces_detail(monkeypatch):
    _fake_settings(monkeypatch)
    monkeypatch.setattr(
        cf.requests, "request",
        lambda *a, **k: _Resp(500, {"detail": "boom happened"}),
    )
    res = cf._request("POST", "/workflows/x/run")
    assert res["success"] is False
    assert "boom happened" in res["error"]


def test_request_connection_error_is_actionable(monkeypatch):
    _fake_settings(monkeypatch)

    def boom(*a, **k):
        raise cf.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(cf.requests, "request", boom)
    res = cf._request("GET", "/workflows")
    assert res["success"] is False
    assert "无法连接 cortexflow" in res["error"]


def test_request_timeout_message(monkeypatch):
    _fake_settings(monkeypatch)

    def boom(*a, **k):
        raise cf.requests.exceptions.Timeout()

    monkeypatch.setattr(cf.requests, "request", boom)
    res = cf._request("GET", "/workflows")
    assert res["success"] is False
    assert "超时" in res["error"]


# ── 各工具 ─────────────────────────────────────────────────

def _patch_request(monkeypatch, payload, status=200, capture=None):
    _fake_settings(monkeypatch)

    def fake(method, url, json=None, headers=None, timeout=None):
        if capture is not None:
            capture.update(method=method, url=url, json=json, timeout=timeout)
        return _Resp(status, payload)

    monkeypatch.setattr(cf.requests, "request", fake)


def test_list_workflows_summarizes(monkeypatch):
    _patch_request(monkeypatch, [
        {"id": "a", "name": "A", "status": "created", "version": 1,
         "definition": {"description": "d", "nodes": {"n1": {}, "n2": {}}}},
    ])
    res = cf.cortexflow_list_workflows()
    assert res["success"] is True and res["count"] == 1
    assert res["workflows"][0]["node_count"] == 2


def test_list_workflows_accepts_wrapped_shape(monkeypatch):
    _patch_request(monkeypatch, {"workflows": [{"id": "a", "name": "A", "definition": {}}]})
    res = cf.cortexflow_list_workflows()
    assert res["count"] == 1


def test_get_workflow_lists_nodes_without_full_config(monkeypatch):
    _patch_request(monkeypatch, {
        "id": "a", "name": "A", "definition": {
            "entry": "start", "variables": {"x": {"type": "string"}},
            "nodes": {"start": {"type": "state", "actions": [{"action": "set"}]}},
        },
    })
    res = cf.cortexflow_get_workflow("a")
    assert res["success"] is True
    node = res["workflow"]["nodes"][0]
    assert node["id"] == "start" and node["type"] == "state"
    # 不应把完整节点配置塞进返回（避免撑爆上下文）
    assert "actions" not in node


def test_get_workflow_requires_id(monkeypatch):
    _fake_settings(monkeypatch)
    res = cf.cortexflow_get_workflow("")
    assert res["success"] is False


def test_run_completed(monkeypatch):
    cap = {}
    _patch_request(monkeypatch, {"status": "completed", "workflow_id": "a", "result": {"out": 1}}, capture=cap)
    res = cf.cortexflow_run("a", variables='{"x": 1}', timeout=30)
    assert res["success"] is True and res["status"] == "completed"
    assert res["result"] == {"out": 1}
    assert cap["json"] == {"variables": {"x": 1}}
    assert cap["url"].endswith("/workflows/a/run")


def test_run_suspended_carries_question_and_hint(monkeypatch):
    _patch_request(monkeypatch, {
        "status": "suspended", "workflow_id": "a",
        "question": "确认吗？", "node_id": "confirm",
    })
    res = cf.cortexflow_run("a")
    assert res["status"] == "suspended"
    assert res["question"] == "确认吗？"
    assert "cortexflow_resume" in res["hint"]


def test_run_suspended_on_event(monkeypatch):
    _patch_request(monkeypatch, {"status": "suspended", "workflow_id": "a", "event": "approved"})
    res = cf.cortexflow_run("a")
    assert res["status"] == "suspended"
    assert res["waiting_event"] == "approved"


def test_run_failed_surfaces_error(monkeypatch):
    _patch_request(monkeypatch, {"status": "failed", "workflow_id": "a", "error": "boom"})
    res = cf.cortexflow_run("a")
    assert res["status"] == "failed" and res["error"] == "boom"


def test_run_rejects_bad_variables_json(monkeypatch):
    _fake_settings(monkeypatch)
    res = cf.cortexflow_run("a", variables="not-json")
    assert res["success"] is False
    assert "JSON" in res["error"]


def test_run_requires_workflow_id(monkeypatch):
    _fake_settings(monkeypatch)
    assert cf.cortexflow_run("")["success"] is False


def test_resume_sends_event_shape(monkeypatch):
    cap = {}
    _patch_request(monkeypatch, {"status": "resumed", "workflow_id": "a", "result": {"ok": 1}}, capture=cap)
    res = cf.cortexflow_resume("a", event_type="user_input", payload='{"answer": "yes"}')
    assert res["success"] is True
    assert cap["json"] == {"type": "user_input", "payload": {"answer": "yes"}}


def test_resume_defaults_event_type(monkeypatch):
    cap = {}
    _patch_request(monkeypatch, {"status": "resumed", "workflow_id": "a"}, capture=cap)
    cf.cortexflow_resume("a")
    assert cap["json"]["type"] == "user_input"


def test_resume_rejects_bad_payload_json(monkeypatch):
    _fake_settings(monkeypatch)
    res = cf.cortexflow_resume("a", payload="{oops")
    assert res["success"] is False
    assert "JSON" in res["error"]


def test_status_passthrough(monkeypatch):
    _patch_request(monkeypatch, {"workflow_id": "a", "status": "completed"})
    res = cf.cortexflow_status("a")
    assert res["success"] is True and res["status"] == "completed"


def test_trace_compacts_fields(monkeypatch):
    _patch_request(monkeypatch, {"traces": [
        {"node_id": "n1", "node_type": "state", "state": "completed",
         "error": None, "started_at": "t0", "completed_at": "t1",
         "inputs": {"huge": "x" * 500}, "outputs": {"huge": "y" * 500}},
    ]})
    res = cf.cortexflow_trace("a")
    assert res["node_count"] == 1
    t = res["traces"][0]
    assert t["node_id"] == "n1" and t["state"] == "completed"
    # inputs/outputs 不返回，避免轨迹撑爆上下文
    assert "inputs" not in t and "outputs" not in t


def test_cancel(monkeypatch):
    _patch_request(monkeypatch, {"status": "cancelled", "workflow_id": "a"})
    res = cf.cortexflow_cancel("a")
    assert res["success"] is True and res["status"] == "cancelled"


# ── 注册 ───────────────────────────────────────────────────

def test_all_tools_registered():
    from infra.tool_manager.tool_registry import ToolRegistry
    names = set(ToolRegistry.list_tools())
    for n in [
        "cortexflow_list_workflows", "cortexflow_get_workflow", "cortexflow_run",
        "cortexflow_status", "cortexflow_trace", "cortexflow_resume", "cortexflow_cancel",
    ]:
        assert n in names, f"{n} 未注册"


# ── 定义解析 ───────────────────────────────────────────────

def test_parse_definition_accepts_dict():
    d, err = cf._parse_definition({"id": "x"})
    assert err == "" and d == {"id": "x"}


def test_parse_definition_accepts_yaml():
    d, err = cf._parse_definition("id: x\nname: X\nnodes:\n  s:\n    type: state\n")
    assert err == ""
    assert d["id"] == "x" and d["nodes"]["s"]["type"] == "state"


def test_parse_definition_accepts_json():
    d, err = cf._parse_definition('{"id": "x", "nodes": {}}')
    assert err == "" and d["id"] == "x"


def test_parse_definition_rejects_empty():
    _, err = cf._parse_definition("   ")
    assert err


def test_parse_definition_rejects_non_mapping():
    _, err = cf._parse_definition("- a\n- b\n")
    assert "对象" in err


def test_parse_definition_rejects_broken_yaml():
    _, err = cf._parse_definition("id: [unclosed\n")
    assert "解析失败" in err


# ── 校验 ───────────────────────────────────────────────────

def _patch_validate(monkeypatch, valid=True, errors=None):
    _fake_settings(monkeypatch)
    payload = {"valid": valid, "errors": [{"message": e} for e in (errors or [])]}

    def fake(method, url, json=None, headers=None, timeout=None):
        assert url.endswith("/workflows/validate")
        return _Resp(200, payload)

    monkeypatch.setattr(cf.requests, "request", fake)


def test_validate_workflow_ok(monkeypatch):
    _patch_validate(monkeypatch, valid=True)
    r = cf.cortexflow_validate_workflow("id: x\nentry: s\nnodes:\n  s:\n    type: state\n")
    assert r["success"] is True and r["valid"] is True
    assert r["node_count"] == 1


def test_validate_workflow_reports_errors(monkeypatch):
    _patch_validate(monkeypatch, valid=False, errors=["nodes.s.type 非法"])
    r = cf.cortexflow_validate_workflow("id: x\nnodes:\n  s:\n    type: nope\n")
    assert r["valid"] is False
    assert "nodes.s.type 非法" in r["errors"][0]


def test_validate_workflow_bad_yaml(monkeypatch):
    _fake_settings(monkeypatch)
    # 未闭合的流式序列，safe_load 会直接抛 ParserError（不会走到网络请求）
    r = cf.cortexflow_validate_workflow("id: [unclosed")
    assert r["success"] is False
    assert "解析失败" in r["error"]


# ── 创建 ───────────────────────────────────────────────────

def _patch_write(monkeypatch, payload=None, status=200, capture=None, validate_valid=True):
    _fake_settings(monkeypatch)

    def fake(method, url, json=None, headers=None, timeout=None):
        if capture is not None:
            capture.update(method=method, url=url, json=json)
        if url.endswith("/workflows/validate"):
            if validate_valid:
                return _Resp(200, {"valid": True, "errors": []})
            return _Resp(200, {"valid": False,
                               "errors": [{"message": "nodes.s.type 非法"}]})
        return _Resp(status, payload if payload is not None else {})

    monkeypatch.setattr(cf.requests, "request", fake)


def test_create_workflow_ok(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"id": "n1", "name": "N1", "status": "created"}, capture=cap)
    r = cf.cortexflow_create_workflow("n1", "N1", "id: n1\nname: N1\nnodes:\n  s:\n    type: state\n")
    assert r["success"] is True and r["action"] == "created"
    assert cap["method"] == "POST" and cap["url"].endswith("/workflows")
    assert cap["json"]["id"] == "n1"


def test_create_workflow_blocks_on_invalid(monkeypatch):
    """核心：校验不通过必须不写入。"""
    cap = {}
    _patch_write(monkeypatch, capture=cap, validate_valid=False)
    r = cf.cortexflow_create_workflow("n1", "N1", "id: n1\nnodes:\n  s:\n    type: bad\n")
    assert r["success"] is False
    assert "未写入" in r["error"]
    assert r["validation_errors"] == ["nodes.s.type 非法"]
    # 不应发生创建请求
    assert cap.get("url", "").endswith("/workflows/validate")


def test_create_workflow_rejects_id_mismatch(monkeypatch):
    _fake_settings(monkeypatch)
    r = cf.cortexflow_create_workflow("other_id", "N", "id: n1\nnodes:\n  s:\n    type: state\n")
    assert r["success"] is False
    assert "不一致" in r["error"]


def test_create_workflow_fills_missing_id(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"id": "n1", "name": "N1"}, capture=cap)
    cf.cortexflow_create_workflow("n1", "N1", "name: N1\nnodes:\n  s:\n    type: state\n")
    assert cap["json"]["definition"]["id"] == "n1"


def test_create_workflow_skip_validate(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"id": "n1"}, capture=cap, validate_valid=False)
    r = cf.cortexflow_create_workflow(
        "n1", "N1", "id: n1\nnodes:\n  s:\n    type: bad\n", skip_validate=True
    )
    assert r["success"] is True
    assert cap["url"].endswith("/workflows")  # 直接创建，没走校验


def test_create_workflow_bad_yaml(monkeypatch):
    _fake_settings(monkeypatch)
    r = cf.cortexflow_create_workflow("n1", "N1", ":::bad")
    assert r["success"] is False


# ── 修改 ───────────────────────────────────────────────────

def test_update_workflow_definition(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"id": "n1", "name": "N1", "version": 2}, capture=cap)
    r = cf.cortexflow_update_workflow(
        "n1", definition="id: n1\nname: N1\nnodes:\n  s:\n    type: state\n"
    )
    assert r["success"] is True and r["version"] == 2
    assert cap["method"] == "PUT" and cap["url"].endswith("/workflows/n1")


def test_update_workflow_name_from_definition(monkeypatch):
    """definition 里改名应生效（模型常只改这里，不传 name 参数）。"""
    cap = {}
    _patch_write(monkeypatch, {"id": "n1", "name": "新名", "version": 2}, capture=cap)
    cf.cortexflow_update_workflow("n1", definition="id: n1\nname: 新名\nnodes:\n  s:\n    type: state\n")
    assert cap["json"]["name"] == "新名"


def test_update_workflow_explicit_name_wins(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"id": "n1", "name": "显式", "version": 2}, capture=cap)
    cf.cortexflow_update_workflow(
        "n1", name="显式", definition="id: n1\nname: 定义里的\nnodes:\n  s:\n    type: state\n"
    )
    assert cap["json"]["name"] == "显式"


def test_update_workflow_name_only(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"id": "n1", "name": "改个名", "version": 2}, capture=cap)
    r = cf.cortexflow_update_workflow("n1", name="改个名")
    assert r["success"] is True
    assert "definition" not in cap["json"]


def test_update_workflow_requires_something(monkeypatch):
    _fake_settings(monkeypatch)
    r = cf.cortexflow_update_workflow("n1")
    assert r["success"] is False


def test_update_workflow_blocks_on_invalid(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, capture=cap, validate_valid=False)
    r = cf.cortexflow_update_workflow("n1", definition="id: n1\nnodes:\n  s:\n    type: bad\n")
    assert r["success"] is False
    assert "未写入" in r["error"]


def test_update_workflow_rejects_id_mismatch(monkeypatch):
    _fake_settings(monkeypatch)
    r = cf.cortexflow_update_workflow("n1", definition="id: other\nnodes: {}\n")
    assert r["success"] is False and "不一致" in r["error"]


def test_update_workflow_passes_message(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"id": "n1", "version": 3}, capture=cap)
    cf.cortexflow_update_workflow("n1", name="N", message="改了什么")
    assert cap["json"]["message"] == "改了什么"


# ── 删除 / 版本 / 回滚 ─────────────────────────────────────

def test_delete_workflow(monkeypatch):
    _patch_write(monkeypatch, {"status": "deleted"})
    r = cf.cortexflow_delete_workflow("n1")
    assert r["success"] is True and r["action"] == "deleted"


def test_delete_workflow_requires_id(monkeypatch):
    _fake_settings(monkeypatch)
    assert cf.cortexflow_delete_workflow("")["success"] is False


def test_list_versions(monkeypatch):
    _patch_write(monkeypatch, {"count": 2, "versions": [{"version": 2}, {"version": 1}]})
    r = cf.cortexflow_list_versions("n1")
    assert r["success"] is True and r["count"] == 2


def test_rollback_workflow(monkeypatch):
    cap = {}
    _patch_write(monkeypatch, {"version": 3}, capture=cap)
    r = cf.cortexflow_rollback_workflow("n1", 1)
    assert r["success"] is True and r["new_version"] == 3
    assert cap["json"] == {"version": 1}


def test_rollback_workflow_rejects_non_int(monkeypatch):
    _fake_settings(monkeypatch)
    r = cf.cortexflow_rollback_workflow("n1", "abc")
    assert r["success"] is False


# ── 示例模板 ───────────────────────────────────────────────

def test_list_examples(monkeypatch):
    _patch_write(monkeypatch, {"count": 1, "examples": [{"filename": "a.yaml"}]})
    r = cf.cortexflow_list_examples()
    assert r["success"] is True and r["count"] == 1


def test_get_example(monkeypatch):
    _patch_write(monkeypatch, {"filename": "a.yaml", "content": "id: a\n", "meta": {}})
    r = cf.cortexflow_get_example("a.yaml")
    assert r["success"] is True and r["content"].startswith("id: a")


def test_get_example_requires_filename(monkeypatch):
    _fake_settings(monkeypatch)
    assert cf.cortexflow_get_example("")["success"] is False


# ── 写工具注册 ─────────────────────────────────────────────

def test_write_tools_registered():
    from infra.tool_manager.tool_registry import ToolRegistry
    names = set(ToolRegistry.list_tools())
    for n in [
        "cortexflow_validate_workflow", "cortexflow_create_workflow",
        "cortexflow_update_workflow", "cortexflow_delete_workflow",
        "cortexflow_list_versions", "cortexflow_rollback_workflow",
        "cortexflow_list_examples", "cortexflow_get_example",
    ]:
        assert n in names, f"{n} 未注册"
