"""v2 L4 -- state machine layer.

A ``.link`` file may declare a state machine:

    state blackboard
    init = empty
    trans = "empty --AcceptEvent--> partial"
    trans = "partial --merge--> complete"
    trans = "complete --flush--> empty"

The checker binds the machine to the module directory of the declaring
``.link`` file and reconciles declarations against real code:

- every declared transition event must appear as a real call in the module;
- every state literal assigned in the module's code must be a declared state;
- a reached state must be reachable: it must be the init state or the target
  of some declared transition.

Diagnostics (returned as tuples):
    E401  code uses a state that is not part of the declared machine
    E402  a declared transition event has no real call in the module
    E403  code reaches a declared state via no declared transition
"""

from __future__ import annotations

import re
from pathlib import Path

from .evidence import call_matches, facts_of
from .parser import Decl

_TRANS_RE = re.compile(r"^\s*(.+?)\s*--(.+?)-->\s*(.+?)\s*$")
_STATE_LIT_RE = re.compile(
    r"""(?:\bstate\s*=|self\.\w*state\w*\s*=|set_state\(|update_state\(|_set_state\(|_update_state\()\s*["']([A-Za-z_]\w*)["']""",
    re.IGNORECASE,
)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _collect_machines(decls: list[Decl]) -> list[dict]:
    machines: list[dict] = []
    cur: dict | None = None
    for d in decls:
        if d.kind == "state":
            cur = {"name": d.name, "decl": d, "init": None, "trans": [], "path": d.path}
            machines.append(cur)
        elif d.kind == "state_init" and cur is not None and cur["name"] == d.name:
            cur["init"] = d.term_str
        elif d.kind == "state_trans" and cur is not None and cur["name"] == d.name:
            m = _TRANS_RE.match(d.term_str or "")
            if m:
                cur["trans"].append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), d))
    return machines


def _scan_module(module_dir: Path) -> tuple[set[str], set[str], dict[str, tuple[str, int]], list[tuple[str, int, str]]]:
    """Return (events_called, state_literals, event_evidence, literal_sites)."""
    events: set[str] = set()
    literals: set[str] = set()
    ev_evidence: dict[str, tuple[str, int]] = {}
    sites: list[tuple[str, int, str]] = []
    if not module_dir.is_dir():
        return events, literals, ev_evidence, sites
    for py in sorted(module_dir.rglob("*.py")):
        if any(part in {"__pycache__", ".venv", "node_modules", "tests"} for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        facts = facts_of(text)
        for ln, call in facts.calls:
            events.add(call)
            ev_evidence.setdefault(call, (str(py), ln))
        for ln, line in enumerate(text.splitlines(), start=1):
            for m in _STATE_LIT_RE.finditer(line):
                literals.add(m.group(1))
                sites.append((str(py), ln, m.group(1)))
    return events, literals, ev_evidence, sites


def check_state(decls: list[Decl]) -> list[tuple]:
    machines = _collect_machines(decls)
    if not machines:
        return []
    diags: list[tuple] = []

    for mach in machines:
        decl = mach["decl"]
        init = mach["init"]
        trans = mach["trans"]
        states: set[str] = set()
        to_states: set[str] = set()
        events: set[str] = set()
        for (frm, ev, to, _d) in trans:
            states.add(frm)
            states.add(to)
            to_states.add(to)
            events.add(ev)
        if init is not None:
            states.add(init)
        module_dir = Path(decl.path).resolve().parent
        events_called, literals, ev_evidence, sites = _scan_module(module_dir)

        # E402: every declared event must be implemented as a real call.
        for (frm, ev, to, tdecl) in trans:
            ev_snake = _snake(ev)
            if not any(_snake(c) == ev_snake or call_matches(c, ev) for c in events_called):
                diags.append(
                        (
                            "E402",
                            "error",
                            f"state `{mach['name']}` declares transition "
                            f"`{frm} --{ev}--> {to}` but no real call implements event `{ev}` "
                            f"in {module_dir.name}",
                            tdecl.path,
                            tdecl.line,
                            tdecl.col,
                            "L4 state machine: declared transitions must be implemented",
                        )
                    )

        # E401 / E403: reconcile code-side state literals with the machine.
        for (fpath, fline, lit) in sites:
            if lit not in states:
                diags.append(
                    (
                        "E401",
                        "error",
                        f"state `{mach['name']}`: code sets `{lit}` which is not a declared state "
                        f"(declared: {{{', '.join(sorted(states))}}})",
                        fpath,
                        fline,
                        1,
                        "L4 state machine: every state literal must be declared",
                    )
                )
            elif lit != init and lit not in to_states:
                diags.append(
                    (
                        "E403",
                        "error",
                        f"state `{mach['name']}`: code reaches `{lit}` but no declared transition "
                        f"targets it (init={init!r}, targets: {{{', '.join(sorted(to_states))}}})",
                        fpath,
                        fline,
                        1,
                        "L4 state machine: reached states must be reachable by a declared transition",
                    )
                )
    return diags
