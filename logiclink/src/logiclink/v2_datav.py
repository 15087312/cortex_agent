"""v2 L2 -- dataflow layer (variable bindings).

Term syntax ``Atom(var)`` / ``Atom(v@"channel")`` / ``Atom(v:Type)`` binds a
variable to an atom.  Producers (Think / Retrieve / ...) define a variable,
consumers (Write / Guard / ...) use it.  Within every eq / alias / block
term a definition-use graph is checked; channel references are resolved
across the whole project.

Diagnostics (returned as tuples):
    E201  a variable is consumed before anything produced it
    E202  a produced variable is never consumed
    E203  explicit types disagree between producer and consumer
    E204  a channel is subscribed but never published
"""

from __future__ import annotations

from . import ast_terms as T
from .config import Config
from .parser import Decl, parse_term


def _decl_term(d: Decl) -> T.Term | None:
    if d.term_str is None:
        return None
    try:
        return parse_term(d.term_str)
    except Exception:
        return None  # E001 already reported by the v1 pipeline


def check_datav(decls: list[Decl], cfg: Config) -> list[tuple]:
    diags: list[tuple] = []
    producers = set(cfg.dataflow.get("producers", []))
    consumers = set(cfg.dataflow.get("consumers", []))
    if not producers and not consumers:
        return diags

    # Global channel registry: publish (producer with @chan) vs subscribe.
    published: set[str] = set()
    subscribed: set[str] = set()
    sub_source: dict[str, Decl] = {}

    for d in decls:
        if d.kind not in ("eq", "alias", "block"):
            continue
        term = _decl_term(d)
        if term is None:
            continue
        produced: set[str] = set()
        consumed: set[str] = set()
        produced_types: dict[str, set[str]] = {}
        consumed_types: dict[str, set[str]] = {}
        for vb in T.var_bindings(term):
            v = vb.var
            if vb.atom in producers:
                produced.add(v)
                if vb.vartype:
                    produced_types.setdefault(v, set()).add(vb.vartype)
                if vb.channel is not None:
                    published.add(vb.channel)
            else:
                # Consumers and unclassified atoms both *use* the variable.
                consumed.add(v)
                if vb.vartype:
                    consumed_types.setdefault(v, set()).add(vb.vartype)
                if vb.channel is not None:
                    subscribed.add(vb.channel)
                    sub_source.setdefault(vb.channel, d)

        # E201: consumed before produced -- order-sensitive walk.  A consumer
        # that sees a variable with no earlier producer in this term is an
        # undefined use; a consumer appearing before its producer is too.
        seen_produced: set[str] = set()
        for vb in T.var_bindings(term):
            v = vb.var
            if vb.atom in producers:
                seen_produced.add(v)
                continue
            if v not in seen_produced:
                diags.append(
                    (
                        "E201",
                        "error",
                        f"variable `{v}` is used by `{vb.atom}` in {d.kind} `{d.name}` "
                        f"but no earlier atom produced it",
                        d.path,
                        d.line,
                        d.col,
                        "L2 dataflow: define before use",
                    )
                )

        # E202: produced but never consumed.
        for v in sorted(produced - consumed):
            diags.append(
                (
                    "E202",
                    "error",
                    f"variable `{v}` produced by {d.kind} `{d.name}` is never consumed",
                    d.path,
                    d.line,
                    d.col,
                    "L2 dataflow: every produced variable must be consumed",
                )
            )

        # E203: explicit type disagreement (per variable, per term).
        for v in sorted(set(produced_types) | set(consumed_types)):
            pt = produced_types.get(v, set())
            ct = consumed_types.get(v, set())
            if pt and ct and not (pt & ct):
                diags.append(
                    (
                        "E203",
                        "error",
                        f"variable `{v}` in {d.kind} `{d.name}` has producer type "
                        f"{{{', '.join(sorted(pt))}}} but consumer type {{{', '.join(sorted(ct))}}}",
                        d.path,
                        d.line,
                        d.col,
                        "L2 dataflow: producer/consumer types must agree",
                    )
                )

    # E204: subscribed channel with no publisher anywhere in the project.
    for chan in sorted(subscribed):
        if chan not in published:
            src = sub_source.get(chan)
            if src is None:
                continue
            diags.append(
                (
                    "E204",
                    "error",
                    f"channel `{chan}` is subscribed but no module publishes it "
                    f"(no producer binds a variable via `@{chan}`)",
                    src.path,
                    src.line,
                    src.col,
                    "L2 dataflow: every subscribed channel needs a publisher",
                )
            )

    return diags
