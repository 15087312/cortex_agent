import urllib.request

urls = [
    "http://localhost:5173/#/settings",
    "http://localhost:5173/#/dashboard",
    "http://localhost:5173/#/perception",
    "http://localhost:5173/#/modules",
    "http://localhost:5173/#/memory",
    "http://localhost:5173/#/security",
    "http://localhost:5173/#/orchestration",
    "http://localhost:5173/#/skills",
    "http://localhost:5173/#/tasks",
    "http://localhost:5173/#/chat"
]

for u in urls:
    try:
        req = urllib.request.Request(u)
        with urllib.request.urlopen(req) as resp:
            print(u, resp.status)
    except Exception as e:
        print(u, "ERR", str(e))
