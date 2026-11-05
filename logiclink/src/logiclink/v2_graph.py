"""v2 L3 -- call-graph reconciliation.

The declared graph comes from ``use`` wiring: every adjacent pair inside an
``eq`` (``A · B``) is a declared edge between the underlying modules.  The
real graph comes from Python imports: a module's ``.py`` files importing
another wired module's package path is treated as a real dependency.

Diagnostics (returned as tuples):
    E301  an edge is declared (A → B) but no real import exists
    E302  a real import edge exists between wired modules but no eq/use mentions it
    E303  the declared direction is opposite to the real one (real is B → A)
    E304  the real import graph contains a dependency cycle (a strongly
          connected component of wired modules larger than one)
    W304  same, but the component is whitelisted in ``[arch] allow_cycles``

Cycles are computed from real imports, not from declarations: writing A → B and
B → A into two ``eq``s documents a cycle, it does not remove it.  Each component
is reported once, and the edges inside it are not also reported as E302.

Before E304 existed, mutual edges were silently invisible: the E302 loop skipped
any edge whose reverse was declared (``# already reported as E303``), but the
E303 branch only fires when the reverse is real *and the forward is not* -- so
when both directions existed, neither diagnostic fired.

``render_graph`` produces a mermaid ``flowchart LR`` for ``logiclink --graph``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from . import ast_terms as T
from .evidence import facts_of
from .parser import Decl, parse_term

_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "tests"}


def _symbol_decl(decls: list[Decl], module: str, symbol: str) -> Decl | None:
    """The declaration of ``symbol`` that belongs to ``module``.

    Matching on the last name segment alone made the module segment of
    ``use n := module.Symbol`` unverifiable: with two modules exporting the same
    symbol the first declaration won, and a symbol that lived in another module
    resolved as if it were this one's.  ``check.run`` reports that mismatch as
    E005; this function returns the module-owned declaration when there is one
    and only falls back to the bare name match so the scan root does not vanish
    (an already-reported error must not also delete the module from the graph).
    """
    fallback: Decl | None = None
    for d in decls:
        if d.kind not in ("export", "eq", "block"):
            continue
        if d.name.split(".")[-1] != symbol:
            continue
        if d.name.startswith(module + ".") or any(
            part == module for part in Path(d.path).parent.parts
        ):
            return d
        if fallback is None:
            fallback = d
    return fallback


def _module_dirs(
    decls: list[Decl], use_target: dict[str, str], roots: Sequence[Path] = ()
) -> dict[str, Path]:
    """module-name -> module directory.

    The module root is the scan root whose directory name matches the ``use``
    target's module segment.  Falling back to "the directory of the file that
    declares the symbol" is what made scanning silently shrink when a symbol
    moved into ``module/core/``: imports from the rest of the module vanished
    and produced phantom E301 / missing E302.  The symbol is still what proves
    the module is wired (and E005 covers a dangling one); it no longer decides
    the scan width.
    """
    by_name: dict[str, Path] = {}
    for r in roots:
        p = Path(r)
        if p.is_dir():
            by_name.setdefault(p.name, p.resolve())
    out: dict[str, Path] = {}
    for name, target in use_target.items():
        module = target.split(".")[0] if "." in target else name
        symbol = target.split(".")[-1]
        d = _symbol_decl(decls, module, symbol)
        if module in by_name:
            out.setdefault(module, by_name[module])
            continue
        if d is None:
            continue
        out.setdefault(module, Path(d.path).resolve().parent)
    return out


def _relative_target(imp: str, source: Path) -> Path | None:
    """Filesystem target of a relative import (``..beta`` from ``pkg/core/x.py``).

    ``level`` dots mean "the package of this file", ``level - 1`` goes up.
    Resolving on paths (not on names) keeps ``from .x`` from being mistaken for
    a top-level module ``x``: an intra-package import must never become a
    cross-module edge.
    """
    level = len(imp) - len(imp.lstrip("."))
    rest = imp.lstrip(".")
    base = source.parent
    for _ in range(max(0, level - 1)):
        if base.parent == base:
            return None
        base = base.parent
    return base / Path(*rest.split(".")) if rest else base


def _under(path: Path, directory: Path) -> bool:
    try:
        return path == directory or path.is_relative_to(directory)
    except (OSError, ValueError):
        return False


def _real_edges(module_dirs: dict[str, Path]) -> tuple[set[tuple[str, str]], dict[tuple[str, str], tuple[str, int]]]:
    """(moduleA, moduleB) edges from real imports, with (file, line) evidence."""
    edges: set[tuple[str, str]] = set()
    evidence: dict[tuple[str, str], tuple[str, int]] = {}
    names = list(module_dirs)
    for a in names:
        base = module_dirs[a]
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            if any(part in _SKIP_DIRS for part in py.parts):
                continue
            try:
                facts = facts_of(py.read_text(encoding="utf-8"))
            except OSError:
                continue
            for ln, imp in facts.imports:
                target = _relative_target(imp, py) if imp.startswith(".") else None
                for b in names:
                    if b == a:
                        continue
                    hit = _under(target, module_dirs[b]) if target is not None else (
                        imp == f"modules.{b}"
                        or imp.startswith(f"modules.{b}.")
                        or imp == b
                        or imp.startswith(b + ".")
                    )
                    if hit:
                        edge = (a, b)
                        edges.add(edge)
                        evidence.setdefault(edge, (str(py), ln))
    return edges, evidence


def _declared_edges(decls: list[Decl], use_target: dict[str, str]) -> tuple[set[tuple[str, str]], dict[tuple[str, str], Decl]]:
    edges: set[tuple[str, str]] = set()
    source: dict[tuple[str, str], Decl] = {}
    for d in decls:
        if d.kind != "eq" or not d.term_str:
            continue
        try:
            term = parse_term(d.term_str)
        except Exception:
            continue
        # Successor pairs stay inside a branch: an alternative is not a
        # successor, so `a · (b | c)` must not declare `b → c`.
        for a, b in T.sequence_pairs(term):
            if a in use_target and b in use_target:
                edges.add((a, b))
                source.setdefault((a, b), d)
    return edges, source


def _resolve_edges(
    decls: list[Decl], use_target: dict[str, str], roots: Sequence[Path] = ()
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], dict[tuple[str, str], Decl], dict[tuple[str, str], tuple[str, int]]]:
    dirs = _module_dirs(decls, use_target, roots)
    declared, dsource = _declared_edges(decls, use_target)
    # Map use-name edges to module-name edges.
    module_of = {n: (t.split(".")[0] if "." in t else n) for n, t in use_target.items()}
    declared_mod: set[tuple[str, str]] = set()
    dmod_source: dict[tuple[str, str], Decl] = {}
    for (a, b) in declared:
        ma, mb = module_of.get(a, a), module_of.get(b, b)
        if ma not in dirs or mb not in dirs:
            continue
        declared_mod.add((ma, mb))
        dmod_source.setdefault((ma, mb), dsource[(a, b)])
    real, revidence = _real_edges(dirs)
    return declared_mod, real, dmod_source, revidence


def _cycles(real: set[tuple[str, str]]) -> list[list[str]]:
    """Strongly connected components with more than one module (Tarjan).

    A component of size > 1 is a dependency cycle: every module in it can reach
    every other one, so the pair is mutual and no ordering can be declared that
    removes the coupling.  Self-edges cannot occur (``_real_edges`` skips a
    module importing itself).
    """
    nodes: set[str] = set()
    adj: dict[str, set[str]] = {}
    for a, b in real:
        nodes.update((a, b))
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set())

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comps: list[list[str]] = []
    counter = 0

    for root in sorted(nodes):
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, pos = work[-1]
            if pos == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = sorted(adj[node])
            pushed = False
            while pos < len(neighbours):
                nxt = neighbours[pos]
                pos += 1
                if nxt not in index:
                    work[-1] = (node, pos)
                    work.append((nxt, 0))
                    pushed = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if pushed:
                continue
            work.pop()
            if low[node] == index[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    comps.append(sorted(comp))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sorted(comps)


def _cycle_path(comp: list[str], real: set[tuple[str, str]]) -> list[str]:
    """A concrete cycle through ``comp[0]``, e.g. ``[a, b, a]`` for the message."""
    members = set(comp)
    adj = {n: sorted({b for a, b in real if a == n and b in members}) for n in comp}
    start = comp[0]
    path = [start]
    seen = {start}

    def walk(node: str) -> bool:
        for nxt in adj[node]:
            if nxt == start and len(path) > 1:
                return True
            if nxt in seen:
                continue
            seen.add(nxt)
            path.append(nxt)
            if walk(nxt):
                return True
            path.pop()
            seen.discard(nxt)
        return False

    return path + [start] if walk(start) else comp + [comp[0]]


def _cycle_edges(real: set[tuple[str, str]], allow_cycles=()) -> dict[tuple[str, str], str]:
    """Real edge -> diagnostic code (``E304``/``W304``) for edges inside a cycle."""
    allowed = {frozenset(c) for c in allow_cycles}
    out: dict[tuple[str, str], str] = {}
    for comp in _cycles(real):
        members = set(comp)
        code = "W304" if frozenset(comp) in allowed else "E304"
        for (a, b) in real:
            if a in members and b in members:
                out[(a, b)] = code
    return out


def check_graph(decls: list[Decl], allow_cycles=(), roots: Sequence[Path] = ()) -> list[tuple]:
    use_target = {d.name: (d.term_str or "") for d in decls if d.kind == "use" and d.op == ":="}
    if not use_target:
        return []
    declared, real, dsource, revidence = _resolve_edges(decls, use_target, roots)
    allowed = {frozenset(c) for c in allow_cycles}
    in_cycle = _cycle_edges(real, allow_cycles)
    diags: list[tuple] = []

    # E304/W304: a strongly connected component is a dependency cycle.  Reported
    # once per component (not per edge) and independent of what is declared --
    # writing the cycle into two `eq`s documents it, it does not remove it.
    for comp in _cycles(real):
        members = set(comp)
        path = _cycle_path(comp, real)
        allowed_here = frozenset(comp) in allowed
        src = next(
            (
                revidence[(a, b)]
                for (a, b) in sorted(real)
                if a in members and b in members and (a, b) in revidence
            ),
            ("", 1),
        )
        diags.append(
            (
                "W304" if allowed_here else "E304",
                "warning" if allowed_here else "error",
                f"dependency cycle: {' → '.join(path)}"
                + (" (allowed by `[arch] allow_cycles`)" if allowed_here else ""),
                src[0],
                src[1],
                1,
                "L3 call graph: modules must form a DAG; break the cycle or whitelist it "
                "in `[arch] allow_cycles`",
            )
        )

    for (ma, mb) in sorted(declared):
        decl_src = dsource.get((ma, mb))
        if decl_src is None:
            continue
        if (mb, ma) in real and (ma, mb) not in real:
            diags.append(
                (
                    "E303",
                    "error",
                    f"declared wiring {ma} → {mb} is opposite to the real dependency "
                    f"(real code imports {mb} → {ma})",
                    decl_src.path,
                    decl_src.line,
                    decl_src.col,
                    "L3 call graph: declared direction must match real calls",
                )
            )
        elif (ma, mb) not in real:
            diags.append(
                (
                    "E301",
                    "error",
                    f"declared wiring {ma} → {mb} has no real call: "
                    f"no file in the {ma} module imports the {mb} module",
                    decl_src.path,
                    decl_src.line,
                    decl_src.col,
                    "L3 call graph: declared edges must exist in real code",
                )
            )

    for (ma, mb) in sorted(real):
        if (ma, mb) in declared:
            continue
        if (ma, mb) in in_cycle:
            continue  # reported once as E304/W304 for the whole component
        # The E303 branch above reports the declared pair (mb, ma) when its own
        # real edge is missing and the reverse (ma, mb) is real.  This loop sees
        # that same pair from the undeclared side, so skip exactly when E303 owns
        # it: reverse declared AND reverse not real.
        #
        # The old guard tested `(ma, mb) not in real`, which is vacuous in a loop
        # over `real` -- so it skipped *every* pair whose reverse was declared,
        # including mutual edges (A→B and B→A both real).  A mutual pair is a
        # dependency cycle that neither E302 nor E303 described: it was invisible.
        if (mb, ma) in declared and (mb, ma) not in real:
            continue  # already reported as E303
        fpath, fline = revidence.get((ma, mb), ("", 0))
        diags.append(
            (
                "E302",
                "error",
                f"real call {ma} → {mb} exists but no eq/use wiring mentions it",
                fpath,
                fline,
                1,
                "L3 call graph: real calls must be declared (use/eq)",
            )
        )
    return diags


def render_graph(
    decls: list[Decl], drift_only: bool = False, allow_cycles=(), roots: Sequence[Path] = ()
) -> str:
    """Mermaid ``flowchart LR`` of declared vs real edges between wired modules."""
    use_target = {d.name: (d.term_str or "") for d in decls if d.kind == "use" and d.op == ":="}
    if not use_target:
        return "flowchart LR\n  %% no `use name := module.Symbol` declarations found\n"
    declared, real, _dsource, revidence = _resolve_edges(decls, use_target, roots)
    in_cycle = _cycle_edges(real, allow_cycles)

    lines = ["flowchart LR"]
    shown: set[tuple[str, str]] = set()
    for (ma, mb) in sorted(declared):
        real_ok = (ma, mb) in real
        reverse = (mb, ma) in real and not real_ok
        if reverse:
            style = "x-- 方向相反(E303) -->"
        elif real_ok:
            style = "-->|真实✓|"
        else:
            style = "-.->|声明无真实(E301)|"
        lines.append(f"  {ma} {style} {mb}")
        shown.add((ma, mb))
    for (ma, mb) in sorted(real):
        if (ma, mb) in shown:
            continue
        # A declared-but-unreal reverse is drawn by the declared pass as E303:
        # that line already stands for this real edge, so do not draw it twice.
        # This mirrors the checker exactly -- the same condition guards E302 at
        # ``check_graph`` (``(mb, ma) in declared and (mb, ma) not in real``).
        # Skipping on "reverse is declared" alone would hide a mutual pair, which
        # the checker now reports as E302.
        if (mb, ma) in declared and (mb, ma) not in real:
            continue
        if drift_only:
            fpath, fline = revidence.get((ma, mb), ("", 0))
            where = f"@{Path(fpath).name}:{fline}" if fpath else ""
            if (ma, mb) in in_cycle:
                tag = f"环{in_cycle[(ma, mb)]}{where}"
            else:
                tag = f"真实未声明(E302){where}"
            lines.append(f"  {ma} -.->|{tag}| {mb}")
        elif (ma, mb) not in declared:
            tag = f"环{in_cycle[(ma, mb)]}" if (ma, mb) in in_cycle else "真实未声明"
            lines.append(f"  {ma} -.->|{tag}| {mb}")
        shown.add((ma, mb))
    if len(lines) == 1:
        lines.append("  %% no edges between wired modules")
    return "\n".join(lines) + "\n"
