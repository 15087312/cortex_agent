import urllib.request
import json

req = urllib.request.Request(
    'http://localhost:8080/api/config',
    data=json.dumps({"shortcut_keys": "Ctrl+Shift+K"}).encode('utf-8'),
    headers={'Content-Type': 'application/json'},
    method='PUT'
)
try:
    with urllib.request.urlopen(req) as resp:
        print(resp.status, resp.read().decode())
except Exception as e:
    print(e)
