import pytest

from logiclink import ast_terms as T
from logiclink.parser import ParseError, parse_decl, parse_term


def test_atom_and_args():
    t = parse_term("Tool[search]")
    assert t == T.Atom("Tool", (("", "search"),))
    t2 = parse_term("Guard[tool]")
    assert t2 == T.Atom("Guard", (("", "tool"),))
    t3 = parse_term("Tool[name=x]")
    assert t3 == T.Atom("Tool", (("name", "x"),))


def test_seq_operators():
    a = parse_term("Think · Tool")
    b = parse_term("Think >> Tool")
    assert a == b == T.Seq(T.Atom("Think", ()), T.Atom("Tool", ()))


def test_precedence_seq_binds_tighter_than_alt():
    t = parse_term("Tool · Observe | Emit")
    assert t == T.Alt(T.Seq(T.Atom("Tool", ()), T.Atom("Observe", ())), T.Atom("Emit", ()))
    t2 = parse_term("A || B | C")
    assert isinstance(t2, T.Alt)
    assert isinstance(t2.left, T.Par)


def test_postfix_binds_tightest():
    t = parse_term("Think* · Tool+ · Emit?")
    assert isinstance(t, T.Seq)
    assert isinstance(t.left.left, T.Star)


def test_loop_and_parens():
    t = parse_term("Loop(Think · Tool)")
    assert t == T.Loop(T.Seq(T.Atom("Think", ()), T.Atom("Tool", ())))
    t2 = parse_term("(Think · Tool) · Observe")
    assert isinstance(t2, T.Seq)


def test_id():
    assert parse_term("id") == T.Id()
    assert parse_term("Think · id") == T.Seq(T.Atom("Think", ()), T.Id())


def test_unicode_ident_and_lexicon():
    lex = {"思考": "Think", "调用工具": "Tool", "循环": "Loop"}
    t = parse_term("循环(思考 · 调用工具)", lex)
    assert t == T.Loop(T.Seq(T.Atom("Think", ()), T.Atom("Tool", ())))


def test_parse_errors():
    with pytest.raises(ParseError):
        parse_term("")
    with pytest.raises(ParseError):
        parse_term("Think ·")
    with pytest.raises(ParseError):
        parse_term("(Think")
    with pytest.raises(ParseError):
        parse_term("Think $ Tool")


def test_decl_forms():
    d = parse_decl("eq ReAct = Loop(Think · Act)", "a.link", 1)
    assert (d.kind, d.name, d.term_str) == ("eq", "ReAct", "Loop(Think · Act)")
    d = parse_decl("alias X = Think", "a.link", 2)
    assert d.kind == "alias"
    d = parse_decl("atom Think", "a.link", 3)
    assert d.kind == "atom"
    d = parse_decl("export Reason", "a.link", 4)
    assert d.kind == "export"
    d = parse_decl("use Reason", "a.link", 5)
    assert d.kind == "use"
    d = parse_decl("block ReAct", "a.py", 6)
    assert d.kind == "block" and d.term_str is None
    d = parse_decl("block Act = Guard[tool] · Tool", "a.py", 7)
    assert d.op == "=" and d.term_str == "Guard[tool] · Tool"
    d = parse_decl("block X : Think · Tool", "a.py", 8)
    assert d.op == ":" and d.term_str == "Think · Tool"
    d = parse_decl("block Y @ file:reason/react.py fn:react", "a.link", 9)
    assert d.hook_file == "reason/react.py" and d.hook_fn == "react" and d.term_str is None
    with pytest.raises(ParseError):
        parse_decl("nonsense !!!", "a.link", 10)
