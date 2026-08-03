"""
API routes — REST + WebSocket endpoints.
"""
import asyncio
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse, JSONResponse

from backend.chat.continuous_thinker import ContinuousThinker
from backend.database.session_repo import get_session_repo
from backend.config.autostart import set_autostart
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


@router.get("/config")
async def get_config():
    """Returns all current settings as a flat dict (proxy-compatible, no /api prefix)."""
    return settings.model_dump()


@router.get("/api/config")
async def get_config_api():
    """Returns all current settings (production-compatible, with /api prefix)."""
    return settings.model_dump()


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


def _apply_config_update(key: str, body: dict) -> JSONResponse:
    """Shared config-update logic for both /config/{key} and /api/config/{key}."""
    if not settings.is_modifiable(key):
        return JSONResponse(
            status_code=400,
            content={"error": f"Field '{key}' is not modifiable at runtime"},
        )
    value = body.get("value")
    if value is None:
        return JSONResponse(status_code=400, content={"error": "Missing 'value' field"})
    setattr(settings, key, value)
    settings.save_overrides()

    # Side-effect handlers for specific config keys
    if key == "launch_at_startup":
        set_autostart(bool(value))

    return JSONResponse(status_code=200, content={"key": key, "value": value})


@router.put("/config/{key}")
async def update_config(key: str, body: dict):
    """Proxy-compatible route (Vite proxy strips /api prefix)."""
    return _apply_config_update(key, body)


@router.put("/api/config/{key}")
async def update_config_api(key: str, body: dict):
    """Production-compatible route (direct access, no proxy)."""
    return _apply_config_update(key, body)


# ── Gallery Endpoints ──

GALLERY_DIR = Path(__file__).resolve().parents[2] / "gallery"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def _gallery_root() -> Path:
    """Resolve gallery directory from config or fallback."""
    if settings.storage_path and settings.storage_path.strip():
        p = Path(settings.storage_path) / "gallery"
        p.mkdir(parents=True, exist_ok=True)
        return p
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    return GALLERY_DIR


@router.get("/api/gallery/images")
async def list_gallery_images():
    """List all images in the gallery directory."""
    root = _gallery_root()
    images = []
    try:
        for entry in sorted(root.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
            if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
                st = entry.stat()
                images.append({
                    "name": entry.name,
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                })
    except FileNotFoundError:
        pass
    return {"images": images}


@router.post("/api/gallery/upload")
async def upload_gallery_image(file: UploadFile = File(...)):
    """Upload an image to the gallery directory. Returns the saved filename."""
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "No filename provided"})

    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported file type: {ext}"},
        )

    root = _gallery_root()
    # Avoid overwriting: append counter if name exists
    base = file.filename
    dest = root / base
    counter = 1
    while dest.exists():
        stem = Path(file.filename).stem
        dest = root / f"{stem}_{counter}{ext}"
        counter += 1

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"name": dest.name, "path": f"/api/gallery/image/{dest.name}"}


@router.get("/api/gallery/image/{name}")
async def serve_gallery_image(name: str):
    """Serve a gallery image file."""
    root = _gallery_root()
    path = root / name
    if not path.is_file():
        return JSONResponse(status_code=404, content={"error": "Image not found"})
    return FileResponse(str(path))


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

            if msg_type == "input" and content:
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
