import http.server
import urllib.request
import urllib.error
import os
import json
import socketserver

# 防残留: 若启动本服务的 cortex 父进程被强杀，自动退出避免孤儿进程
try:
    from cortex.watchdog import enable as _enable_orphan_watchdog
    _enable_orphan_watchdog()
except Exception:
    pass

BACKEND_URL = "http://localhost:8080"
FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(FRONTEND_DIR, "dist")

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
        url = f"{BACKEND_URL}{path}"
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
        if self.path.startswith("/api/"):
            self._proxy_request("GET")
            return
        if self.path == "/" or self.path == "/index.html":
            self._serve_index()
            return
        super().do_GET()

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
