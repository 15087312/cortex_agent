"""后端启动器 — PyInstaller 打包入口（AI_Backend.exe）

打包版里 uvicorn 以字符串 "api.main:app" 导入，模块在 PYZ 归档中可直接解析；
chdir 到 exe 所在目录，保证 data/ 等相对路径落在应用目录内。
"""
import os
import sys

if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn

if __name__ == "__main__":
    # 端口被占时自动回退到空闲端口，并把实际端口写入发现文件
    from utils.port_discovery import pick_free_port, save_backend_port
    preferred = int(os.environ.get("SERVER_PORT", "8080"))
    port = pick_free_port(preferred)
    save_backend_port(port)
    if port != preferred:
        print(f"[Cortex] 端口 {preferred} 被占用，已自动改用端口 {port}", flush=True)
    uvicorn.run("api.main:app", host="127.0.0.1", port=port, log_level="info")
