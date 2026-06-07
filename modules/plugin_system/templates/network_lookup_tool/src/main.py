def lookup_status(args, api=None):
    if api is None:
        return {"status_code": 503, "body": ""}
    path = str(args["path"]).strip()
    if not path.startswith("/"):
        path = "/" + path
    response = api.network_request(f"https://api.example.com{path}", method="GET", timeout=3)
    return {
        "status_code": int(response.get("status_code", 0)),
        "body": str(response.get("body", ""))[:2048],
    }
