def package_echo(args, api=None):
    return {"text": str(args["text"])[:256]}
