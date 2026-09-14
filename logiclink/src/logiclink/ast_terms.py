"""Term AST + normalize + equal for the Link language (v1).

AST is compared structurally AFTER normalization. String comparison of
terms is forbidden by spec -- always go through ``normalize`` + ``equal``.

Associativity is fixed to right-nested for ``Seq``/``Alt``/``Par``.
``|`` / ``||`` / ``.`` are never commutative: order matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

# Resolve a bare name reference (alias / eq / block name) to its RHS term.
# Returns None when the name is a plain atom with no expansion.
Resolver = Callable[[str], "Term | None"]


@dataclass(frozen=True)
class Atom:
    name: str
    args: tuple = ()


@dataclass(frozen=True)
class Seq:  # A . B
    left: "Term"
    right: "Term"


@dataclass(frozen=True)
class Alt:  # A | B (ordered choice)
    left: "Term"
    right: "Term"


@dataclass(frozen=True)
class Par:  # A || B (ordered parallel)
    left: "Term"
    right: "Term"


@dataclass(frozen=True)
class Loop:
    body: "Term"


@dataclass(frozen=True)
class Star:
    body: "Term"


@dataclass(frozen=True)
class Plus:
    body: "Term"


@dataclass(frozen=True)
class Opt:
    body: "Term"


@dataclass(frozen=True)
class Id:
    pass


Term = Union[Atom, Seq, Alt, Par, Loop, Star, Plus, Opt, Id]


def _flatten_seq(t: Term) -> list:
    if isinstance(t, Seq):
        return _flatten_seq(t.left) + _flatten_seq(t.right)
    return [t]


def _flatten_alt(t: Term) -> list:
    if isinstance(t, Alt):
        return _flatten_alt(t.left) + _flatten_alt(t.right)
    return [t]


def _flatten_par(t: Term) -> list:
    if isinstance(t, Par):
        return _flatten_par(t.left) + _flatten_par(t.right)
    return [t]


def _rebuild_right(items: list, cls):
    out = items[-1]
    for it in reversed(items[:-1]):
        out = cls(it, out)
    return out


def normalize(t: Term, resolve: Resolver | None = None, _seen: frozenset = frozenset()) -> Term:
    """Normalize a term.

    1. Expand alias / eq name references via ``resolve``.
    2. ``A . id`` / ``id . A`` -> ``A``.
    3. Re-associate ``Seq``/``Alt``/``Par`` to right-nested form.
    4. ``Loop(Loop(A))`` -> ``Loop(A)``.
    5. Never commutative.
    """
    if isinstance(t, Atom):
        if t.args == () and resolve is not None and t.name not in _seen:
            rhs = resolve(t.name)
            if rhs is not None:
                return normalize(rhs, resolve, _seen | {t.name})
        return t
    if isinstance(t, Id):
        return t
    if isinstance(t, Loop):
        body = normalize(t.body, resolve, _seen)
        if isinstance(body, Loop):
            return body
        if isinstance(body, Id):
            return body
        return Loop(body)
    if isinstance(t, (Star, Plus, Opt)):
        body = normalize(t.body, resolve, _seen)
        if isinstance(body, Id):
            return body
        return type(t)(body)
    if isinstance(t, Seq):
        left = normalize(t.left, resolve, _seen)
        right = normalize(t.right, resolve, _seen)
        items = [i for i in _flatten_seq(Seq(left, right)) if not isinstance(i, Id)]
        if not items:
            return Id()
        if len(items) == 1:
            return items[0]
        return _rebuild_right(items, Seq)
    if isinstance(t, Alt):
        left = normalize(t.left, resolve, _seen)
        right = normalize(t.right, resolve, _seen)
        items = _flatten_alt(Alt(left, right))
        if len(items) == 1:
            return items[0]
        return _rebuild_right(items, Alt)
    if isinstance(t, Par):
        left = normalize(t.left, resolve, _seen)
        right = normalize(t.right, resolve, _seen)
        items = _flatten_par(Par(left, right))
        if len(items) == 1:
            return items[0]
        return _rebuild_right(items, Par)
    raise TypeError(f"unknown term node: {t!r}")


def _struct_equal(a: Term, b: Term, wildcard: bool) -> bool:
    if isinstance(a, Atom) and isinstance(b, Atom):
        if a.name != b.name:
            return False
        if wildcard:
            return True
        return a.args == b.args
    if type(a) is not type(b):
        return False
    if isinstance(a, Id):
        return True
    if isinstance(a, (Loop, Star, Plus, Opt)):
        return _struct_equal(a.body, b.body, wildcard)  # type: ignore[union-attr]
    if isinstance(a, (Seq, Alt, Par)):
        return _struct_equal(a.left, b.left, wildcard) and _struct_equal(a.right, b.right, wildcard)  # type: ignore[union-attr]
    return False


def equal(a: Term, b: Term, resolve: Resolver | None = None, wildcard: bool = False) -> bool:
    """Structural equality after normalization.

    ``Tool`` vs ``Tool[search]`` are NOT equal unless ``wildcard`` is True
    (mirrors ``tool_wildcard`` config, default False).
    """
    return _struct_equal(normalize(a, resolve), normalize(b, resolve), wildcard)


def atoms_of(t: Term) -> set[str]:
    """All atom names occurring in a term (``id`` excluded, ``Loop`` transparent)."""
    out: set[str] = set()

    def visit(n: Term):
        if isinstance(n, Atom):
            out.add(n.name)
        elif isinstance(n, Id):
            return
        elif isinstance(n, (Loop, Star, Plus, Opt)):
            visit(n.body)
        elif isinstance(n, (Seq, Alt, Par)):
            visit(n.left)
            visit(n.right)
        else:
            raise TypeError(f"unknown term node: {n!r}")

    visit(t)
    return out


def count_loops(t: Term) -> int:
    """Number of ``Loop`` nodes in a term (used for evidence checks)."""
    n = 0

    def visit(x: Term):
        nonlocal n
        if isinstance(x, Loop):
            n += 1
            visit(x.body)
        elif isinstance(x, (Star, Plus, Opt)):
            visit(x.body)
        elif isinstance(x, (Seq, Alt, Par)):
            visit(x.left)
            visit(x.right)

    visit(t)
    return n


@dataclass(frozen=True)
class VarBinding:
    """One ``Atom(var)`` / ``Atom(v@"channel")`` / ``Atom(v:Type)`` binding in a term.

    ``kind`` is ``"var"`` for a plain variable, ``"chan"`` when a channel is
    attached (``v@"channel"``), and ``"typed"`` when an explicit type is given.
    """

    atom: str
    var: str
    channel: str | None = None
    vartype: str | None = None

    @property
    def kind(self) -> str:
        if self.channel is not None:
            return "chan"
        if self.vartype is not None:
            return "typed"
        return "var"


def var_bindings(t: Term) -> list[VarBinding]:
    """Extract every variable binding occurring in a term, in structural order.

    Only atoms carrying a ``("var", ...)`` arg participate; plain atoms and
    type-only args (``Atom[foo]``) are ignored. Loop / Star / Plus / Opt /
    Alt / Par bodies are traversed; ``Id`` contributes nothing.
    """
    out: list[VarBinding] = []

    def visit(n: Term):
        if isinstance(n, Atom):
            var = chan = vtype = None
            for k, v in n.args:
                if k == "var":
                    var = v
                elif k == "chan":
                    chan = v
                elif k == "type":
                    vtype = v
            if var is not None:
                out.append(VarBinding(n.name, var, chan, vtype))
        elif isinstance(n, Id):
            return
        elif isinstance(n, (Loop, Star, Plus, Opt)):
            visit(n.body)
        elif isinstance(n, (Seq, Alt, Par)):
            visit(n.left)
            visit(n.right)
        else:
            raise TypeError(f"unknown term node: {n!r}")

    visit(t)
    return out


def linear_atoms(t: Term) -> list[Atom]:
    """All atoms of a term in structural order (branches concatenated).

    Used by the L1 assembly checker to derive declared neighbour pairs from an
    ``eq`` such as ``MainFlow = perceive · think · memory · output``.
    """
    out: list[Atom] = []

    def visit(n: Term):
        if isinstance(n, Atom):
            out.append(n)
        elif isinstance(n, Id):
            return
        elif isinstance(n, (Loop, Star, Plus, Opt)):
            visit(n.body)
        elif isinstance(n, (Seq, Alt, Par)):
            visit(n.left)
            visit(n.right)
        else:
            raise TypeError(f"unknown term node: {n!r}")

    visit(t)
    return out


def fmt(t: Term) -> str:
    """Canonical pretty printer (right-associated, minimal parens)."""
    if isinstance(t, Atom):
        if not t.args:
            return t.name
        var = chan = vtype = None
        parts: list[str] = []
        for k, v in t.args:
            if k == "var":
                var = v
            elif k == "chan":
                chan = v
            elif k == "type":
                vtype = v
            else:
                parts.append(f"{k}={v}" if k else str(v))
        if var is not None:
            inner = var + (f'@"{chan}"' if chan is not None else "") + (f":{vtype}" if vtype is not None else "")
            if parts:
                inner += ", " + ", ".join(parts)
            return f"{t.name}({inner})"
        return f"{t.name}[{', '.join(parts)}]"
    if isinstance(t, Id):
        return "id"
    if isinstance(t, Loop):
        return f"Loop({fmt(t.body)})"
    if isinstance(t, Star):
        return f"{_fmt_post(t.body)}*"
    if isinstance(t, Plus):
        return f"{_fmt_post(t.body)}+"
    if isinstance(t, Opt):
        return f"{_fmt_post(t.body)}?"
    if isinstance(t, Seq):
        return f"{_fmt_seq(t.left)} · {_fmt_seq_right(t.right)}"
    if isinstance(t, Alt):
        return f"{fmt(t.left)} | {fmt(t.right)}"
    if isinstance(t, Par):
        return f"{fmt(t.left)} || {fmt(t.right)}"
    raise TypeError(f"unknown term node: {t!r}")


def _fmt_post(t: Term) -> str:
    if isinstance(t, (Atom, Id)) or (isinstance(t, Loop)):
        return fmt(t)
    return f"({fmt(t)})"


def _fmt_seq(t: Term) -> str:
    if isinstance(t, (Alt, Par)):
        return f"({fmt(t)})"
    return fmt(t)


def _fmt_seq_right(t: Term) -> str:
    # Right-nested Seq prints flat; Alt/Par need parens.
    if isinstance(t, (Alt, Par)):
        return f"({fmt(t)})"
    return fmt(t)
