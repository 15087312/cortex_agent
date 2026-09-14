"""v2 L4 state machine: declarations vs real code (E401/E402/E403)."""

import textwrap

from logiclink.check import run


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


def ok_proj():
    return {
        "link.toml": "",
        "modules/blackboard/blackboard.link": """
            state blackboard
            init = empty
            trans = "empty --AcceptEvent--> partial"
            trans = "partial --merge--> complete"
            trans = "complete --flush--> empty"
            """,
        "modules/blackboard/impl.py": """
            def handle():
                accept_event()
                state = "partial"
                merge()
                state = "complete"
                flush()
                state = "empty"
            """,
    }


def test_state_ok(tmp_path):
    root = write_proj(tmp_path, ok_proj())
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code.startswith("E40")]


def test_e401_undeclared_state_literal(tmp_path):
    files = ok_proj()
    files["modules/blackboard/impl.py"] = """
        def handle():
            accept_event()
            state = "unknown_state"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e401 = [d for d in diags if d.code == "E401"]
    assert e401 and "unknown_state" in e401[0].message, [(d.code, d.message) for d in diags]


def test_e402_declared_event_not_implemented(tmp_path):
    files = ok_proj()
    # flush() is declared as an event but never called in the module.
    files["modules/blackboard/impl.py"] = """
        def handle():
            accept_event()
            state = "partial"
            merge()
            state = "complete"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e402 = [d for d in diags if d.code == "E402"]
    assert e402 and "flush" in e402[0].message, [(d.code, d.message) for d in diags]


def test_e403_reached_without_declared_transition(tmp_path):
    files = ok_proj()
    # "partial" is a declared state but only a transition *source*: reaching it
    # directly (no declared transition targets it, init is "complete") is E403.
    files["modules/blackboard/blackboard.link"] = """
        state blackboard
        init = complete
        trans = "partial --merge--> complete"
        """
    files["modules/blackboard/impl.py"] = """
        def handle():
            merge()
            state = "partial"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e403 = [d for d in diags if d.code == "E403"]
    assert e403 and "partial" in e403[0].message, [(d.code, d.message) for d in diags]


def test_snake_case_event_matching(tmp_path):
    # Snake_case call matches the declared event name.
    files = ok_proj()
    files["modules/blackboard/blackboard.link"] = """
        state blackboard
        init = empty
        trans = "empty --accept_event--> partial"
        """
    files["modules/blackboard/impl.py"] = """
        def handle():
            accept_event()
            state = "partial"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
