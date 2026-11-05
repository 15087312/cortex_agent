"""v2 L4 -- state machine layer.

A ``.link`` file may declare a state machine:

    state blackboard
    init = empty
    trans = "empty --AcceptEvent--> partial"
    trans = "partial --merge--> complete"
    trans = "complete --flush--> empty"

When the states are Enum members (the common Python spelling) the machine names
its evidence source instead of matching string literals:

    state turn
    enum = TurnState
    init = idle
    trans = "idle --transition_to--> planning"
    trans = "planning --transition_to--> executing"

The checker binds the machine to its module directory (the nearest ancestor that
is a scan root or that holds a ``<dirname>.link``, not just the file's own
folder -- a machine declared in ``<module>/core/`` used to scan only ``core/``)
and reconciles declarations against real code:

- every declared transition event must appear as a real call in the module, or
  the transition target must be reached by a real site (a call that passes the
  state, or an assignment of it) -- Enum machines are implemented through
  ``transition_to(TurnState.X)``, which is a target site, not a call named after
  the transition;
- every state the code references (``TurnState.X``) must be declared;
- a state the code *assigns/passes* must be reachable: it must be the init state
  or the target of some declared transition.  Merely reading a state is not a
  reach, so ``self.state in (TurnState.A, TurnState.B)`` is not E403.

Diagnostics (returned as tuples):
    E401  code uses a state that is not part of the declared machine
    E402  a declared transition has no implementation in the module
    E403  code reaches a declared state via no declared transition
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path

from .evidence import call_matches, facts_of
from .parser import Decl

_TRANS_RE = re.compile(r"^\s*(.+?)\s*--(.+?)-->\s*(.+?)\s*$")
_STATE_LIT_RE = re.compile(
    r"""(?:\bstate\s*=|self\.\w*state\w*\s*=|set_state\(|update_state\(|_set_state\(|_update_state\()\s*["']([A-Za-z_]\w*)["']""",
    re.IGNORECASE,
)
_SKIP_PARTS = {"__pycache__", ".venv", "node_modules", "tests"}


def _snake(name: str) -> str:
    """``AcceptEvent`` -> ``accept_event``; ``IDLE`` -> ``idle`` (not ``i_d_l_e``)."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _collect_machines(decls: list[Decl]) -> list[dict]:
    machines: list[dict] = []
    cur: dict | None = None
    for d in decls:
        if d.kind == "state":
            cur = {
                "name": d.name,
                "decl": d,
                "init": None,
                "enum": None,
                "trans": [],
                "path": d.path,
            }
            machines.append(cur)
        elif d.kind == "state_enum" and cur is not None and cur["name"] == d.name:
            cur["enum"] = d.term_str
        elif d.kind == "state_init" and cur is not None and cur["name"] == d.name:
            cur["init"] = d.term_str
        elif d.kind == "state_trans" and cur is not None and cur["name"] == d.name:
            m = _TRANS_RE.match(d.term_str or "")
            if m:
                cur["trans"].append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), d))
    return machines


def _module_root(link_path: str, roots: Sequence[Path] = ()) -> Path:
    """The module directory a ``.link`` file speaks for.

    The declaration used to be bound to ``Path(link_path).parent``, so a machine
    declared in ``<module>/core/`` scanned only ``core/`` and every state set
    elsewhere in the module was invisible -- the same blind spot F3 fixed for
    the call graph.  Two signals, nearest first per level:

    - a ``<dirname>.link`` sibling, the module-root naming convention;
    - a directory that is one of the configured scan roots.

    Falling back to the declaring file's directory keeps standalone ``.link``
    projects (no module layout) working.
    """
    start = Path(link_path).resolve().parent
    root_names = {Path(r).resolve().name for r in roots if Path(r).is_dir()}
    p = start
    while True:
        if (p / f"{p.name}.link").is_file():
            return p
        if p.name in root_names:
            return p
        if p.parent == p:
            return start
        p = p.parent


def _scan_module(module_dir: Path) -> tuple[set[str], set[str], list[tuple[str, int, str]]]:
    """Legacy string-literal machines: (events_called, state_literals, sites)."""
    events: set[str] = set()
    literals: set[str] = set()
    sites: list[tuple[str, int, str]] = []
    if not module_dir.is_dir():
        return events, literals, sites
    for py in sorted(module_dir.rglob("*.py")):
        if any(part in _SKIP_PARTS for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for _ln, call in facts_of(text).calls:
            events.add(call)
        for ln, line in enumerate(text.splitlines(), start=1):
            for m in _STATE_LIT_RE.finditer(line):
                literals.add(m.group(1))
                sites.append((str(py), ln, m.group(1)))
    return events, literals, sites


def _calls_in(module_dir: Path) -> set[str]:
    """Every call name in the module (evidence for declared transition events)."""
    calls: set[str] = set()
    if not module_dir.is_dir():
        return calls
    for py in sorted(module_dir.rglob("*.py")):
        if any(part in _SKIP_PARTS for part in py.parts):
            continue
        try:
            calls |= {c for _ln, c in facts_of(py.read_text(encoding="utf-8")).calls}
        except OSError:
            continue
    return calls


def _dotted_tail(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _enum_sites(
    module_dir: Path, enum: str
) -> tuple[set[str], set[str], list[tuple[str, int, str, bool]]]:
    """Enum machines: (referenced, targeted, sites) over ``enum.Member`` nodes.

    ``targeted`` keeps only the sites that *change* a state: a member passed to
    a call (``transition_to(TurnState.PLANNING)``) or assigned
    (``self.state = TurnState.PLANNING``).  Reads stay out of it so
    ``self.state in (TurnState.A, TurnState.B)`` is not mistaken for a reach.
    """
    referenced: set[str] = set()
    targeted: set[str] = set()
    sites: list[tuple[str, int, str, bool]] = []
    if not module_dir.is_dir():
        return referenced, targeted, sites
    for py in sorted(module_dir.rglob("*.py")):
        if any(part in _SKIP_PARTS for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if _dotted_tail(node.value) != enum:
                continue
            owner = parents.get(node)
            is_target = (
                (isinstance(owner, ast.Call) and node in owner.args)
                or isinstance(owner, ast.keyword)
                or (isinstance(owner, ast.Assign) and node is owner.value)
                or (isinstance(owner, ast.AnnAssign) and node is owner.value)
            )
            member = _snake(node.attr)
            referenced.add(member)
            if is_target:
                targeted.add(member)
            sites.append((str(py), node.lineno, member, is_target))
    return referenced, targeted, sites


def check_state(decls: list[Decl], roots: Sequence[Path] = ()) -> list[tuple]:
    machines = _collect_machines(decls)
    if not machines:
        return []
    diags: list[tuple] = []

    for mach in machines:
        decl = mach["decl"]
        init = mach["init"]
        trans = mach["trans"]
        enum = mach["enum"]
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
        module_dir = _module_root(decl.path, roots)

        if enum:
            _referenced, targeted, enum_sites = _enum_sites(module_dir, enum)
            events_called = _calls_in(module_dir)
            # E401: any referenced member must exist in the machine.
            for (fpath, fline, member, _is_target) in enum_sites:
                if member in states:
                    continue
                diags.append(
                    (
                        "E401",
                        "error",
                        f"state `{mach['name']}`: code uses `{enum}.{member.upper()}` which is "
                        f"not declared (declared: {{{', '.join(sorted(states))}}})",
                        fpath,
                        fline,
                        1,
                        "L4 state machine: every state literal must be declared",
                    )
                )
            # E403: a state the code *sets* must be reachable by a declaration.
            for (fpath, fline, member, is_target) in enum_sites:
                if not is_target or member not in states:
                    continue
                if member != init and member not in to_states:
                    diags.append(
                        (
                            "E403",
                            "error",
                            f"state `{mach['name']}`: code reaches `{member}` but no declared "
                            f"transition targets it (init={init!r}, targets: "
                            f"{{{', '.join(sorted(to_states))}}})",
                            fpath,
                            fline,
                            1,
                            "L4 state machine: reached states must be reachable by a declared transition",
                        )
                    )
            implemented = targeted
        else:
            events_called, _literals, lit_sites = _scan_module(module_dir)
            implemented = set()
            for (fpath, fline, lit) in lit_sites:
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
            implemented = {lit for (_p, _l, lit) in lit_sites}

        # E402: every declared transition must be implemented -- either the event
        # is a real call, or the target state is reached by a real site.
        #
        # Enum machines take only the second form: the event is documentation
        # (``transition_to`` is one generic method), so honouring it as evidence
        # would make every declared edge pass and the check vacuous.
        ev_snake = {_snake(c) for c in events_called}
        for (frm, ev, to, tdecl) in trans:
            if to in implemented:
                continue
            if not enum and (_snake(ev) in ev_snake or any(call_matches(c, ev) for c in events_called)):
                continue
            reason = (
                f"no site reaches `{to}`"
                if enum
                else f"no call `{ev}` and no site reaches `{to}`"
            )
            diags.append(
                (
                    "E402",
                    "error",
                    f"state `{mach['name']}` declares transition "
                    f"`{frm} --{ev}--> {to}` but nothing implements it "
                    f"({reason}) in {module_dir.name}",
                    tdecl.path,
                    tdecl.line,
                    tdecl.col,
                    "L4 state machine: declared transitions must be implemented",
                )
            )
    return diags
