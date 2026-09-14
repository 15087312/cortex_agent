"""v2 L3 -- call-graph reconciliation.

The declared graph comes from ``use`` wiring: every adjacent pair inside an
``eq`` (``A · B``) is a declared edge between the underlying modules.  The
real graph comes from Python imports: a module's ``.py`` files importing
another wired module's package path is treated as a real dependency.

Diagnostics (returned as tuples):
    E301  an edge is declared (A → B) but no real import exists
    E302  a real import edge exists between wired modules but no eq/use mentions it
    E303  the declared direction is opposite to the real one (real is B → A)

``render_graph`` produces a mermaid ``flowchart LR`` for ``logiclink --graph``.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import ast_terms as T
from .evidence import facts_of
from .parser import Decl, parse_term

_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "tests"}


def _symbol_decl(decls: list[Decl], symbol: str) -> Decl | None:
    for d in decls:
        if d.kind in ("export", "eq", "block"):
            last = d.name.split(".")[-1]
            if last == symbol:
                return d
    return None


def _module_dirs(decls: list[Decl], use_target: dict[str, str]) -> dict[str, Path]:
    """module-name -> module directory (from the wired symbol's declaration)."""
    out: dict[str, Path] = {}
    for name, target in use_target.items():
        module = target.split(".")[0] if "." in target else name
        symbol = target.split(".")[-1]
        d = _symbol_decl(decls, symbol)
        if d is None:
            continue
        out.setdefault(module, Path(d.path).resolve().parent)
    return out


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
                for b in names:
                    if b == a:
                        continue
                    if imp == f"modules.{b}" or imp.startswith(f"modules.{b}.") or imp == b or imp.startswith(b + "."):
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
        atoms = [a.name for a in T.linear_atoms(term)]
        for i in range(len(atoms) - 1):
            a, b = atoms[i], atoms[i + 1]
            if a in use_target and b in use_target:
                edges.add((a, b))
                source.setdefault((a, b), d)
    return edges, source


def _resolve_edges(
    decls: list[Decl], use_target: dict[str, str]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], dict[tuple[str, str], Decl], dict[tuple[str, str], tuple[str, int]]]:
    dirs = _module_dirs(decls, use_target)
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


def check_graph(decls: list[Decl]) -> list[tuple]:
    use_target = {d.name: (d.term_str or "") for d in decls if d.kind == "use" and d.op == ":="}
    if not use_target:
        return []
    declared, real, dsource, revidence = _resolve_edges(decls, use_target)
    diags: list[tuple] = []

    for (ma, mb) in sorted(declared):
        src = dsource.get((ma, mb))
        if src is None:
            continue
        if (mb, ma) in real and (ma, mb) not in real:
            diags.append(
                (
                    "E303",
                    "error",
                    f"declared wiring {ma} → {mb} is opposite to the real dependency "
                    f"(real code imports {mb} → {ma})",
                    src.path,
                    src.line,
                    src.col,
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
                    src.path,
                    src.line,
                    src.col,
                    "L3 call graph: declared edges must exist in real code",
                )
            )

    for (ma, mb) in sorted(real):
        if (ma, mb) in declared:
            continue
        if (mb, ma) in declared and (ma, mb) not in declared:
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


def render_graph(decls: list[Decl], drift_only: bool = False) -> str:
    """Mermaid ``flowchart LR`` of declared vs real edges between wired modules."""
    use_target = {d.name: (d.term_str or "") for d in decls if d.kind == "use" and d.op == ":="}
    if not use_target:
        return "flowchart LR\n  %% no `use name := module.Symbol` declarations found\n"
    module_of = {n: (t.split(".")[0] if "." in t else n) for n, t in use_target.items()}
    declared, real, dsource, revidence = _resolve_edges(decls, use_target)

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
        if (ma, mb) in shown or (mb, ma) in shown:
            continue
        if drift_only:
            fpath, fline = revidence.get((ma, mb), ("", 0))
            tag = f"真实未声明(E302)@{Path(fpath).name}:{fline}" if fpath else "真实未声明(E302)"
            lines.append(f"  {ma} -.->|{tag}| {mb}")
        elif (ma, mb) not in declared:
            lines.append(f"  {ma} -.->|真实未声明| {mb}")
        shown.add((ma, mb))
    if len(lines) == 1:
        lines.append("  %% no edges between wired modules")
    return "\n".join(lines) + "\n"
