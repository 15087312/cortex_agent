def send_operator_note(args, api=None):
    channel = str(args.get("channel") or "default")
    if api is None:
        return {"sent": False, "channel": channel}
    message = str(args["message"])[:500]
    sent = api.send_output(message, channel=channel, content_type="text/plain")
    return {"sent": True, "channel": str(sent.get("channel") or channel)}
