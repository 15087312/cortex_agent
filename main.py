"""Cortex Agent — FastAPI backend entry point (api.main 单一入口)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    import os
    import uvicorn
    # 默认只绑定本机回环（安全）：局域网/公网访问需显式设置 SERVER_HOST=0.0.0.0
    host = os.environ.get("SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("SERVER_PORT", "8080"))
    uvicorn.run("api.main:app", host=host, port=port, log_level="info")
