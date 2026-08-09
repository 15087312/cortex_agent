import urllib.request
import json

endpoints = [
    ("GET", "http://localhost:8080/api/config", None),
    ("PUT", "http://localhost:8080/api/config", {"shortcut_keys": "Ctrl+Shift+K"}),
    ("PUT", "http://localhost:8080/api/config", {"CORTEX_MODE": "pure_chat"}),
    ("PUT", "http://localhost:8080/api/config", {"PERCEPTION_TRIGGER_COOLDOWN": 75}),
    ("GET", "http://localhost:8080/api/config/memory-libs", None),
    ("GET", "http://localhost:8080/api/management/dashboard", None),
    ("POST", "http://localhost:8080/api/management/perception/start", None),
    ("POST", "http://localhost:8080/api/management/perception/stop", None),
    ("POST", "http://localhost:8080/api/management/modules/test/refresh", None),
    ("GET", "http://localhost:8080/api/management/memory/events", None),
    ("POST", "http://localhost:8080/api/security/switch", {"enabled": True}),
    ("POST", "http://localhost:8080/api/management/orchestration/preview", {"agent_id": "test", "prompt": "hello"}),
    ("POST", "http://localhost:8080/api/management/skills/reload", None),
    ("PUT", "http://localhost:8080/api/stream/session/1/tasks", {"tasks": [{"id": "1", "title": "test"}]})
]

for method, url, data in endpoints:
    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            print(method, url, resp.status, resp.read().decode()[:100])
    except urllib.error.HTTPError as e:
        print(method, url, e.code, e.read().decode()[:100])
    except Exception as e:
        print(method, url, "ERR", str(e))
