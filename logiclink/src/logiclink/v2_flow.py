"""v2 L1 -- assembly / wiring layer.

``use name := module.Symbol`` declares a module instance; ``input`` /
``output`` port contracts attached to a symbol declare what it needs and
what it produces.  An ``eq`` that chains use-names (``A · B · C``) declares
an end-to-end pipeline; every adjacent pair is checked for port
compatibility.

Diagnostics (returned as tuples for the orchestrator to wrap):
    E101  a module's declared input is not fully provided by its predecessor
    E102  a module's whole output set is consumed by nothing (dangling)
    E103  adjacent modules declare disjoint port atom sets (cannot splice)
"""

from __future__ import annotations

import re

from . import ast_terms as T
from .parser import Decl, parse_term


def _parse_port_list(raw: str | None) -> set[str]:
    if not raw:
        return set()
    m = re.match(r"\[\s*(.*?)\s*\]\s*$", raw, re.DOTALL)
    if not m:
        return set()
    body = m.group(1).strip()
    if not body:
        return set()
    return {x.strip() for x in body.split(",") if x.strip()}


def _neighbour_pairs(term: T.Term) -> list[tuple[str, str]]:
    """All adjacent atom pairs of a term, branch bodies concatenated in order."""
    atoms = T.linear_atoms(term)
    return [(atoms[i].name, atoms[i + 1].name) for i in range(len(atoms) - 1)]


def check_flow(decls: list[Decl]) -> list[tuple]:
    """Return ``(code, level, message, path, line, col, note)`` tuples."""
    diags: list[tuple] = []

    use_decls: dict[str, Decl] = {}  # bound use name -> declaration
    use_target: dict[str, str] = {}  # bound use name -> module.Symbol
    ports: dict[str, tuple[set[str], set[str]]] = {}  # symbol -> (inputs, outputs)
    chain_decls: list[Decl] = []

    for d in decls:
        if d.kind == "use" and d.op == ":=":
            use_decls[d.name] = d
            use_target[d.name] = d.term_str or ""
        elif d.kind in ("input", "output") and d.name:
            ins, outs = ports.get(d.name, (set(), set()))
            parsed = _parse_port_list(d.term_str)
            if d.kind == "input":
                ins |= parsed
            else:
                outs |= parsed
            ports[d.name] = (ins, outs)
        elif d.kind == "eq" and d.term_str:
            chain_decls.append(d)

    if not use_decls:
        return diags

    # Declared pipeline edges: every adjacent pair in every eq that references
    # at least two bound use names.
    declared_pairs: set[tuple[str, str]] = set()
    pair_source: dict[tuple[str, str], Decl] = {}
    chain_modules: set[str] = set()
    for d in chain_decls:
        try:
            term = parse_term(d.term_str)
        except Exception:
            continue  # E001 already reported by the v1 pipeline
        for a, b in _neighbour_pairs(term):
            if a not in use_decls or b not in use_decls:
                continue
            declared_pairs.add((a, b))
            pair_source.setdefault((a, b), d)
            chain_modules.add(a)
            chain_modules.add(b)

    if not declared_pairs:
        return diags

    # ---- E101 / E103: per adjacent pair ----
    for (a, b) in sorted(declared_pairs):
        sym_a = use_target.get(a, "").split(".")[-1]
        sym_b = use_target.get(b, "").split(".")[-1]
        pa = ports.get(sym_a)
        pb = ports.get(sym_b)
        if pa is None or pb is None:
            continue
        ins_b, outs_a = pb[0], pa[1]
        if ins_b and outs_a:
            missing = sorted(ins_b - outs_a)
            if not (outs_a & ins_b):
                # Disjoint ports are E103, not also "every input missing" E101:
                # one broken splice should produce one diagnostic.
                src = pair_source[(a, b)]
                diags.append(
                    (
                        "E103",
                        "error",
                        f"port mismatch between `{a}` (={sym_a}) output {{{', '.join(sorted(outs_a))}}} "
                        f"and `{b}` (={sym_b}) input {{{', '.join(sorted(ins_b))}}}",
                        src.path,
                        src.line,
                        src.col,
                        "L1 assembly: adjacent modules share no port atoms",
                    )
                )
            elif missing:
                src = pair_source[(a, b)]
                diags.append(
                    (
                        "E101",
                        "error",
                        f"use `{b}` (={sym_b}) declares input {{{', '.join(missing)}}} "
                        f"not provided by `{a}` (={sym_a}) output {{{', '.join(sorted(outs_a))}}}",
                        src.path,
                        src.line,
                        src.col,
                        "L1 assembly: predecessor output must satisfy successor input",
                    )
                )

    # ---- E102: dangling outputs (whole output set consumed by nothing) ----
    has_successor = {a for (a, _b) in declared_pairs}
    consumers_of: dict[str, set[str]] = {}
    for (a, b) in declared_pairs:
        sym_b = use_target.get(b, "").split(".")[-1]
        ins_b = ports.get(sym_b, (set(), set()))[0]
        consumers_of.setdefault(a, set()).update(ins_b)

    for name in sorted(chain_modules):
        if name not in use_decls:
            continue
        if name not in has_successor:
            continue  # terminal module: nothing after it, no dangling check
        sym = use_target.get(name, "").split(".")[-1]
        outs = ports.get(sym, (set(), set()))[1]
        if not outs:
            continue
        consumed = consumers_of.get(name, set())
        if not (outs - consumed):
            continue
        src = use_decls[name]
        diags.append(
            (
                "E102",
                "error",
                f"module `{name}` (={sym}) output {{{', '.join(sorted(outs - consumed))}}} "
                f"is dangling (no successor consumes it)",
                src.path,
                src.line,
                src.col,
                "L1 assembly: every declared output must be consumed downstream",
            )
        )

    return diags
