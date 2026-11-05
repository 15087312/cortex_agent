from logiclink import ast_terms as T
from logiclink.parser import parse_term


def eq(a: str, b: str, **kw) -> bool:
    return T.equal(parse_term(a), parse_term(b), **kw)


def test_id_elimination():
    assert eq("Think · id", "Think")
    assert eq("id · Think", "Think")


def test_associativity_fixed():
    assert eq("(A · B) · C", "A · (B · C)")
    assert eq("(A | B) | C", "A | (B | C)")


def test_loop_idempotent():
    assert eq("Loop(Loop(Think))", "Loop(Think)")


def test_order_matters():
    assert not eq("Think · Tool", "Tool · Think")
    assert not eq("A | B", "B | A")
    assert not eq("A || B", "B || A")


def test_args_strict_by_default():
    assert not eq("Tool", "Tool[search]")
    assert eq("Tool", "Tool[search]", wildcard=True)
    assert eq("Tool[a]", "Tool[b]", wildcard=True)


def test_alias_expansion():
    resolve = lambda n: parse_term("Think · Tool") if n == "ReAct" else None  # noqa: E731
    assert T.equal(parse_term("ReAct"), parse_term("Loop(Think · Tool)").body, resolve)
    assert not T.equal(parse_term("ReAct"), parse_term("Think"), resolve)


def test_fmt_roundtrip():
    for s in ["Loop(Think · Guard · (Tool · Observe | Emit))", "A* · B+ · C?", "Tool[x=y] · id"]:
        t = T.normalize(parse_term(s))
        assert T.equal(parse_term(s), parse_term(T.fmt(t)))


def pairs(s: str) -> list[tuple[str, str]]:
    return T.sequence_pairs(parse_term(s))


def test_sequence_pairs_linear():
    assert pairs("A · B · C") == [("A", "B"), ("B", "C")]


def test_sequence_pairs_never_cross_an_alternative():
    """Alternatives are not successors -- `B | C` must not produce `B → C`."""
    assert pairs("A · (B | C)") == [("A", "B"), ("A", "C")]
    assert pairs("A · (B | C) · D") == [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
    assert ("B", "C") not in pairs("A · (B | C) · D")


def test_sequence_pairs_nested_branch():
    got = pairs("A · (B · C | D)")
    assert ("B", "C") in got
    assert ("A", "B") in got and ("A", "D") in got
    assert ("C", "D") not in got


def test_sequence_pairs_loop_body():
    got = pairs("Loop(Retrieve · Think · Guard · (Write | Emit))")
    assert ("Think", "Guard") in got
    assert ("Guard", "Write") in got and ("Guard", "Emit") in got
    assert ("Write", "Emit") not in got


def test_atoms_of():
    t = parse_term("Loop(Think · Guard · (Tool · Observe | Emit))")
    assert T.atoms_of(t) == {"Think", "Guard", "Tool", "Observe", "Emit"}
    assert T.atoms_of(parse_term("id")) == set()
