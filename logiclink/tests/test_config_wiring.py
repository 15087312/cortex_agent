"""Every architecture knob must be wired to a check (docs/CHECKER_AUDIT.md C3).

`forbid_eq_change` used to be parsed and then never compared against anything:
the gate read as a guard rail while only meaning "refuse to run".  Six more
fields had the same problem.  This test makes that impossible: a field that is
declared in :class:`ArchConstraints` and referenced nowhere outside
``config.py`` fails the suite.

The reference scan is **AST-based**: a field mentioned only in a comment or a
docstring is not a consumer.  (The previous text search could be satisfied by
the sentence "we should implement max_complexity one day".)

If you are adding a knob, wire it and test its behaviour in the same commit.
If it needs a temporary exemption, list it in ``ALLOWED_UNWIRED`` with a TODO
and a deadline -- the list should only ever shrink.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from logiclink.config import ArchConstraints

SRC = Path(__file__).resolve().parents[1] / "src" / "logiclink"

# Fields deliberately allowed to exist without a consumer yet.
ALLOWED_UNWIRED: dict[str, str] = {}


def _symbols_in(source: str) -> set[str]:
    """Identifiers actually used by ``source`` (attribute names + plain names).

    Comments are dropped by the parser and docstrings are just ``Expr(Constant)``
    nodes, so neither can fake a consumer.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Name):
            out.add(node.id)
    return out


def _file_symbols(name: str) -> set[str]:
    path = SRC / name
    if not path.is_file():
        return set()
    return _symbols_in(path.read_text(encoding="utf-8"))


def _all_symbols() -> set[str]:
    out: set[str] = set()
    for p in SRC.rglob("*.py"):
        if p.name == "config.py":
            continue
        out |= _symbols_in(p.read_text(encoding="utf-8"))
    return out


def test_every_arch_field_is_referenced_outside_config():
    used = _all_symbols()
    unwired = [
        f.name
        for f in dataclasses.fields(ArchConstraints)
        if f.name not in ALLOWED_UNWIRED and f.name not in used
    ]
    assert not unwired, (
        f"ArchConstraints fields referenced nowhere outside config.py: {unwired}. "
        "Wire them to a check (and test the behaviour) or delete them."
    )


def test_comment_mentions_do_not_count_as_wiring():
    """A TODO sentence is not a consumer."""
    src = "# TODO: implement max_weak_evidence later\n\"\"\"max_complexity\"\"\"\n"
    assert "max_weak_evidence" not in _symbols_in(src)
    assert "max_complexity" not in _symbols_in(src)


def test_allow_cycles_is_wired():
    """A concrete guard: the whitelist must actually reach the cycle check."""
    assert "allow_cycles" in _file_symbols("check.py"), "check_graph must receive allow_cycles"
    assert "allow_cycles" in _file_symbols("v2_graph.py"), "v2_graph must consume allow_cycles"


def test_forbid_eq_change_reaches_the_baseline_gate():
    assert "forbid_eq_change" in _file_symbols("arch.py"), "E305 gate must read forbid_eq_change"
    assert "check_arch" in _file_symbols("check.py"), "run() must call the gate"


def test_max_weak_evidence_reaches_the_gate():
    assert "max_weak_evidence" in _file_symbols("check.py"), "run() must enforce the W011 ceiling"
    # 诊断码是字符串字面量，不会出现在 AST 标识符集合里；用源码确认门禁真的会报错。
    assert "E011" in (SRC / "check.py").read_text(encoding="utf-8")
