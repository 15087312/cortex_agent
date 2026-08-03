"""Mock Backend — 观察前端实际发送了哪些消息/请求

替代真实后端 (:8080)，完整记录：
  1. 前端发来的每条 WebSocket 消息（JSON + 时间戳 + 溯源字段）
  2. 前端发来的每个 HTTP 请求（method/path/query/headers/body）

用法（先停掉真实后端）：
  python tools/mock_backend.py            # uvicorn 启动在 :8080
  MOCK_BUSY=1 python tools/mock_backend.py  # 每条 input 先回 busy ack（观察前端是否/如何重发）

日志同时输出到控制台和 logs/mock_backend.log，每条都带毫秒级时间戳，
用 trace_id/trace_seq 溯源字段把 WS 消息与触发它的 HTTP/页面动作关联起来。
"""
import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "mock_backend.log"
LOG_PATH.parent.mkdir(exist_ok=True)

_logf = open(LOG_PATH, "a", encoding="utf-8")
MOCK_BUSY = os.environ.get("MOCK_BUSY", "") != ""


def log(*parts):
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] " + " ".join(str(p) for p in parts)
    print(line, flush=True)
    _logf.write(line + "\n")
    _logf.flush()


app = FastAPI(title="Mock Backend (前端流量观测)")

_sessions = {}
_seq = 0


def _new_session():
    global _seq
    _seq += 1
    return f"mock_{_seq}_{uuid.uuid4().hex[:6]}"


@app.middleware("http")
async def trace_http(request: Request, call_next):
    body = b""
    try:
        body = await request.body()
    except Exception:
        pass
    hdrs = dict(request.headers)
    log(
        "HTTP",
        request.method,
        f"{request.url.path}?{request.url.query}",
        "| trace=", hdrs.get("x-trace-id", "-"),
        "seq=", hdrs.get("x-request-seq", "-"),
        "api_key=", "yes" if hdrs.get("x-api-key") else "no",
    )
    if body and request.method in ("POST", "PUT", "DELETE"):
        log("   BODY:", body.decode("utf-8", "replace")[:800])
    try:
        response = await call_next(request)
    except Exception as e:  # noqa: BLE001
        log("   EXC:", repr(e))
        return JSONResponse({"success": False, "error": {"message": str(e)}}, status_code=500)
    return response


# ── HTTP：chat 页实际用到的接口 ────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stream/sessions")
async def list_sessions():
    data = [
        {
            "session_id": sid,
            "title": s.get("title", sid[:12]),
            "last_active": s.get("last_active", ""),
            "message_count": s.get("message_count", 0),
        }
        for sid, s in _sessions.items()
    ]
    log("MOCK /stream/sessions ->", len(data), "条")
    return {"success": True, "data": data}


@app.post("/stream/session")
async def create_session(req: Request):
    sid = _new_session()
    _sessions[sid] = {"title": "新会话", "last_active": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "message_count": 0, "messages": []}
    log("MOCK create_session ->", sid)
    return {"success": True, "data": {"session_id": sid}}


@app.get("/management/sessions/{sid}/dialog")
async def session_dialog(sid: str, limit: int = 100):
    s = _sessions.get(sid, {})
    dialog = s.get("messages", [])[-int(limit):]
    log("MOCK dialog", sid, "->", len(dialog), "条")
    return {"success": True, "data": {"dialog": dialog}}


@app.get("/management/thinking")
async def thinking_status():
    return {"success": True, "data": {"models": {"large": True, "supervisor": True, "expert": True}}}


# ── 兜底：其他页面接口返回空成功，避免一堆 404 toast ─────────────────────────


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all(full_path: str, request: Request):
    log("MOCK (catch-all)", request.method, full_path)
    return {"success": True, "data": {}}


# ── WebSocket：记录前端每条消息并按脚本回复 ──────────────────────────────────


@app.websocket("/stream/ws/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    log("WS CONNECT", session_id, "| peer:", websocket.client)

    await websocket.send_json(_env("ack", "session_ready", "WebSocket 会话已建立", "system"))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "input", "content": raw}

            log(
                "WS RECV",
                session_id,
                "| type=", msg.get("type"),
                "trace=", msg.get("trace_id", "-"),
                "seq=", msg.get("trace_seq", "-"),
                "content=", (msg.get("content") or "")[:60],
                "other=", {k: v for k, v in msg.items() if k not in ("type", "content", "trace_id", "trace_seq")},
            )

            mtype = msg.get("type", "input")

            if mtype == "input":
                content = msg.get("content", "")
                s = _sessions.setdefault(session_id, {"messages": [], "title": content[:20]})
                s["messages"].append({"role": "user", "content": content})
                s["message_count"] = len(s["messages"])
                s["last_active"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _sessions[session_id] = s
                await _respond(websocket, session_id, content, trace=msg.get("trace_id", "-"))
            elif mtype == "stop":
                log("WS STOP", session_id)
                await websocket.send_json(_env("done", "stopped", "会话已停止", "system"))
            elif mtype == "ping":
                await websocket.send_json(_env("ack", "pong", "pong", "system"))
            elif mtype == "security_response":
                log("WS 审批响应:", session_id,
                    "request_id=", msg.get("request_id", "-"),
                    "approved=", msg.get("approved"),
                    "reason=", msg.get("reason", ""))
            else:
                log("WS UNKNOWN TYPE:", mtype)
    except WebSocketDisconnect:
        log("WS DISCONNECT", session_id)
    except Exception as e:  # noqa: BLE001
        log("WS EXC:", session_id, repr(e))


def _env(msg_type: str, event: str, content: str, role: str, data=None):
    return {
        "type": msg_type,
        "event": event,
        "session_id": "mock_ws",
        "role": role,
        "content": content,
        "data": data or {},
        "timestamp": time.time(),
    }


async def _respond(ws: WebSocket, session_id: str, content: str, trace: str):
    """按真实 chat_gateway(agent) 的事件顺序回复，便于观察前端渲染与重复触发。"""
    if MOCK_BUSY:
        log("  -> MOCK_BUSY ack 返回 busy，观察前端是否重发")
        await ws.send_json(_env("ack", "busy", "会话正在处理中，请稍后", "system"))
        return

    await ws.send_json(_env("ack", "received", "已接收请求，开始处理", "system"))
    await asyncio.sleep(0.3)

    # 模拟三层身份各自的 thinking_step，验证前端按身份渲染不同头像/名字
    steps = [
        ("large", "large_primary", "总指挥", "🧠 [总指挥] [R1] 我来拆解这个问题：先让主管规划。"),
        ("supervisor", "supervisor_code_001", "代码主管", "📊 [代码主管] [R1] 收到，拆分为两个子任务，委托给专家。"),
        ("expert", "expert_reviewer_001", "审查专家", "🔧 [审查专家→代码主管] [R1] 审查完成，实现思路可行，建议补充边界处理。"),
        ("supervisor", "supervisor_code_001", "代码主管", "📊 [代码主管] [R2] 已汇总专家结论，汇报给总指挥。"),
    ]
    for tier, model_id, identity, text in steps:
        await ws.send_json(_env("thinking", "thinking_step", text, tier, {
            "dialog_tier": tier,
            "identity_name": identity,
            "model_id": model_id,
            "payload": {"content": {"content": text, "model_id": model_id}},
        }))
        await asyncio.sleep(0.35)

    # 模拟安全审批：专家要执行 run_command，需要用户批准（验证前端审批横幅）
    await ws.send_json(_env("thinking", "thinking_step",
        "[安全审查] run_command 等待用户审批 — 调用者: expert_implementer_001\n参数: command=npm install",
        "thinking", {
            "event_type": "security",
            "action": "等待用户审批",
            "target": "run_command",
            "stage_event": {
                "event_type": "security",
                "action": "等待用户审批",
                "target": "run_command",
                "payload": {"request_id": "mock_approval_001", "detail": "command=npm install"},
            },
            "payload": {"request_id": "mock_approval_001", "detail": "command=npm install"},
        }))
    await asyncio.sleep(0.5)
    log("  -> 已模拟安全审批请求 (request_id=mock_approval_001)，等待前端批准")

    # 模拟 ask_user_intent：模型问用户一个问题（带选项）
    await ws.send_json(_env("thinking", "thinking_step",
        "[提问] 模型需要你确认：继续用哪种技术栈？", "thinking", {
            "event_type": "security",
            "action": "user_intent_request",
            "target": "user_intent_request",
            "stage_event": {
                "event_type": "security",
                "action": "user_intent_request",
                "target": "user_intent_request",
                "payload": {"request_id": "mock_intent_001", "question": "继续用哪种技术栈？", "options": ["Vue3+Vite", "React+Vite", "纯HTML/CSS/JS"]},
            },
            "payload": {"request_id": "mock_intent_001", "question": "继续用哪种技术栈？", "options": ["Vue3+Vite", "React+Vite", "纯HTML/CSS/JS"]},
        }))
    await asyncio.sleep(0.5)
    log("  -> 已模拟 ask_user_intent (request_id=mock_intent_001)，等待前端选择")

    reply = f"【MOCK:{trace}】你说了「{content}」，这是第 {_sessions[session_id]['message_count']} 条消息。"
    await ws.send_json(_env("message", "assistant_message", reply, "large", {
        "identity_name": "总指挥", "model_id": "large_primary", "dialog_tier": "large",
    }))
    _sessions[session_id]["messages"].append({"role": "assistant", "content": reply})
    await ws.send_json(_env("done", "done", "处理完成", "system"))
    log("  -> 已回复完整流程 (ack/thinking/approval/message/done)")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
