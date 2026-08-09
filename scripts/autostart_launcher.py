"""开机自启启动器 — 由 LaunchAgent 调用：拉起后端 + 前端桌面窗口

后端: uvicorn api.main:app (8080)
前端: frontend/main.py（Qt 主窗口，内含前端代理 + 桌宠）
"""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _start_backend():
    port = os.environ.get("SERVER_PORT", "8080")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app",
         "--host", "0.0.0.0", "--port", str(port), "--log-level", "info"],
        cwd=PROJECT_ROOT,
        stdout=open(os.path.join(PROJECT_ROOT, "data", "logs", "backend.log"), "a") if os.path.isdir(os.path.join(PROJECT_ROOT, "data", "logs")) else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def _start_frontend():
    main_py = os.path.join(PROJECT_ROOT, "frontend", "main.py")
    if os.path.exists(main_py):
        subprocess.Popen(
            [sys.executable, main_py],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def main():
    _start_backend()
    _start_frontend()


if __name__ == "__main__":
    main()
