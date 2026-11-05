"""Every architecture knob must be wired to a check (docs/CHECKER_AUDIT.md C3).

`forbid_eq_change` used to be parsed and then never compared against anything:
the gate read as a guard rail while only meaning "refuse to run".  Six more
fields had the same problem.  This test makes that impossible: a field that is
declared in :class:`ArchConstraints` and referenced nowhere outside
``config.py`` fails the suite.

If you are adding a knob, wire it and test its behaviour in the same commit.
If it needs a temporary exemption, list it in ``ALLOWED_UNWIRED`` with a TODO
and a deadline -- the list should only ever shrink.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from logiclink.config import ArchConstraints

SRC = Path(__file__).resolve().parents[1] / "src" / "logiclink"

# Fields deliberately allowed to exist without a consumer yet.
ALLOWED_UNWIRED: dict[str, str] = {}


def _sources() -> dict[str, str]:
    """`file name -> text` for every module except the config definition itself."""
    out: dict[str, str] = {}
    for p in SRC.rglob("*.py"):
        if p.name == "config.py":
            continue
        out[p.name] = p.read_text(encoding="utf-8")
    return out


def test_every_arch_field_is_referenced_outside_config():
    sources = _sources()
    unwired = []
    for f in dataclasses.fields(ArchConstraints):
        if f.name in ALLOWED_UNWIRED:
            continue
        if not any(f.name in text for text in sources.values()):
            unwired.append(f.name)
    assert not unwired, (
        f"ArchConstraints fields referenced nowhere outside config.py: {unwired}. "
        "Wire them to a check (and test the behaviour) or delete them."
    )


def test_allow_cycles_is_wired():
    """A concrete guard: the whitelist must actually reach the cycle check."""
    sources = _sources()
    assert "allow_cycles" in sources["check.py"], "check_graph must receive allow_cycles"
    assert "allow_cycles" in sources["v2_graph.py"], "v2_graph must consume allow_cycles"


def test_forbid_eq_change_reaches_the_baseline_gate():
    sources = _sources()
    assert "forbid_eq_change" in sources["arch.py"], "E305 gate must read forbid_eq_change"
    assert "arch.check_arch" in sources["check.py"], "run() must call the gate"
