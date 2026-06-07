def summarize_file(args, api=None):
    filename = str(args["filename"]).strip()
    if "/" in filename or "\\" in filename or filename in {"", ".", ".."} or ".." in filename:
        raise ValueError("filename must be a simple file name")
    if api is None or not hasattr(api, "filesystem"):
        return {"filename": filename, "summary": "Template dry run: no host filesystem Gateway was provided."}
    text = api.filesystem.read_text("data/input/" + filename, max_bytes=16000)
    words = str(text).split()
    return {
        "filename": filename,
        "summary": " ".join(words[:120])[:1000],
    }
