"""常驻屏幕采集进程 — 全项目唯一截图入口

背景：macOS 的 screencapture 命令每次调用都会触发系统「屏幕录制权限」确认。
原来 6+ 处（OCR/视觉/UI 检测/两个 MCP server）各自独立调 screencapture，
未授权时每次调用都弹窗。本 daemon 收敛为「一个进程持续运行 + 帧缓存」，
所有消费方通过 Unix socket 取帧，只在 daemon 内调用一次截图。

- 授权一次（daemon 归属到启动它的 app）后不再弹窗
- 串行处理请求，帧缓存避免 1s 内重复截图
- 独立脚本，自包含截图实现（不依赖项目其他模块）

用法（由 utils.screen_capture.init_screen_permission 自动拉起）:
    python infra/screen_capture_daemon.py
"""
import base64
import io
import json
import os
import socket
import stat
import sys
import threading
import time
from utils.logger import setup_logger


logger = setup_logger(__name__)
SOCKET_PATH = os.environ.get(
    "CORTEX_SCREEN_CAPTURE_SOCKET",
    "/tmp/cortex_screen_capture.sock",
)
# 截图约 1s/次；缓存 3s 可覆盖大多数并发请求，避免每个请求都重新截屏
CACHE_TTL_SECONDS = float(os.environ.get("CORTEX_SCREEN_CAPTURE_TTL", "3.0"))
# 并发请求排队上限：另一个连接正在截图时，最多等这么久；超时直接返回上一帧。
# 客户端超时 8s，必须远小于它，否则一次慢截图会让所有消费者一起超时并各自回退本地截图。
STALE_WAIT_SECONDS = float(os.environ.get("CORTEX_SCREEN_CAPTURE_WAIT", "2.0"))
LOG_PREFIX = "[screen_capture_daemon]"


def _log(msg: str):
    sys.stderr.write(f"{LOG_PREFIX} {msg}\n")
    sys.stderr.flush()


def _screencapture_image():
    """截取全屏，返回 PIL RGB Image 或 None（独立实现，避免依赖项目模块）"""
    try:
        import subprocess
        import tempfile

        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        try:
            result = subprocess.run(
                ["screencapture", "-x", "-C", "-t", "png", tmp_path],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0 or not os.path.exists(tmp_path):
                return None
            with open(tmp_path, "rb") as fh:
                img = Image.open(io.BytesIO(fh.read()))
                img.load()
                return img.convert("RGB")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError as _swallowed_err:
                logger.debug(f"[_screencapture_image] ignored OSError: {_swallowed_err}")
    except Exception as e:
        _log(f"截图失败: {e}")
        return None


def _crop_resize(img, max_width, region):
    """按 region 裁剪、max_width 缩放，返回处理后的 Image"""
    if region and len(region) == 4:
        x, y, w, h = region
        img = img.crop((int(x), int(y), int(x) + int(w), int(y) + int(h)))
    if max_width:
        w, h = img.size
        if w > max_width:
            ratio = max_width / w
            img = img.resize((max_width, int(h * ratio)))
    return img


def _to_png_b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class ScreenCaptureDaemon:
    """串行处理 Unix socket 请求：ping / frame（带帧缓存）"""

    def __init__(self, socket_path=SOCKET_PATH):
        self._socket_path = socket_path
        self._cached_img = None
        self._cached_ts = 0.0
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)  # 保护缓存 + 截图状态
        self._capturing = False
        self._shutdown = threading.Event()

    # ── 帧缓存 ──

    def _get_fresh_frame(self, wait_budget: float = STALE_WAIT_SECONDS):
        """返回帧；必要时截图。

        并发场景：若另一个连接正在截图，最多等待 wait_budget 秒，超时直接返回
        上一帧缓存（可能略旧），避免所有消费者排队到客户端 8s 超时后各自回退
        本地 screencapture（会触发权限弹窗并进一步争抢截图资源）。
        """
        with self._cv:
            if self._cached_img is not None and (time.time() - self._cached_ts) < CACHE_TTL_SECONDS:
                return self._cached_img
            if self._capturing:
                self._cv.wait(timeout=wait_budget)
                return self._cached_img
            self._capturing = True

        img = None
        try:
            img = _screencapture_image()
        finally:
            with self._cv:
                if img is not None:
                    self._cached_img = img
                    self._cached_ts = time.time()
                self._capturing = False
                self._cv.notify_all()
        return img

    # ── 请求处理 ──

    def handle_request(self, req: dict) -> dict:
        method = req.get("method", "")
        req_id = req.get("id")

        if method == "ping":
            return {"id": req_id, "result": {"ok": True, "pid": os.getpid()}}

        if method == "frame":
            params = req.get("params") or {}
            img = self._get_fresh_frame()
            if img is None:
                return {"id": req_id, "error": {"code": -32001, "message": "截图失败或权限未授予"}}
            try:
                processed = _crop_resize(
                    img,
                    max_width=params.get("max_width"),
                    region=params.get("region"),
                )
                return {
                    "id": req_id,
                    "result": {
                        "png": _to_png_b64(processed),
                        "ts": self._cached_ts,
                        "width": processed.size[0],
                        "height": processed.size[1],
                    },
                }
            except Exception as e:
                return {"id": req_id, "error": {"code": -32002, "message": f"帧处理失败: {e}"}}

        return {"id": req_id, "error": {"code": -32601, "message": f"未知方法: {method}"}}

    # ── socket 服务 ──

    def _serve_connection(self, conn):
        try:
            conn.settimeout(30.0)
            with conn.makefile("rwb") as f:
                while not self._shutdown.is_set():
                    line = f.readline()
                    if not line:
                        break
                    try:
                        req = json.loads(line)
                        resp = self.handle_request(req)
                    except Exception as e:
                        resp = {"id": None, "error": {"code": -32700, "message": f"请求解析失败: {e}"}}
                    f.write((json.dumps(resp) + "\n").encode())
                    f.flush()
        except Exception as e:
            _log(f"连接处理异常: {e}")
        finally:
            try:
                conn.close()
            except OSError as _swallowed_err:
                logger.debug(f"[_serve_connection] ignored OSError: {_swallowed_err}")

    def _bind_server(self):
        """绑定 Unix socket。处理多进程竞态：
        - bind 失败且已有 daemon 在服务（socket 可连）→ 本进程退出，不误杀他人
        - bind 失败且是残留 stale socket（不可连）→ 清理后重试一次
        探测时给其他 daemon 留出「bind 后 listen 前」的窗口（重试 3 次），
        避免把正在初始化的 daemon 误判为 stale 而抢占其 socket。
        返回 server 或 None（None 表示应退出，不进行服务）。
        """
        try:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(self._socket_path)
            os.chmod(self._socket_path, 0o600)
            server.listen(8)
            return server
        except OSError:
            # 探测 socket：能连上说明已有 daemon 在服务（可能还在 bind/listen 途中，多探几次）
            for _ in range(3):
                try:
                    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    probe.settimeout(1)
                    probe.connect(self._socket_path)
                    probe.close()
                    _log("检测到已有 daemon 在服务，本进程退出")
                    return None
                except Exception:
                    time.sleep(0.3)
            # 仍连不上 → stale socket → 清理后重试一次
            try:
                if os.path.exists(self._socket_path):
                    os.unlink(self._socket_path)
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(self._socket_path)
                os.chmod(self._socket_path, 0o600)
                server.listen(8)
                return server
            except Exception as e:
                _log(f"socket 绑定失败: {e}")
                return None

    def run(self):
        """启动 Unix socket 服务（阻塞，直到收到 shutdown）"""
        server = self._bind_server()
        if server is None:
            return
        server.settimeout(1.0)
        _log(f"监听 {self._socket_path}")

        # 独立线程监听 stdin（stdin EOF → 退出），兼容父进程退出场景。
        # /dev/null（手动后台运行 & 时）不算 EOF——避免被立即误杀；
        # 拿不到真实 fd（如 StringIO）时也不监听。
        def _watch_stdin():
            try:
                if not sys.stdin.isatty():
                    try:
                        mode = os.fstat(sys.stdin.fileno())
                    except Exception:
                        return  # 无法判断 stdin 类型（如 StringIO）→ 不监听
                    if stat.S_ISCHR(mode.st_mode):
                        return  # /dev/null：不监听
                sys.stdin.read()
            except Exception as _swallowed_err:
                logger.debug(f"[_watch_stdin] ignored Exception: {_swallowed_err}")
            self._shutdown.set()

        threading.Thread(target=_watch_stdin, daemon=True).start()

        try:
            while not self._shutdown.is_set():
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._serve_connection, args=(conn,), daemon=True).start()
        except Exception as e:
            _log(f"主循环异常（不退出，继续服务）: {e}")
        finally:
            server.close()
            # 仅当本进程成功绑定时清理自己占用的 socket；若本进程未 bind 成功，
            # 绝不 unlink（可能误杀正在服务的另一个 daemon 的 socket）
            try:
                if os.path.exists(self._socket_path):
                    os.unlink(self._socket_path)
            except OSError as _swallowed_err:
                logger.debug(f"[_watch_stdin] ignored OSError: {_swallowed_err}")
            _log("已退出")


def main():
    # 防残留: 绑定后端进程（env > ~/.cortex/backend_pid.json，等 30s 兼容先后端后卫星）。
    # 本 daemon 历史上常成为孤儿（父进程退出但无人 terminate），绑定后端后自动退出。
    # daemon 未注册屏幕录制权限时仍可绑定——权限归属由启动方继承。
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from infra.watchdog import bind_backend
        bind_backend()
    except Exception as _swallowed_err:
        logger.debug(f"[main] ignored Exception: {_swallowed_err}")
    daemon = ScreenCaptureDaemon()
    daemon.run()


if __name__ == "__main__":  # pragma: no cover — 入口守卫，由 subprocess/启动脚本触发
    main()
