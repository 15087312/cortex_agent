"""v2 L1 -- assembly / wiring layer.

``use name := module.Symbol`` declares a module instance; ``input`` /
``output`` port contracts attached to a symbol declare what it needs and
what it produces.  An ``eq`` that chains use-names (``A · B · C``) declares
an end-to-end pipeline; every adjacent pair is checked for port
compatibility.

Return ports close the last hole in that model.  A call hands its result back
to the caller, so an output atom can legitimately leave a chain without a
*downstream* consumer -- but only if the caller says it receives it:

    output Distrib: [Emit]
    accept App: [Emit]        # app 调用 Distrib 后接到 Emit

The old rule exempted whatever happened to be last in a chain (position, not
intent) and unioned consumption across *all* chains, so an atom consumed
somewhere silenced a dangling output everywhere else.  Now, per chain and per
pair ``a · b``: b's outputs must be consumed by b's successor's input **or**
by a's ``accept`` port.

Diagnostics (returned as tuples for the orchestrator to wrap):
    E101  a module's declared input is not fully provided by its predecessor
    E102  an output atom is neither consumed downstream nor accepted by the caller
    E103  adjacent modules declare disjoint port atom sets (cannot splice)
    E104  `accept` declares an atom no callee actually produces (a mute button)
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
    """Declared successor pairs, within a branch (never across ``|``).

    ``sequence_pairs`` keeps alternatives apart, so ``a · (b | c) · d`` yields
    ``a→b, a→c, b→d, c→d`` instead of the bogus ``b→c``.
    """
    return T.sequence_pairs(term)


def check_flow(decls: list[Decl]) -> list[tuple]:
    """Return ``(code, level, message, path, line, col, note)`` tuples."""
    diags: list[tuple] = []

    use_decls: dict[str, Decl] = {}  # bound use name -> declaration
    use_target: dict[str, str] = {}  # bound use name -> module.Symbol
    ports: dict[str, tuple[set[str], set[str]]] = {}  # symbol -> (inputs, outputs)
    accepts: dict[str, set[str]] = {}  # symbol -> atoms the caller takes back
    accept_decls: dict[str, Decl] = {}
    chain_decls: list[Decl] = []

    for d in decls:
        if d.kind == "use" and d.op == ":=":
            use_decls[d.name] = d
            use_target[d.name] = d.term_str or ""
        elif d.kind == "accept" and d.name:
            accepts.setdefault(d.name, set()).update(_parse_port_list(d.term_str))
            accept_decls.setdefault(d.name, d)
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
        if not d.term_str:
            continue
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

    # ---- E102: dangling outputs, judged per chain ----
    #
    # For a pair `a · b` (a calls b), b's outputs leave the chain in one of two
    # legitimate ways: forward into b's successor in the *same* chain, or back
    # to its caller through the caller's `accept` port.  Anything else is dangling.
    #
    # The rule this replaces was doubly positional: a module was exempt just for
    # being last in a chain, and `consumers_of` unioned consumption across every
    # chain, so an atom consumed in chain X silenced the same atom in chain Y.
    reported: set[tuple[str, str]] = set()
    produced_to_caller: dict[str, set[str]] = {}
    for d in chain_decls:
        if not d.term_str:
            continue
        try:
            term = parse_term(d.term_str)
        except Exception:
            continue
        pairs = [(a, b) for a, b in _neighbour_pairs(term) if a in use_decls and b in use_decls]
        if not pairs:
            continue
        successors: dict[str, set[str]] = {}
        for a, b in pairs:
            successors.setdefault(a, set()).add(b)
            sym_a = use_target.get(a, "").split(".")[-1]
            sym_b = use_target.get(b, "").split(".")[-1]
            produced_to_caller.setdefault(sym_a, set()).update(
                ports.get(sym_b, (set(), set()))[1]
            )

        # Called members (their output needs a destination) ...
        callees = {b for (_a, b) in pairs}
        # ... and the members that enter the chain: never called by another one.
        heads = [a for (a, _b) in pairs if a not in callees] or [pairs[0][0]]
        heads_ports = [
            ports.get(use_target.get(h, "").split(".")[-1])
            or accepts.get(use_target.get(h, "").split(".")[-1])
            for h in heads
        ]
        # A chain whose entry publishes no port contract makes no dataflow claim
        # (e.g. an `eq` that only documents an import edge): requiring a return
        # port there would force a declaration the caller cannot honestly make.
        if not any(heads_ports):
            continue
        predecessors: dict[str, set[str]] = {}
        for a, b in pairs:
            predecessors.setdefault(b, set()).add(a)

        # Only a callee's output needs a destination.  A chain head's `output` is
        # what the head itself produces for *its* callers -- `app` declaring
        # [Think, Write, Emit] is not a promise to hand all three to `think`.
        for elem in sorted(callees):
            sym_e = use_target.get(elem, "").split(".")[-1]
            outs_e = ports.get(sym_e, (set(), set()))[1]
            if not outs_e:
                continue
            succ = successors.get(elem, set())
            if succ:
                destination: set[str] = set()
                for nxt in succ:
                    destination |= ports.get(use_target.get(nxt, "").split(".")[-1], (set(), set()))[0]
                why = f"its successor {{{', '.join(sorted(destination))}}} does not take it"
                how = "consumed downstream"
            else:
                destination = set()
                for pred in predecessors.get(elem, set()):
                    destination |= accepts.get(use_target.get(pred, "").split(".")[-1], set())
                why = (
                    f"it ends the chain and no caller accepts it "
                    f"(accept {{{', '.join(sorted(destination))}}})"
                )
                how = "accepted by the caller (`accept Symbol: [Atom]`)"
            for atom in sorted(outs_e - destination):
                key = (elem, atom)
                if key in reported:
                    continue
                reported.add(key)
                src = use_decls[elem]
                diags.append(
                    (
                        "E102",
                        "error",
                        f"module `{elem}` (={sym_e}) output `{atom}` is dangling: {why}",
                        src.path,
                        src.line,
                        src.col,
                        f"L1 assembly: every output must be {how}",
                    )
                )

    # ---- E104: `accept` that names an atom no callee produces ----
    # Without this, `accept X: [everything]` would be a global mute button for E102.
    for sym, atoms in sorted(accepts.items()):
        impossible = atoms - produced_to_caller.get(sym, set())
        if not impossible or sym not in produced_to_caller:
            continue  # 模块不调用任何东西时，accept 无意义但也不冤报
        src = accept_decls[sym]
        diags.append(
            (
                "E104",
                "error",
                f"accept `{sym}` declares {{{', '.join(sorted(impossible))}}} but no module it "
                f"calls produces "
                f"{{{', '.join(sorted(produced_to_caller.get(sym, set())))}}}",
                src.path,
                src.line,
                src.col,
                "L1 assembly: accept may only name atoms a callee really outputs",
            )
        )

    return diags
