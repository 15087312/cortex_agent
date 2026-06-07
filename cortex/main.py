"""
cortex — 一键启动 Humanoid AGI 后端 + CLI 终端

用法:
  cortex                    # 启动后端 + TUI
  cortex --port 9000        # 指定端口
  cortex --no-tui           # 只启动后端（无 TUI）
  cortex --api-url http://x:8080  # 连接已有后端
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def _wait_for_server(url: str, timeout: int = 30) -> bool:
    """等待后端服务就绪"""
    import urllib.request
    import urllib.error

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ConnectionError):
            pass
        time.sleep(0.5)
    return False


def _port_in_use(port: int) -> bool:
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _get_project_root() -> Path:
    """获取项目根目录（包含 api/main.py 的目录）"""
    env_root = os.environ.get("CORTEX_ROOT")
    if env_root:
        return Path(env_root)
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "api" / "main.py").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent


def parse_args():
    parser = argparse.ArgumentParser(
        prog="cortex",
        description="Cortex Agent — 一键启动 Humanoid AGI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  cortex                         # 启动后端 + TUI
  cortex --port 9000             # 指定后端端口
  cortex --no-tui                # 只启动后端（API 服务模式）
  cortex --api-url http://x:8080 # 连接已有的远程后端
  cortex --api-key my-key        # 指定 API 密钥
        """,
    )
    parser.add_argument("--port", "-p", type=int,
                        default=int(os.environ.get("SERVER_PORT", "8080")),
                        help="后端监听端口 (默认: 8080)")
    parser.add_argument("--host", default=os.environ.get("SERVER_HOST", "127.0.0.1"),
                        help="后端监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--api-url", default=None,
                        help="连接已有的后端（跳过自动启动）")
    parser.add_argument("--api-key", default=os.environ.get("SIMPLE_API_KEY", ""),
                        help="API 认证密钥")
    parser.add_argument("--model", default=None, help="指定主模型")
    parser.add_argument("--no-tui", action="store_true",
                        help="只启动后端，不启动 TUI")
    parser.add_argument("--workers", type=int,
                        default=int(os.environ.get("MAX_WORKERS", "1")),
                        help="后端 worker 数 (默认: 1)")
    return parser.parse_args()


def start_backend(args) -> subprocess.Popen:
    """启动后端 uvicorn 子进程"""
    project_root = _get_project_root()
    env = os.environ.copy()
    env["SERVER_PORT"] = str(args.port)
    env["SERVER_HOST"] = args.host
    env["MAX_WORKERS"] = str(args.workers)
    if args.api_key:
        env["SIMPLE_API_KEY"] = args.api_key

    cmd = [
        sys.executable, "-m", "uvicorn", "api.main:app",
        "--host", args.host,
        "--port", str(args.port),
        "--workers", str(args.workers),
        "--log-level", "info",
    ]
    return subprocess.Popen(cmd, cwd=str(project_root), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def launch_tui(args):
    """启动 TUI（替换当前进程）"""
    api_url = args.api_url or f"http://{args.host}:{args.port}"
    cmd = [sys.executable, "-m", "cli_tui.main", "--api-url", api_url]
    if args.api_key:
        cmd.extend(["--api-key", args.api_key])
    if args.model:
        cmd.extend(["--model", args.model])
    os.execvp(sys.executable, cmd)


def main():
    args = parse_args()

    # ── 模式 1: 连接已有后端 ──
    if args.api_url:
        if args.no_tui:
            print(f"连接到: {args.api_url}  (Ctrl+C 退出)")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            launch_tui(args)
        return

    # ── 模式 2: 自动启动后端 ──
    api_url = f"http://{args.host}:{args.port}"

    # 端口被占用 → 尝试复用
    if _port_in_use(args.port):
        if _wait_for_server(api_url, timeout=3):
            print(f"✓ 检测到已有后端: {api_url}")
            args.api_url = api_url
            if not args.no_tui:
                launch_tui(args)
            return
        else:
            print(f"✗ 端口 {args.port} 被占用但服务不可用")
            sys.exit(1)

    # 启动后端
    print(f"🚀 启动 Cortex Agent (:{args.port})...")
    backend_proc = start_backend(args)

    # 清理函数
    def cleanup(signum=None, frame=None):
        print("\n⏹ 停止后端...")
        backend_proc.terminate()
        try:
            backend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_proc.kill()
        print("✓ 已停止")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 等待就绪
    print("⏳ 等待后端就绪...")
    if _wait_for_server(api_url, timeout=30):
        print(f"✓ 后端就绪: {api_url}")
    else:
        print("✗ 后端启动超时")
        backend_proc.terminate()
        sys.exit(1)

    if args.no_tui:
        print(f"\n📡 API: {api_url}")
        print(f"   健康检查: {api_url}/health")
        print(f"   指标:     {api_url}/metrics")
        print(f"   Ctrl+C 停止\n")
        try:
            backend_proc.wait()
        except KeyboardInterrupt:
            cleanup()
    else:
        launch_tui(args)


if __name__ == "__main__":
    main()
