def fetch_status(args, api=None):
    path = str(args["path"]).strip()
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    query = str(args.get("query") or "").strip()
    if api is None or not hasattr(api, "network"):
        return {"status_code": 200, "body": f"dry template response for {path}?{query}"[:2048]}
    response = api.network.get("https://api.example.com" + path, query=query)
    return {
        "status_code": int(getattr(response, "status_code", 200)),
        "body": str(getattr(response, "text", ""))[:2048],
    }
