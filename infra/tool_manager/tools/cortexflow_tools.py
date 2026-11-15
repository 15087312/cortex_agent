"""CortexFlow 工作流工具 — 把外部工作流引擎（cortexflow）接入 ai_backend。

cortexflow 是本地优先的工作流编排引擎（FastAPI，默认 127.0.0.1:9000），
把工作流定义为 DAG 并逐节点执行。本模块把它包成一组工具，让模型能够：
列出/查看工作流 → 运行 → 查看状态与执行轨迹 → 回答挂起问题（resume）/取消。

设计要点：
1. cortexflow 的 /api 路由整体挂 `Depends(require_user)`，除 `CORTEXFLOW_DISABLE_AUTH=1`
   外一律需要 Bearer JWT。因此这里实现了「用账号密码换 token 并缓存」的最小客户端；
   也支持直接给现成 token（CORTEXFLOW_TOKEN），便于服务端关掉登录的场景。
2. `POST /workflows/{id}/run` 是**阻塞式**的：它一直等到工作流 completed / suspended /
   failed 才返回，不存在“提交后轮询”的异步作业语义。所以 run 工具用较短的超时并
   明确返回 suspended（挂起等你回答）状态，让模型知道该走 cortexflow_resume。
3. 工具函数统一用同步 requests：工具执行器会把 async 工具丢进线程池另起事件循环，
   同步实现反而更直白且不踩嵌套 loop 的坑。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("cortexflow_tools")

# 单次 HTTP 请求超时（秒）。run 会阻塞到工作流结束，用更长的超时。
_HTTP_TIMEOUT = 10
_RUN_TIMEOUT = 300
# token 提前过期的安全边界：缓存到 expires_at - _TOKEN_SKEW 为止
_TOKEN_SKEW = 60

# 进程内 token 缓存：{"token": str, "expires_at": float}
_TOKEN_CACHE: Dict[str, Any] = {}


def _settings():
    from config.settings import settings
    return settings


def _base_url() -> str:
    """cortexflow 服务地址（含 /api 前缀）"""
    base = (getattr(_settings(), "CORTEXFLOW_URL", "") or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:9000"
    # 允许配置写成 http://host:9000 或 http://host:9000/api，统一补 /api
    if not base.endswith("/api"):
        base = f"{base}/api"
    return base


def _auth_headers() -> Dict[str, str]:
    """构造认证头。取 token 顺序：环境变量直给 → settings 直给 → 账号密码换。

    换不到 token 时不抛异常，直接不带 Authorization 发出——若服务端用
    CORTEXFLOW_DISABLE_AUTH=1 跑着，这样反而正好能通。
    """
    token = _resolve_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _resolve_token() -> str:
    import os
    # 1) 环境变量 / settings 里直接配置的现成 token
    token = (os.environ.get("CORTEXFLOW_TOKEN", "") or
             getattr(_settings(), "CORTEXFLOW_TOKEN", "") or "").strip()
    if token:
        return token

    # 2) 账号密码换 token（带缓存，避免每个工具调用都登录一次）
    cached = _TOKEN_CACHE.get("token")
    if cached and time.time() < float(_TOKEN_CACHE.get("expires_at", 0)):
        return str(cached)

    username = (os.environ.get("CORTEXFLOW_USERNAME", "") or
                getattr(_settings(), "CORTEXFLOW_USERNAME", "") or "").strip()
    password = (os.environ.get("CORTEXFLOW_PASSWORD", "") or
                getattr(_settings(), "CORTEXFLOW_PASSWORD", "") or "").strip()
    if not (username and password):
        return ""

    try:
        resp = requests.post(
            f"{_base_url()}/login",
            json={"username": username, "password": password},
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(f"[cortexflow] 登录失败 HTTP {resp.status_code}: {resp.text[:200]}")
            return ""
        data = resp.json()
        token = str(data.get("token") or "")
        if not token:
            return ""
        # JWT 默认 24h（见 cortexflow api/jwt.py），缓存 1 小时足够且不依赖解析
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = time.time() + 3600 - _TOKEN_SKEW
        return token
    except Exception as e:
        logger.warning(f"[cortexflow] 登录请求异常: {e}")
        return ""


def _request(method: str, path: str, *, json_body: Any = None,
             timeout: int = _HTTP_TIMEOUT) -> Dict[str, Any]:
    """统一请求封装：把 HTTP/网络错误翻译成结构化结果，不抛给模型。"""
    url = f"{_base_url()}{path}"
    try:
        resp = requests.request(
            method, url, json=json_body, headers=_auth_headers(), timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return {"success": False, "error": f"请求超时（{timeout}s）：{method} {path}"}
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": (
                f"无法连接 cortexflow（{_base_url()}）。请确认服务已启动："
                "在 cortexflow 目录执行 python run.py"
            ),
        }
    except Exception as e:
        return {"success": False, "error": f"请求失败：{type(e).__name__}: {e}"}

    if resp.status_code == 401:
        return {
            "success": False,
            "error": (
                "cortexflow 认证失败（401）。请在 ai_backend 配置 CORTEXFLOW_USERNAME / "
                "CORTEXFLOW_PASSWORD（或直接配置 CORTEXFLOW_TOKEN）；"
                "若服务端以 CORTEXFLOW_DISABLE_AUTH=1 运行则可忽略此项。"
            ),
        }
    if resp.status_code == 404:
        return {"success": False, "error": f"对象不存在（404）：{path}"}
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str(resp.json().get("detail", ""))
        except Exception:
            detail = resp.text[:300]
        return {"success": False, "error": f"cortexflow 返回 {resp.status_code}：{detail}"}

    try:
        return {"success": True, "data": resp.json()}
    except Exception:
        return {"success": True, "data": {"raw": resp.text[:2000]}}


def _summarize_workflow(wf: Dict[str, Any]) -> Dict[str, Any]:
    """把工作流记录裁剪成模型好读的摘要（definition 可能很大）"""
    definition = wf.get("definition") or {}
    nodes = definition.get("nodes") or {}
    return {
        "id": wf.get("id"),
        "name": wf.get("name"),
        "status": wf.get("status"),
        "version": wf.get("version"),
        "description": definition.get("description", ""),
        "node_count": len(nodes) if isinstance(nodes, dict) else 0,
    }


def _summarize_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把 run/resume 的返回值整理成模型可行动的形式。

    重点是把 suspended 说清楚：那不是失败，而是工作流在等一个回答，
    需要调用 cortexflow_resume 继续。
    """
    status = payload.get("status")
    out: Dict[str, Any] = {"status": status, "workflow_id": payload.get("workflow_id")}
    if status == "completed":
        out["result"] = payload.get("result")
    elif status == "suspended":
        if payload.get("question"):
            out["question"] = payload["question"]
            out["node_id"] = payload.get("node_id")
            out["hint"] = "工作流在提问，请调用 cortexflow_resume 传入回答后继续。"
        elif payload.get("event"):
            out["waiting_event"] = payload["event"]
            out["hint"] = (
                f"工作流正在等待事件 {payload['event']!r}，"
                "可用 cortexflow_resume 发送该事件后继续。"
            )
        else:
            out["hint"] = "工作流已挂起，可用 cortexflow_resume 继续。"
    elif status == "failed":
        out["error"] = payload.get("error")
    else:
        out.update(payload)
    return out


def _parse_definition(definition: Any) -> tuple:
    """把模型给出的工作流定义解析成 dict。

    模型写定义用 YAML 比 JSON 顺手得多（多行 code 片段、注释都不用转义），
    而 JSON 本身是 YAML 的子集，所以统一按 YAML 解析即可覆盖两种写法。

    Returns:
        (definition_dict, error_message)。error 非空表示解析失败。
    """
    if isinstance(definition, dict):
        return definition, ""
    if not isinstance(definition, str) or not definition.strip():
        return None, "definition 不能为空（需要 YAML 或 JSON 文本）"
    try:
        import yaml
        data = yaml.safe_load(definition)
    except Exception as e:
        return None, f"YAML/JSON 解析失败：{e}"
    if not isinstance(data, dict):
        return None, (
            f"definition 必须解析成对象（键值对），当前是 {type(data).__name__}。"
            "请检查缩进，或确认顶层就是 id/name/entry/nodes 这些字段。"
        )
    return data, ""


def _validate_definition(defn: Dict[str, Any]) -> Dict[str, Any]:
    """调用 cortexflow 的校验端点。返回 {ok: bool, errors: [...]}"""
    res = _request("POST", "/workflows/validate", json_body=defn)
    if not res.get("success"):
        return {"ok": False, "errors": [res.get("error", "校验请求失败")]}
    data = res.get("data") or {}
    if data.get("valid"):
        return {"ok": True, "errors": []}
    errors = [
        str(e.get("message", "")).strip()
        for e in (data.get("errors") or [])
        if isinstance(e, dict)
    ]
    return {"ok": False, "errors": [e for e in errors if e]}


def _format_validation_error(errors: List[str]) -> str:
    """把校验错误压成一段模型能读懂、能据此修改的提示。"""
    joined = "；".join(errors)[:1800]
    return (
        f"工作流定义校验未通过（未写入）：{joined}\n"
        "请按上面的字段/取值提示修正 definition 后重试；"
        "不确定正确结构时，可先调用 cortexflow_get_example 参考现成模板。"
    )


@ToolRegistry.register(
    "cortexflow_list_workflows",
    description=(
        "列出 cortexflow 工作流引擎中已注册的全部工作流（返回 id/名称/状态/节点数）。"
        "需要运行某个工作流但不知道它的 id 时，先调用本工具。"
    ),
    params={},
    risk_level="LOW",
    category="query",
    core=True,
)
def cortexflow_list_workflows() -> Dict[str, Any]:
    """列出 cortexflow 中的全部工作流"""
    res = _request("GET", "/workflows")
    if not res.get("success"):
        return res
    raw = res.get("data")
    items = raw if isinstance(raw, list) else (raw or {}).get("workflows", [])
    workflows = [_summarize_workflow(w) for w in items if isinstance(w, dict)]
    return {"success": True, "count": len(workflows), "workflows": workflows}


@ToolRegistry.register(
    "cortexflow_get_workflow",
    description=(
        "查看某个 cortexflow 工作流的完整定义（节点、连线、入口节点）。"
        "在运行工作流之前想确认它需要哪些输入变量时使用。"
    ),
    params={
        "workflow_id": "工作流 ID（来自 cortexflow_list_workflows）",
    },
    risk_level="LOW",
    category="query",
    core=True,
)
def cortexflow_get_workflow(workflow_id: str) -> Dict[str, Any]:
    """查看工作流完整定义"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    res = _request("GET", f"/workflows/{workflow_id}")
    if not res.get("success"):
        return res
    wf = res.get("data") or {}
    definition = wf.get("definition") or {}
    nodes = definition.get("nodes") or {}
    # 只列节点 id/类型，避免把整个 DAG 配置塞进上下文
    node_list = []
    if isinstance(nodes, dict):
        for nid, nd in nodes.items():
            nd = nd if isinstance(nd, dict) else {}
            node_list.append({
                "id": nid,
                "type": nd.get("type"),
                "description": nd.get("description", ""),
            })
    return {
        "success": True,
        "workflow": {
            "id": wf.get("id"),
            "name": wf.get("name"),
            "status": wf.get("status"),
            "version": wf.get("version"),
            "description": definition.get("description", ""),
            "entry": definition.get("entry"),
            "variables": definition.get("variables"),
            "nodes": node_list,
        },
    }


@ToolRegistry.register(
    "cortexflow_run",
    description=(
        "运行一个 cortexflow 工作流并等待其返回。"
        "注意：本调用会阻塞直到工作流完成/挂起/失败（最长约 5 分钟）。"
        "返回 status=completed 时结果在 result 中；"
        "status=suspended 表示工作流在提问或等事件，需再用 cortexflow_resume 继续；"
        "status=failed 看 error 字段。"
    ),
    params={
        "workflow_id": "工作流 ID",
        "variables": "可选，工作流输入变量（JSON 对象字符串，如 {\"topic\": \"AI\"}）",
        "timeout": "可选，等待超时秒数（默认 300）",
    },
    risk_level="MEDIUM",
    category="execute",
    core=True,
)
def cortexflow_run(workflow_id: str, variables: Optional[str] = None,
                   timeout: Optional[int] = None) -> Dict[str, Any]:
    """运行工作流并返回结果"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}

    body: Dict[str, Any] = {}
    if variables:
        if isinstance(variables, str):
            import json
            try:
                body["variables"] = json.loads(variables)
            except Exception as e:
                return {"success": False, "error": f"variables 不是合法 JSON：{e}"}
        else:
            body["variables"] = variables

    try:
        wait = int(timeout) if timeout else _RUN_TIMEOUT
    except Exception:
        wait = _RUN_TIMEOUT

    res = _request("POST", f"/workflows/{workflow_id}/run",
                   json_body=body or None, timeout=max(wait, 10))
    if not res.get("success"):
        return res
    return {"success": True, **_summarize_run(res.get("data") or {})}


@ToolRegistry.register(
    "cortexflow_status",
    description=(
        "查询某个 cortexflow 工作流的最近执行状态（若无执行记录则返回工作流自身状态）。"
    ),
    params={"workflow_id": "工作流 ID"},
    risk_level="LOW",
    category="query",
)
def cortexflow_status(workflow_id: str) -> Dict[str, Any]:
    """查询工作流执行状态"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    res = _request("GET", f"/workflows/{workflow_id}/status")
    if not res.get("success"):
        return res
    return {"success": True, **(res.get("data") or {})}


@ToolRegistry.register(
    "cortexflow_trace",
    description=(
        "获取某个 cortexflow 工作流的执行轨迹：每个节点的类型、状态、耗时、输入输出摘要与报错。"
        "工作流失败或结果不符合预期时用它定位是哪一步出了问题。"
    ),
    params={"workflow_id": "工作流 ID"},
    risk_level="LOW",
    category="query",
)
def cortexflow_trace(workflow_id: str) -> Dict[str, Any]:
    """获取工作流执行轨迹"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    res = _request("GET", f"/workflows/{workflow_id}/trace")
    if not res.get("success"):
        return res
    traces = (res.get("data") or {}).get("traces") or []
    # 轨迹可能很长：保留关键字段，避免撑爆上下文
    compact = [
        {
            "node_id": t.get("node_id"),
            "node_type": t.get("node_type"),
            "state": t.get("state"),
            "error": t.get("error"),
            "started_at": t.get("started_at"),
            "completed_at": t.get("completed_at"),
        }
        for t in traces if isinstance(t, dict)
    ]
    return {"success": True, "workflow_id": workflow_id,
            "node_count": len(compact), "traces": compact}


@ToolRegistry.register(
    "cortexflow_resume",
    description=(
        "继续（唤醒）一个处于 suspended 状态的 cortexflow 工作流。"
        "当 cortexflow_run 返回 status=suspended 且带 question 时，"
        "把用户对该问题的回答通过 payload 传回；等事件时传事件名与数据。"
    ),
    params={
        "workflow_id": "工作流 ID",
        "event_type": "可选，事件类型（默认 user_input）",
        "payload": "可选，事件数据（JSON 对象字符串）。回答提问时通常形如 {\"answer\": \"...\"}",
        "timeout": "可选，等待超时秒数（默认 300）",
    },
    risk_level="MEDIUM",
    category="execute",
)
def cortexflow_resume(workflow_id: str, event_type: Optional[str] = None,
                      payload: Optional[str] = None,
                      timeout: Optional[int] = None) -> Dict[str, Any]:
    """唤醒挂起的工作流"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}

    body: Dict[str, Any] = {"type": event_type or "user_input", "payload": {}}
    if payload:
        if isinstance(payload, str):
            import json
            try:
                body["payload"] = json.loads(payload)
            except Exception as e:
                return {"success": False, "error": f"payload 不是合法 JSON：{e}"}
        else:
            body["payload"] = payload

    try:
        wait = int(timeout) if timeout else _RUN_TIMEOUT
    except Exception:
        wait = _RUN_TIMEOUT

    res = _request("POST", f"/workflows/{workflow_id}/resume",
                   json_body=body, timeout=max(wait, 10))
    if not res.get("success"):
        return res
    data = res.get("data") or {}
    # resume 成功时把 result 一并交回；仍可能再次挂起
    return {"success": True, **_summarize_run({**data, "status": data.get("status", "completed")})}


@ToolRegistry.register(
    "cortexflow_cancel",
    description="取消某个正在运行的 cortexflow 工作流。",
    params={"workflow_id": "工作流 ID"},
    risk_level="MEDIUM",
    category="execute",
)
def cortexflow_cancel(workflow_id: str) -> Dict[str, Any]:
    """取消运行中的工作流"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    res = _request("POST", f"/workflows/{workflow_id}/cancel")
    if not res.get("success"):
        return res
    return {"success": True, **(res.get("data") or {})}


# ─────────────────────────────────────────────────────────────
# 写操作：让 AI 创建 / 修改 / 删除 / 回滚工作流
# ─────────────────────────────────────────────────────────────


@ToolRegistry.register(
    "cortexflow_list_examples",
    description=(
        "列出 cortexflow 仓库自带的示例工作流模板（文件名+说明+节点数）。"
        "需要从零创建一个工作流、但不确定 DSL 怎么写时，先看这里有哪些模板可参考。"
    ),
    params={},
    risk_level="LOW",
    category="query",
)
def cortexflow_list_examples() -> Dict[str, Any]:
    """列出示例工作流模板"""
    res = _request("GET", "/examples")
    if not res.get("success"):
        return res
    data = res.get("data") or {}
    return {
        "success": True,
        "count": data.get("count", 0),
        "examples": data.get("examples") or [],
    }


@ToolRegistry.register(
    "cortexflow_get_example",
    description=(
        "读取某个示例工作流的完整 YAML 原文。可以把它当作模板，"
        "改完后用 cortexflow_create_workflow 保存成自己的工作流。"
    ),
    params={"filename": "示例文件名（来自 cortexflow_list_examples，如 basic_flow.yaml）"},
    risk_level="LOW",
    category="query",
)
def cortexflow_get_example(filename: str) -> Dict[str, Any]:
    """读取示例工作流的 YAML 原文"""
    if not filename:
        return {"success": False, "error": "filename 不能为空"}
    res = _request("GET", f"/examples/{filename}")
    if not res.get("success"):
        return res
    data = res.get("data") or {}
    return {
        "success": True,
        "filename": data.get("filename"),
        "content": data.get("content", ""),
        "meta": data.get("meta") or {},
    }


@ToolRegistry.register(
    "cortexflow_validate_workflow",
    description=(
        "只校验工作流定义是否合法，不保存任何东西。"
        "写定义后建议先调本工具确认无误，再 create/update，能提前暴露节点类型、"
        "必填字段、可达性等错误。definition 支持 YAML 或 JSON 文本。"
    ),
    params={"definition": "工作流定义（YAML 或 JSON 文本）"},
    risk_level="LOW",
    category="query",
    core=True,
)
def cortexflow_validate_workflow(definition: str) -> Dict[str, Any]:
    """校验工作流定义（不保存）"""
    defn, err = _parse_definition(definition)
    if err:
        return {"success": False, "error": err}
    result = _validate_definition(defn)
    if result["ok"]:
        nodes = defn.get("nodes") or {}
        return {
            "success": True,
            "valid": True,
            "node_count": len(nodes) if isinstance(nodes, dict) else 0,
            "entry": defn.get("entry"),
        }
    return {
        "success": True,
        "valid": False,
        "errors": result["errors"],
        "hint": "按 errors 修正 definition 后重新校验。",
    }


@ToolRegistry.register(
    "cortexflow_create_workflow",
    description=(
        "在 cortexflow 中新建一个工作流。definition 支持 YAML 或 JSON 文本，"
        "需包含 id/name/entry/nodes（可选 variables/tiers/config 等）。"
        "默认会先校验，校验失败则不会保存并返回具体错误，可修正后重试。"
        "不确定 DSL 结构时先用 cortexflow_get_example 拿模板改。"
    ),
    params={
        "workflow_id": "工作流唯一 ID（英文/数字/下划线，创建后即以此调用）",
        "name": "工作流显示名称",
        "definition": "工作流定义（YAML 或 JSON 文本）",
        "skip_validate": "可选，true 则跳过预校验直接保存（默认 false，不建议）",
    },
    risk_level="HIGH",
    category="execute",
    core=True,
)
def cortexflow_create_workflow(workflow_id: str, name: str, definition: str,
                               skip_validate: Optional[bool] = None) -> Dict[str, Any]:
    """创建新工作流"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    defn, err = _parse_definition(definition)
    if err:
        return {"success": False, "error": err}

    # 让 definition.id 与 workflow_id 保持一致，避免两者不一致导致后续调用混乱
    if defn.get("id") and defn.get("id") != workflow_id:
        return {
            "success": False,
            "error": (
                f"definition.id（{defn['id']!r}）与 workflow_id（{workflow_id!r}）不一致。"
                "请把 definition 里的 id 改成同一个值，或改 workflow_id。"
            ),
        }
    defn["id"] = workflow_id
    defn.setdefault("name", name)

    if not skip_validate:
        check = _validate_definition(defn)
        if not check["ok"]:
            return {
                "success": False,
                "error": _format_validation_error(check["errors"]),
                "validation_errors": check["errors"],
            }

    res = _request("POST", "/workflows",
                   json_body={"id": workflow_id, "name": name, "definition": defn})
    if not res.get("success"):
        return res
    data = res.get("data") or {}
    return {
        "success": True,
        "action": "created",
        "workflow_id": data.get("id", workflow_id),
        "name": data.get("name", name),
        "node_count": len(defn.get("nodes") or {}),
        "next": "可用 cortexflow_run 试跑，或 cortexflow_get_workflow 复核定义。",
    }


@ToolRegistry.register(
    "cortexflow_update_workflow",
    description=(
        "修改一个已存在工作流的定义或名称（不存在时会创建，即 upsert）。"
        "每次保存都会记一个新版本，可用 cortexflow_rollback_workflow 回退。"
        "definition 支持 YAML 或 JSON 文本；默认先校验，校验失败不保存。"
        "只想改名称时可省略 definition。"
    ),
    params={
        "workflow_id": "要修改的工作流 ID",
        "definition": "可选，新的工作流定义（YAML 或 JSON 文本）。不填则只改名称",
        "name": "可选，新的显示名称",
        "message": "可选，本次改动的说明（会记进版本历史）",
        "skip_validate": "可选，true 跳过预校验（默认 false）",
    },
    risk_level="HIGH",
    category="execute",
    core=True,
)
def cortexflow_update_workflow(workflow_id: str, definition: Optional[str] = None,
                               name: Optional[str] = None, message: Optional[str] = None,
                               skip_validate: Optional[bool] = None) -> Dict[str, Any]:
    """修改工作流（upsert）"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    if definition is None and name is None:
        return {"success": False, "error": "definition 与 name 至少要提供一个"}

    body: Dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if message is not None:
        body["message"] = message

    if definition is not None:
        defn, err = _parse_definition(definition)
        if err:
            return {"success": False, "error": err}
        if defn.get("id") and defn.get("id") != workflow_id:
            return {
                "success": False,
                "error": (
                    f"definition.id（{defn['id']!r}）与 workflow_id（{workflow_id!r}）不一致。"
                    "修改已有工作流时，definition.id 必须与目标 id 相同。"
                ),
            }
        defn["id"] = workflow_id
        # 模型往往只改 definition 里的 name，没单独传 name 参数。
        # 若 definition 带了 name 而调用方没显式给，就用 definition 的，
        # 否则用户/模型会以为改名生效了、实际没有。
        if name is None and defn.get("name"):
            body["name"] = defn["name"]
        if not skip_validate:
            check = _validate_definition(defn)
            if not check["ok"]:
                return {
                    "success": False,
                    "error": _format_validation_error(check["errors"]),
                    "validation_errors": check["errors"],
                }
        body["definition"] = defn

    res = _request("PUT", f"/workflows/{workflow_id}", json_body=body)
    if not res.get("success"):
        return res
    data = res.get("data") or {}
    out: Dict[str, Any] = {
        "success": True,
        "action": "updated",
        "workflow_id": data.get("id", workflow_id),
        "name": data.get("name"),
        "version": data.get("version"),
    }
    if definition is not None:
        defn2, _ = _parse_definition(definition)
        if isinstance(defn2, dict):
            out["node_count"] = len(defn2.get("nodes") or {})
    out["next"] = "可用 cortexflow_run 验证改动是否按预期工作。"
    return out


@ToolRegistry.register(
    "cortexflow_delete_workflow",
    description=(
        "删除一个工作流及其全部版本历史。这是不可逆操作（只能靠删除前自己备份定义）。"
        "删除前建议先用 cortexflow_get_workflow 取出定义留存。"
    ),
    params={"workflow_id": "要删除的工作流 ID"},
    risk_level="HIGH",
    category="execute",
)
def cortexflow_delete_workflow(workflow_id: str) -> Dict[str, Any]:
    """删除工作流"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    res = _request("DELETE", f"/workflows/{workflow_id}")
    if not res.get("success"):
        return res
    return {
        "success": True,
        "action": "deleted",
        "workflow_id": workflow_id,
        "note": "该工作流的版本历史一并删除，无法回滚。",
    }


@ToolRegistry.register(
    "cortexflow_list_versions",
    description=(
        "列出某个工作流的版本历史（每次保存定义都会记一版）。"
        "想回退到之前的版本时，先用它拿到 version 号。"
    ),
    params={"workflow_id": "工作流 ID"},
    risk_level="LOW",
    category="query",
)
def cortexflow_list_versions(workflow_id: str) -> Dict[str, Any]:
    """列出工作流版本历史"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    res = _request("GET", f"/workflows/{workflow_id}/versions")
    if not res.get("success"):
        return res
    data = res.get("data") or {}
    return {
        "success": True,
        "workflow_id": workflow_id,
        "count": data.get("count", 0),
        "versions": data.get("versions") or [],
    }


@ToolRegistry.register(
    "cortexflow_rollback_workflow",
    description=(
        "把工作流回退到某个历史版本（回退本身也会记为新版本，不会丢历史）。"
        "改坏了定义、或想撤销上一次修改时用。版本号来自 cortexflow_list_versions。"
    ),
    params={
        "workflow_id": "工作流 ID",
        "version": "要恢复到的版本号（整数）",
    },
    risk_level="HIGH",
    category="execute",
)
def cortexflow_rollback_workflow(workflow_id: str, version: int) -> Dict[str, Any]:
    """回滚工作流到指定版本"""
    if not workflow_id:
        return {"success": False, "error": "workflow_id 不能为空"}
    try:
        ver = int(version)
    except Exception:
        return {"success": False, "error": f"version 必须是整数，收到 {version!r}"}
    res = _request("POST", f"/workflows/{workflow_id}/rollback", json_body={"version": ver})
    if not res.get("success"):
        return res
    data = res.get("data") or {}
    return {
        "success": True,
        "action": "rolled back",
        "workflow_id": workflow_id,
        "restored_from_version": ver,
        "new_version": data.get("version"),
    }
