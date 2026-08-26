import http.server
import urllib.request
import urllib.error
import os
import json
import sys
import socketserver

# 防残留: 若启动本服务的 cortex 父进程被强杀，自动退出避免孤儿进程
try:
    from cortex.watchdog import enable as _enable_orphan_watchdog
    _enable_orphan_watchdog()
except Exception:
    pass

BACKEND_URL = "http://localhost:8080"
# 打包版（PyInstaller）：依赖解压在 sys._MEIPASS，frontend/dist 位于 _MEIPASS/frontend/dist；
# 开发版：server.py 就在 frontend/ 目录下。
if getattr(sys, "frozen", False):
    FRONTEND_DIR = os.path.join(getattr(sys, "_MEIPASS", ""), "frontend")
else:
    FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(FRONTEND_DIR, "dist")


def _resolve_backend_port() -> int:
    """解析后端实际端口：从发现文件读取（后端启动时写入，端口可能为 OS 随机分配）"""
    from utils.port_discovery import read_backend_port
    return read_backend_port()


BACKEND_PORT = _resolve_backend_port()
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"

_MISSING_DIST_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>Cortex Agent</title></head>
<body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#111;color:#eee">
<div style="text-align:center;padding:24px">
<h2>前端未构建</h2>
<p>Vue 前端需要先执行构建：</p>
<pre style="background:#222;padding:12px;border-radius:8px">cd frontend && npm run build</pre>
</div>
</body></html>"""


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # 优先服务 Vue 构建产物 dist/（Vite 输出，入口为打包后的 index.html）
        if os.path.isfile(os.path.join(DIST_DIR, "index.html")):
            self._serve_dir = DIST_DIR
        else:
            self._serve_dir = FRONTEND_DIR
        super().__init__(*args, directory=self._serve_dir, **kwargs)

    def _proxy_request(self, method):
        path = self.path[4:]
        # 动态读后端端口：后端可能回退/重启到不同端口（见 utils/port_discovery），
        # 每次请求实时读取发现文件，避免启动时固定的 BACKEND_URL 与后端脱节
        # 用 127.0.0.1 而非 localhost：macOS 上 localhost 可能解析 ::1(IPv6)，
        # 而后端只绑 IPv4 → 代理 502
        from utils.port_discovery import read_backend_port
        url = f"http://127.0.0.1:{read_backend_port()}{path}"
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length else None
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in self.headers.items():
            if key.lower() not in ('host', 'content-length', 'connection'):
                req.add_header(key, value)
        try:
            response = urllib.request.urlopen(req, timeout=3)
            self.send_response(response.status)
            self.send_header('Access-Control-Allow-Origin', '*')
            for key, value in response.headers.items():
                if key.lower() not in ('transfer-encoding',):
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header('Access-Control-Allow-Origin', '*')
            for key, value in e.headers.items():
                if key.lower() not in ('transfer-encoding',):
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(e.read())
        except urllib.error.URLError as e:
            self.send_response(502)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": False,
                "error": {"code": "BACKEND_UNREACHABLE", "message": f"后端不可用: {e.reason}"}
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": False,
                "error": {"code": "PROXY_ERROR", "message": f"代理错误: {str(e)}"}
            }).encode())

    def do_GET(self):
        if self.path == "/backend-port":
            self._serve_backend_port()
            return
        if self.path.startswith("/api/"):
            self._proxy_request("GET")
            return
        if self.path == "/" or self.path == "/index.html":
            self._serve_index()
            return
        super().do_GET()

    def _serve_backend_port(self):
        """返回后端实际端口（前端 WebSocket 直连用，端口可能自动回退而非 8080）"""
        body = json.dumps({"port": BACKEND_PORT}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Vite 产物带内容 hash，文件名不变则内容不变 → 强缓存；index.html 由 _serve_index 设为 no-cache
        if getattr(self, "command", "") == "GET" and getattr(self, "path", "").startswith("/assets/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        super().end_headers()

    def _serve_index(self):
        """Serve the built Vue index.html (dist/index.html)."""
        if not os.path.isfile(os.path.join(DIST_DIR, "index.html")):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(_MISSING_DIST_PAGE.encode("utf-8"))
            return
        try:
            path = os.path.join(DIST_DIR, "index.html")
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy_request("POST")
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._proxy_request("PUT")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy_request("DELETE")
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-API-Key, Authorization')
        self.end_headers()

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()


# Allow port reuse on restart
socketserver.TCPServer.allow_reuse_address = True


def _ensure_dist():
    """dist/index.html 缺失时自动执行 npm run build（fresh clone 场景）。"""
    if os.path.isfile(os.path.join(DIST_DIR, "index.html")):
        return
    # 打包版（PyInstaller）一定内置 dist；缺失说明打包异常，不应尝试 npm build（环境无 npm 脚本）
    if getattr(sys, "frozen", False):
        print("[ERR] 打包版缺失 frontend/dist/index.html，前端将无法渲染")
        return
    import subprocess
    print("[..] 前端未构建 (dist 缺失)，正在执行 npm run build ...")
    try:
        r = subprocess.run(
            ["npm", "run", "build"],
            cwd=FRONTEND_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode == 0:
            print("[OK] 前端构建完成")
        else:
            print("[ERR] npm run build 失败:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    except Exception as e:
        print(f"[ERR] npm run build 失败: {e}")


def create_server(port=8765):
    _ensure_dist()
    return socketserver.ThreadingTCPServer(("", port), ProxyHandler)


if __name__ == "__main__":
    srv = create_server()
    print(f"http://localhost:8765")
    srv.serve_forever()
