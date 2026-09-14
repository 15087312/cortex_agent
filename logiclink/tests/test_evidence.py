from logiclink.evidence import attribute_call, call_matches, facts_of


def test_facts_calls_branches_loops_returns():
    src = (
        "def f(state):\n"
        "    while True:\n"
        "        msg = llm.invoke(state)\n"
        "        if not msg:\n"
        "            return msg\n"
        "        out = tools.invoke(msg)\n"
        "        return observe(out)\n"
    )
    facts = facts_of(src)
    assert any(n == "llm.invoke" for _, n in facts.calls)
    assert any(n == "tools.invoke" for _, n in facts.calls)
    assert len(facts.loops) == 1
    assert len(facts.branches) == 1
    assert len(facts.returns) == 2
    assert facts.funcs[0].name == "f"


def test_call_matches_suffix_and_case():
    assert call_matches("llm.invoke", "llm.invoke")
    assert call_matches("self.tools.invoke", "tools.invoke")
    assert call_matches("LLM.Invoke", "llm.invoke")
    assert not call_matches("observe", "llm.invoke")


def test_attribute_call_exact_beats_suffix():
    rules = {"Think": ["llm.invoke", "invoke"], "Tool": ["tools.invoke"]}
    assert attribute_call("llm.invoke", rules) == "Think"
    assert attribute_call("tools.invoke", rules) == "Tool"
    # bare last-segment calls (e.g. `q.append`) must NOT attribute to dotted rules
    assert attribute_call("append", {"Write": ["notebook.append"]}) is None


def test_syntax_error_is_empty():
    assert facts_of("def broken(:").calls == []
