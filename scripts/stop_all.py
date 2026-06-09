"""
一键停止所有模块的脚本（跨平台）
"""
import os
import signal
import sys
import subprocess
import time

IS_WIN = sys.platform == "win32"


def _find_pids_on_port(port: int) -> list:
    """查找占用指定端口的进程 PID 列表"""
    pids = []
    try:
        if IS_WIN:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pids.append(parts[-1])
        else:
            result = subprocess.run(
                ["lsof", f"-ti:{port}"], capture_output=True, text=True
            )
            if result.stdout:
                pids = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
    except Exception as e:
        print(f"Error finding PIDs on port {port}: {e}")
    return pids


def _kill_pid(pid: str, force: bool = False) -> bool:
    """杀死指定 PID 的进程（跨平台）"""
    try:
        if IS_WIN:
            cmd = ["taskkill", "/F", "/PID", pid] if force else ["taskkill", "/PID", pid]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.returncode == 0
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(int(pid), sig)
            return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def _pid_alive(pid: str) -> bool:
    """检查进程是否还活着"""
    try:
        if IS_WIN:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True
            )
            return pid in result.stdout
        else:
            os.kill(int(pid), 0)
            return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def main():
    """停止服务"""
    port = int(os.environ.get("SERVER_PORT", "8080"))
    print(f"Stopping Humanoid AGI server (port {port})...")

    pids = _find_pids_on_port(port)
    if not pids:
        print(f"Port {port} is not in use")
        print("Server stopped.")
        return

    # First pass: graceful shutdown
    for pid in pids:
        if _kill_pid(pid, force=False):
            print(f"Sent graceful stop to process {pid}")
        else:
            print(f"Failed to stop process {pid}")

    # Wait up to 5 seconds
    time.sleep(5)

    # Second pass: force kill any still alive
    for pid in pids:
        if _pid_alive(pid):
            if _kill_pid(pid, force=True):
                print(f"Force-killed process {pid}")
            else:
                print(f"Failed to force-kill process {pid}")

    print("Server stopped.")


if __name__ == "__main__":
    main()
