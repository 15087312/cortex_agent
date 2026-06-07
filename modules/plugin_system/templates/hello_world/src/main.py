def say_hello(args, api=None):
    name = str(args["name"]).strip()
    return {"greeting": f"Hello, {name}!"}
