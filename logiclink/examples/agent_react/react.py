# @link:eq ReAct = Loop(Think · Guard · (Tool · Observe | Emit))
# @link:block ReAct
def react(state):
    while True:
        # @link:block Think
        msg = llm.invoke(state)
        # @link:block Guard
        if not getattr(msg, "tool_calls", None):
            # @link:block Emit
            return msg
        # @link:block Tool
        out = tools.invoke(msg.tool_calls[0])
        # @link:block Observe
        state = observe(state, out)
