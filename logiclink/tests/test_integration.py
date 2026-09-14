"""Integration fixtures from the spec: ok_react / missing_think / eq_break / lexicon."""

import textwrap

import pytest

from logiclink.check import run
from logiclink.cli import main as cli_main

OK_REACT = textwrap.dedent(
    """\
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
    """
)


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


def codes(diags):
    return sorted(f"{d.level}[{d.code}]" for d in diags)


def test_ok_react(tmp_path):
    root = write_proj(tmp_path, {"react.py": OK_REACT})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert diags == []


def test_missing_think(tmp_path):
    src = OK_REACT.replace("        msg = llm.invoke(state)\n", "")
    root = write_proj(tmp_path, {"react.py": src})
    diags, code = run(root)
    assert code == 1
    e002 = [d for d in diags if d.code == "E002" and "Think" in d.message]
    assert e002, [(d.code, d.message) for d in diags]


def test_eq_break(tmp_path):
    src = textwrap.dedent(
        """\
        # @link:eq ReAct = Loop(Think · Tool)
        # @link:block ReAct
        def react(state):
            while True:
                # @link:block Tool
                out = tools.invoke(state)
        """
    )
    root = write_proj(tmp_path, {"react.py": src})
    diags, code = run(root)
    assert code == 1
    e004 = [d for d in diags if d.code == "E004"]
    assert e004 and "missing: Think" in e004[0].message, [(d.code, d.message) for d in diags]


def test_chinese_lexicon(tmp_path):
    toml = textwrap.dedent(
        """\
        [scan]
        roots = ["."]
        exclude = []
        extensions = [".py", ".link"]

        [lexicon]
        "思考" = "Think"
        "调用工具" = "Tool"
        "循环" = "Loop"
        """
    )
    src = textwrap.dedent(
        """\
        # @link:eq ReAct = 循环(思考 · 调用工具)
        # @link:block ReAct
        def react(state):
            while True:
                # @link:block Think
                msg = llm.invoke(state)
                # @link:block Tool
                out = tools.invoke(msg)
        """
    )
    root = write_proj(tmp_path, {"link.toml": toml, "react.py": src})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_unregistered_ident_is_E001(tmp_path):
    root = write_proj(tmp_path, {"a.link": "eq X = Frobnicate · Think\n"})
    diags, code = run(root)
    assert code == 1
    assert any(d.code == "E001" and "Frobnicate" in d.message for d in diags)


def test_strict_turns_W010_into_error(tmp_path, capsys):
    src = textwrap.dedent(
        """\
        # @link:block Think
        def f(state):
            msg = llm.invoke(state)
            out = tools.invoke(msg)
        """
    )
    root = write_proj(tmp_path, {"react.py": src})
    assert cli_main([root]) == 0  # warning only
    capsys.readouterr()
    assert cli_main([root, "--strict"]) == 1  # W010 -> error


def test_json_and_explain(tmp_path, capsys):
    root = write_proj(tmp_path, {"react.py": OK_REACT})
    assert cli_main([root, "--json"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "[]"
    assert cli_main([root, "--explain", "ReAct"]) == 0
    explained = capsys.readouterr().out.strip()
    assert explained == "Loop(Think · Guard · (Tool · Observe | Emit))"
    assert cli_main([root, "--explain", "Nope"]) == 1


def test_emit_any_semantics(tmp_path):
    # Emit witnessed by EITHER a return OR a notify-call (v1 existence semantics).
    toml = textwrap.dedent(
        """\
        [scan]
        roots = ["."]
        exclude = []
        extensions = [".py", ".link"]

        [atoms.Emit]
        calls = ["notify_done"]
        returns = true
        """
    )
    src = textwrap.dedent(
        """\
        # @link:block A = Emit
        def a():
            return 1
        # @link:block B = Emit
        def b():
            notify_done()
        """
    )
    root = write_proj(tmp_path, {"link.toml": toml, "s.py": src})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_e004_no_cross_file_shadowing(tmp_path):
    # Same line ranges in different files must not shadow each other's children.
    body = textwrap.dedent(
        """\
        # @link:eq P = Loop(Think · Tool)
        # @link:block P
        def f():
            while True:
                # @link:block P_Think = Think
                think()
                # @link:block P_Tool = Tool
                call_tool()
        """
    )
    root = write_proj(tmp_path, {"a.py": body, "b.py": body.replace("P", "Q")})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_hook_failure_is_E006(tmp_path):
    root = write_proj(tmp_path, {"a.link": "block X @ file:nope.py fn:f\n"})
    diags, code = run(root)
    assert code == 1
    assert any(d.code == "E006" for d in diags)


def test_duplicate_export_is_E005(tmp_path):
    root = write_proj(
        tmp_path, {"a.link": "export Reason\nexport Reason\n"}
    )
    diags, code = run(root)
    assert code == 1
    assert any(d.code == "E005" for d in diags)


def test_eq_redefined_inconsistent_is_E003(tmp_path):
    # 同名 eq 在两个文件里重定义成不同项 → E003
    root = write_proj(
        tmp_path,
        {"a.link": "eq X = Think\n", "b.link": "eq X = Tool\n"},
    )
    diags, code = run(root)
    assert code == 1
    assert any(d.code == "E003" and "redefined" in d.message for d in diags)


def test_block_term_conflicts_with_eq_is_E003(tmp_path):
    # block 显式项与同名 eq 不一致 → E003
    toml = textwrap.dedent(
        """\
        [scan]
        roots = ["."]
        exclude = []
        extensions = [".py", ".link"]
        """
    )
    src = textwrap.dedent(
        """\
        # @link:eq X = Think
        # @link:block X = Tool
        def f():
            call_tool()
        """
    )
    root = write_proj(tmp_path, {"link.toml": toml, "x.py": src})
    diags, code = run(root)
    assert code == 1
    e003 = [d for d in diags if d.code == "E003"]
    assert e003 and "block" in e003[0].message, [(d.code, d.message) for d in diags]
