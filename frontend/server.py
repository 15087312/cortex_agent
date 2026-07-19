import http.server
import urllib.request
import urllib.error
import os
import json
import socketserver
import hashlib
import glob

BACKEND_URL = "http://localhost:8080"
FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_js_hash():
    """Compute a hash of all JS file mtimes for cache busting."""
    h = hashlib.md5()
    pattern = os.path.join(FRONTEND_DIR, "**", "*.js")
    for f in sorted(glob.glob(pattern, recursive=True)):
        h.update(str(os.path.getmtime(f)).encode())
    return h.hexdigest()[:12]


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self._js_hash_cache = None  # must set before super().__init__
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def _get_js_hash(self):
        if self._js_hash_cache is None:
            self._js_hash_cache = _get_js_hash()
        return self._js_hash_cache

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
        """Serve index.html with cache-busting version hash."""
        path = os.path.join(FRONTEND_DIR, "index.html")
        if not os.path.isfile(path):
            self.send_error(404)
            return
        try:
            with open(path, "rb") as f:
                content = f.read()
            version = self._get_js_hash()
            content = content.replace(b"v=2", f"v={version}".encode())
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


def create_server(port=8765):
    return socketserver.ThreadingTCPServer(("", port), ProxyHandler)


if __name__ == "__main__":
    srv = create_server()
    print(f"http://localhost:8765")
    srv.serve_forever()
