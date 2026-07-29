"""
API routes — REST + WebSocket endpoints.
"""
import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse

from backend.chat.continuous_thinker import ContinuousThinker
from backend.database.session_repo import get_session_repo
from backend.config.settings import settings
from backend.utils.logger import setup_logger

logger = setup_logger("api_routes")

router = APIRouter()

# Global thinker instance
_thinker: Optional[ContinuousThinker] = None


def get_thinker() -> ContinuousThinker:
    global _thinker
    if _thinker is None:
        _thinker = ContinuousThinker()
    return _thinker


# ── REST Endpoints ──

@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/api/sessions")
async def create_session():
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    repo = get_session_repo()
    repo.create_session(session_id)
    return {"session_id": session_id}


@router.get("/api/sessions")
async def list_sessions(limit: int = Query(50, ge=1, le=200)):
    repo = get_session_repo()
    sessions = repo.get_all_sessions(limit=limit)
    return {"sessions": sessions}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    repo = get_session_repo()
    messages = repo.get_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    repo = get_session_repo()
    deleted = repo.delete_session(session_id)
    if deleted:
        thinker = get_thinker()
        thinker.get_blackboard().clear_session(session_id)
        return {"status": "deleted"}
    return JSONResponse(status_code=404, content={"error": "Session not found"})


@router.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str, limit: int = Query(100, ge=1, le=500)):
    repo = get_session_repo()
    messages = repo.get_messages(session_id, limit=limit)
    return {"session_id": session_id, "messages": messages}


@router.put("/api/config/{key}")
async def update_config(key: str, body: dict):
    if not settings.is_modifiable(key):
        return JSONResponse(
            status_code=400,
            content={"error": f"Field '{key}' is not modifiable at runtime"},
        )
    value = body.get("value")
    if value is None:
        return JSONResponse(status_code=400, content={"error": "Missing 'value' field"})
    setattr(settings, key, value)
    return {"key": key, "value": value}


# ── WebSocket Endpoint ──

@router.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info(f"WebSocket connected: session={session_id[:12]}...")

    thinker = get_thinker()
    repo = get_session_repo()

    # Ensure session exists
    repo.create_session(session_id)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "content": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")
            content = msg.get("content", "")

            if msg_type == "message" and content:
                # Save user message
                repo.save_message(session_id, "user", content)

                # Create queue for streaming
                queue = asyncio.Queue()

                # Run thinking in background
                async def run_think():
                    await thinker.think(session_id, content, queue)

                think_task = asyncio.create_task(run_think())

                # Stream tokens to client
                try:
                    while True:
                        try:
                            token_msg = await asyncio.wait_for(queue.get(), timeout=300)
                        except asyncio.TimeoutError:
                            await websocket.send_json({"type": "error", "content": "Thinking timeout"})
                            break

                        await websocket.send_json(token_msg)

                        if token_msg.get("type") in ("done", "error"):
                            break
                except WebSocketDisconnect:
                    think_task.cancel()
                    break

                # Save assistant response after streaming completes
                if think_task.done() and not think_task.cancelled():
                    blackboard = thinker.get_blackboard()
                    messages = blackboard.get_messages(session_id)
                    for m in reversed(messages):
                        if m.get("role") == "assistant":
                            repo.save_message(session_id, "assistant", m["content"])
                            break

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: session={session_id[:12]}...")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
