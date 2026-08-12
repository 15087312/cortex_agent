"""
一键启动所有模块的脚本

为什么不用 uvicorn.run()：
uvicorn.run() 在 macOS 上即使传 workers=None 也会启动 parent process，
子进程中 SentenceTransformer/transformers 的模型加载行为与主进程不一致，
导致 Embedding 模型初始化卡死（[Errno 60] Operation timed out）。

为什么不用 multiprocessing / workers：
子进程会丢失主进程的 os.environ 设置（HF_HUB_OFFLINE、TRANSFORMERS_OFFLINE），
导致 huggingface hub 发起网络连接请求，国内环境连 huggingface.co 超时。

解法：直接用 subprocess 执行 python -m uvicorn，与终端命令行为完全一致。
"""
import signal
import sys
import os
import subprocess

_memory_scheduler = None


def _graceful_shutdown(signum, frame):
    """优雅退出：停止记忆调度器"""
    global _memory_scheduler
    print("\n正在关闭...")
    if _memory_scheduler:
        _memory_scheduler.stop()
        print("✓ 记忆调度器已停止")
    sys.exit(0)


def main():
    """启动服务"""
    global _memory_scheduler

    import argparse
    parser = argparse.ArgumentParser(description="Humanoid AGI Server")
    parser.add_argument("--debug", action="store_true", help="开启 DEBUG 日志（含 prompt 前 500 字符输出）")
    args = parser.parse_args()

    log_level = "debug" if args.debug else os.environ.get("LOG_LEVEL", "info")
    log_level = log_level.lower()  # uvicorn 只认小写

    # 注册信号处理器
    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    port = int(os.environ.get("SERVER_PORT", "8080"))
    # 默认只绑定本机回环（安全）：局域网/公网访问需显式设置 SERVER_HOST=0.0.0.0
    host = os.environ.get("SERVER_HOST", "127.0.0.1")
    cmd = [
        sys.executable, "-m", "uvicorn",
        "api.main:app",
        "--host", host,
        "--port", str(port),
        "--log-level", log_level,
    ]

    print(f"Starting Humanoid AGI server (log={log_level})...")
    print(f"  → http://{host}:{port}")
    print(f"  → Ctrl+C 停止")

    # 直接 subprocess 调用 uvicorn，与终端命令行为完全一致
    # 不用 uvicorn.run() 的原因是它在 macOS 上会启动额外子进程，
    # 导致 Embedding 模型初始化环境不一致而卡死
    process = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(process.returncode)


if __name__ == "__main__":
    main()
