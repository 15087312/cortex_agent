"""
一键停止所有模块的脚本
"""
import os
import signal
import sys
import subprocess
import time


def main():
    """停止服务"""
    print("Stopping Humanoid AGI server...")

    # 查找并杀死占用 8080 端口的进程
    try:
        result = subprocess.run(
            ["lsof", "-ti:8080"],
            capture_output=True,
            text=True
        )
        if result.stdout:
            pids = result.stdout.strip().split('\n')
            # First pass: send SIGTERM for graceful shutdown
            for pid in pids:
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        print(f"Sent SIGTERM to process {pid} on port 8080")
                    except (ProcessLookupError, ValueError):
                        pass

            # Wait up to 5 seconds for processes to exit
            time.sleep(5)

            # Second pass: SIGKILL any still alive
            for pid in pids:
                if pid:
                    try:
                        os.kill(int(pid), 0)  # Check if still alive
                        os.kill(int(pid), signal.SIGKILL)
                        print(f"Force-killed process {pid} on port 8080")
                    except (ProcessLookupError, ValueError):
                        pass
        else:
            print("Port 8080 is not in use")
    except Exception as e:
        print(f"Error: {e}")

    print("Server stopped.")


if __name__ == "__main__":
    main()