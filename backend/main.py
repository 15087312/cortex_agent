"""
Cortex — FastAPI application entry point.
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config.settings import settings
from backend.database.connection import get_db_manager
from backend.utils.logger import setup_logger

logger = setup_logger("main")

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # Startup
    logger.info("Cortex starting up...")

    # Ensure data directory exists
    os.makedirs(PROJECT_ROOT / "data", exist_ok=True)
    os.makedirs(PROJECT_ROOT / "logs", exist_ok=True)

    # Initialize database
    db = get_db_manager()
    db.initialize(settings.MEMORY_DB_PATH)
    logger.info("Database initialized")

    # Preload embedding engine (optional, takes time)
    try:
        from backend.memory.embedding import EmbeddingEngine
        eng = EmbeddingEngine.get_instance()
        eng._load_model()
        logger.info("Embedding engine loaded")
    except Exception as e:
        logger.warning(f"Embedding engine preload failed (will lazy-load): {e}")

    logger.info("Cortex ready")

    yield

    # Shutdown
    logger.info("Cortex shutting down...")
    db.close()
    logger.info("Database closed")


app = FastAPI(
    title="Cortex",
    description="Simplified chat-only AI with memory system",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
from backend.api.routes import router
app.include_router(router)

# Serve frontend static files (if built)
frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


def main():
    """CLI entry point."""
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
