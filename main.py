"""Cortex Agent — FastAPI backend entry point (api.main 单一入口)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    import os
    import uvicorn
    # 默认只绑定本机回环（安全）：局域网/公网访问需显式设置 SERVER_HOST=0.0.0.0
    host = os.environ.get("SERVER_HOST", "127.0.0.1")
    preferred = int(os.environ.get("SERVER_PORT", "8080"))
    # 端口被占时自动回退到空闲端口，并把实际端口写入发现文件
    from utils.port_discovery import pick_free_port, save_backend_port
    port = pick_free_port(preferred)
    save_backend_port(port)
    if port != preferred:
        print(f"[Cortex] 端口 {preferred} 被占用，已自动改用端口 {port}", flush=True)
    uvicorn.run("api.main:app", host=host, port=port, log_level="info")
