def retrieve_records(args, api=None):
    query = str(args["query"]).strip()
    limit = int(args.get("limit") or 3)
    limit = max(1, min(limit, 5))
    if api is not None and hasattr(api, "memory"):
        records = api.memory.read(query=query, limit=limit)
        return {"results": records[:limit]}
    return {
        "results": [
            {
                "id": "example-1",
                "title": "Example read-only record",
                "snippet": f"Local template result for query: {query[:120]}",
            }
        ][:limit]
    }
