def read_memory_record(args, api=None):
    if api is None:
        return {"found": False, "value": ""}
    key = str(args["key"]).strip()
    value = api.read_memory(key)
    if value is None:
        return {"found": False, "value": ""}
    return {"found": True, "value": str(value)[:2048]}
