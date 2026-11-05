"""Integration fixtures from the spec: ok_react / missing_think / eq_break / lexicon."""

import textwrap

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


WEAK_TOML = textwrap.dedent(
    """\
    [scan]
    roots = ["."]
    exclude = []
    extensions = [".py"]

    [atoms.Retrieve]
    calls = ["retrieve"]
    weak_calls = ["get"]
    """
)


def test_weak_call_witnesses_atom_without_w010(tmp_path):
    """A generic call proves the atom (E002) but is never a surplus (W010).

    `d.get("k")` is local dict access, not a retrieval: warning "you forgot
    Retrieve" on it flags every block that touches a dict.  But a term that
    honestly declares Retrieve must not fail for using only that API either.
    """
    src = textwrap.dedent(
        """\
        # @link:block Fetch = Retrieve
        def f(d):
            return d.get("k")
        """
    )
    root = write_proj(tmp_path, {"link.toml": WEAK_TOML, "a.py": src})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_weak_call_is_not_reported_as_surplus(tmp_path):
    src = textwrap.dedent(
        """\
        # @link:block G = Guard
        def f(d):
            if d.get("k"):
                return 1
            return 0
        """
    )
    root = write_proj(tmp_path, {"link.toml": WEAK_TOML, "a.py": src})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "W010"]


def test_strong_call_is_still_a_surplus(tmp_path):
    """Emptying `calls` for real retrieval names would kill the signal."""
    src = textwrap.dedent(
        """\
        # @link:block G = Guard
        def f(d):
            retrieve(d)
            if d:
                return 1
            return 0
        """
    )
    root = write_proj(tmp_path, {"link.toml": WEAK_TOML, "a.py": src})
    diags, code = run(root)
    assert code == 0
    assert [d for d in diags if d.code == "W010"], [(d.code, d.message) for d in diags]


def test_weak_only_evidence_is_reported_as_W011(tmp_path):
    """Generic-only evidence passes E002 but must stay visible as W011 (info)."""
    src = textwrap.dedent(
        """\
        # @link:block Fetch = Retrieve
        def f(d):
            return d.get("k")
        """
    )
    root = write_proj(tmp_path, {"link.toml": WEAK_TOML, "a.py": src})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    w011 = [d for d in diags if d.code == "W011"]
    assert len(w011) == 1, [(d.code, d.message) for d in diags]
    assert w011[0].level == "info"  # not a gate: --strict / --strict-warnings stay green
    assert "Retrieve" in w011[0].message and "Fetch" in w011[0].message


def test_strong_evidence_does_not_emit_W011(tmp_path):
    """A real retrieval call makes the claim verified, so no W011."""
    src = textwrap.dedent(
        """\
        # @link:block Fetch = Retrieve
        def f(d):
            return retrieve(d) or d.get("k")
        """
    )
    root = write_proj(tmp_path, {"link.toml": WEAK_TOML, "a.py": src})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "W011"]


def test_W011_does_not_change_cli_exit_codes(tmp_path, capsys):
    """W011 is a ledger, not a gate: both strict flags must still pass."""
    src = textwrap.dedent(
        """\
        # @link:block Fetch = Retrieve
        def f(d):
            return d.get("k")
        """
    )
    root = write_proj(tmp_path, {"link.toml": WEAK_TOML, "a.py": src})
    capsys.readouterr()
    assert cli_main([root, "--strict"]) == 0
    out = capsys.readouterr().out
    assert "W011" in out and "logiclink: ok" in out  # debt visible, green signal kept
    capsys.readouterr()
    assert cli_main([root, "--strict-warnings"]) == 0


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


# ---------------------------------------------------------------- F8/W012 同名符号歧义

SCAN_ALPHA_BETA = "[scan]\nroots = [\"app.link\", \"modules/alpha\", \"modules/beta\"]\nexclude = []\n"
BETA_IMPL = "# @link:block ASym = id\ndef f():\n    return 1\n"


def test_ambiguous_unqualified_use_is_reported_as_W012(tmp_path):
    """Two modules declaring the same short name + an unqualified `use`.

    `_symbol_decl` only ever sees one of them, so the wiring would silently pick
    an implementation the author never named.  The ambiguity must be visible
    (warning: a qualified reference is still valid, it just has to say which).
    """
    root = write_proj(
        tmp_path,
        {
            "link.toml": SCAN_ALPHA_BETA,
            "app.link": "use ASym\n",
            "modules/alpha/alpha.link": "export ASym\neq ASym = id\n",
            "modules/beta/beta.py": BETA_IMPL,
        },
    )
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    w = [d for d in diags if d.code == "W012"]
    assert len(w) == 1, [(d.code, d.message) for d in diags]
    assert w[0].level == "warning"
    assert "alpha" in w[0].message and "beta" in w[0].message
    assert "qualify" in w[0].message


def test_qualified_use_disambiguates_same_symbol(tmp_path):
    """`alpha.ASym` names one module: the same short name elsewhere is not a problem."""
    root = write_proj(
        tmp_path,
        {
            "link.toml": SCAN_ALPHA_BETA,
            "app.link": "use a := alpha.ASym\n",
            "modules/alpha/alpha.link": "export ASym\neq ASym = id\n",
            "modules/beta/beta.py": BETA_IMPL,
        },
    )
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "W012"]


def test_dotted_export_resolves_in_its_module(tmp_path):
    """`export alpha.ASym` + `use a := alpha.ASym` must resolve (no E005).

    Symbol tables are keyed by the full declared name while `use` names only the
    last segment.  Matching only the exact key made a dotted export invisible to
    its own wiring and reported a false E005.
    """
    root = write_proj(
        tmp_path,
        {
            "link.toml": SCAN_ALPHA_BETA,
            "app.link": "use a := alpha.ASym\n",
            "modules/alpha/alpha.link": "export alpha.ASym\neq alpha.ASym = id\n",
        },
    )
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code in ("E005", "W012")]


def test_unqualified_use_of_dotted_exports_is_ambiguous(tmp_path):
    """Two dotted exports of the same short name + unqualified `use` -> W012."""
    root = write_proj(
        tmp_path,
        {
            "link.toml": SCAN_ALPHA_BETA,
            "app.link": "use ASym\n",
            "modules/alpha/alpha.link": "export alpha.ASym\neq alpha.ASym = id\n",
            "modules/beta/beta.link": "export beta.ASym\neq beta.ASym = id\n",
        },
    )
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    w = [d for d in diags if d.code == "W012"]
    assert len(w) == 1, [(d.code, d.message) for d in diags]
    assert "alpha" in w[0].message and "beta" in w[0].message


def test_link_declaration_plus_python_block_is_not_ambiguous(tmp_path):
    """A `.link` declaration and its Python block are one symbol, not two implementations."""
    root = write_proj(
        tmp_path,
        {
            "link.toml": "[scan]\nroots = [\"app.link\", \"modules/alpha\"]\nexclude = []\n",
            "app.link": "use a := alpha.ASym\n",
            "modules/alpha/alpha.link": "export ASym\neq ASym = id\n",
            "modules/alpha/impl.py": BETA_IMPL,
        },
    )
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "W012"]


# ---------------------------------------------------------------- W011 上限（E011）

WEAK_BLOCK = textwrap.dedent(
    """\
    # @link:block Fetch = Retrieve
    def f(d):
        return d.get("k")
    """
)


def _debt_toml(limit: int | None) -> str:
    if limit is None:
        return WEAK_TOML
    return WEAK_TOML + f"\n[arch]\nmax_weak_evidence = {limit}\n"


def test_weak_evidence_ceiling_allows_baseline(tmp_path):
    root = write_proj(tmp_path, {"link.toml": _debt_toml(1), "a.py": WEAK_BLOCK})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "E011"]


def test_weak_evidence_ceiling_fires_when_debt_grows(tmp_path):
    """The ledger is machine-enforced: one more weak-only atom than the ceiling fails."""
    root = write_proj(tmp_path, {"link.toml": _debt_toml(0), "a.py": WEAK_BLOCK})
    diags, code = run(root)
    assert code == 1
    e011 = [d for d in diags if d.code == "E011"]
    assert len(e011) == 1, [(d.code, d.message) for d in diags]
    assert e011[0].level == "error"
    assert "max_weak_evidence = 0" in e011[0].message


def test_weak_evidence_ceiling_default_is_unlimited(tmp_path):
    root = write_proj(tmp_path, {"link.toml": _debt_toml(None), "a.py": WEAK_BLOCK})
    diags, code = run(root)
    assert code == 0
    assert not [d for d in diags if d.code == "E011"]


def test_max_weak_evidence_is_parsed(tmp_path):
    from logiclink.config import load_config
    from pathlib import Path

    root = write_proj(tmp_path, {"link.toml": "[arch]\nmax_weak_evidence = 6\n"})
    assert load_config(Path(root)).arch.max_weak_evidence == 6


# ---------------------------------------------------------------- E010 复杂度上限

def test_max_complexity_emits_E010(tmp_path):
    """`[arch] max_complexity` is wired: calls + branches + loops over the limit fail."""
    toml = '[scan]\nroots = ["."]\nexclude = []\nextensions = [".py"]\n[arch]\nmax_complexity = 1\n'
    src = textwrap.dedent(
        """\
        # @link:block B = id
        def f():
            a()
            if x:
                b()
            return 1
        """
    )
    root = write_proj(tmp_path, {"link.toml": toml, "a.py": src})
    diags, code = run(root)
    assert code == 1
    assert [d for d in diags if d.code == "E010"], [(d.code, d.message) for d in diags]


def test_max_complexity_default_is_unlimited(tmp_path):
    src = textwrap.dedent(
        """\
        # @link:block B = id
        def f():
            a()
            if x:
                b()
            return 1
        """
    )
    root = write_proj(tmp_path, {"a.py": src})
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "E010"]


def test_W012_is_gating_under_strict(tmp_path):
    """Same contract as W010: a warning by default, an error under `--strict`."""
    root = write_proj(
        tmp_path,
        {
            "link.toml": SCAN_ALPHA_BETA,
            "app.link": "use ASym\n",
            "modules/alpha/alpha.link": "export ASym\neq ASym = id\n",
            "modules/beta/beta.py": BETA_IMPL,
        },
    )
    diags, code = run(root, strict=True)
    assert code == 1
    w = [d for d in diags if d.code == "W012"]
    assert w and w[0].level == "error", [(d.code, d.level) for d in diags]


def test_use_module_segment_must_name_a_declaration_not_a_bare_atom(tmp_path):
    """`use a := alpha.Think` cannot lean on a global atom: the module owns nothing."""
    toml = '[scan]\nroots = ["app.link"]\nexclude = []\n[atoms.Think]\ncalls = ["think"]\n'
    root = write_proj(tmp_path, {"link.toml": toml, "app.link": "use a := alpha.Think\n"})
    diags, code = run(root)
    assert code == 1
    e005 = [d for d in diags if d.code == "E005"]
    assert e005 and "only an atom" in e005[0].message, [(d.code, d.message) for d in diags]


# ---------------------------------------------------------------- config validation

def test_invalid_arch_int_fails_cleanly(tmp_path):
    from logiclink.config import ConfigError, load_config
    from pathlib import Path

    root = write_proj(tmp_path, {"link.toml": '[arch]\nmax_weak_evidence = "6"\n'})
    try:
        load_config(Path(root))
    except ConfigError as exc:
        assert "max_weak_evidence" in str(exc)
    else:  # pragma: no cover - the assertion above is the test
        raise AssertionError("string max_weak_evidence should be rejected")


def test_negative_arch_int_fails_cleanly(tmp_path):
    from logiclink.config import ConfigError, load_config
    from pathlib import Path

    root = write_proj(tmp_path, {"link.toml": "[arch]\nmax_complexity = -5\n"})
    try:
        load_config(Path(root))
    except ConfigError as exc:
        assert "max_complexity" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("negative max_complexity should be rejected")


def test_cli_reports_invalid_config_with_exit_2(tmp_path, capsys):
    root = write_proj(tmp_path, {"link.toml": "[arch]\nmax_complexity = true\n"})
    assert cli_main([root]) == 2
    assert "invalid link.toml" in capsys.readouterr().err
