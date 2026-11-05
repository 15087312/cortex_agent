"""Renderer helpers: pure layout / grouping functions.

The HTML views were only smoke-tested ("a file was written").  These tests pin
the deterministic pieces they rely on -- layer assignment, module discovery,
block spans and eq grouping -- without asserting on markup.
"""

from pathlib import Path

from logiclink.evidence import FuncSpan
from logiclink.viz import _rel
from logiclink.viz_all import _layer_of, _module_map
from logiclink.viz_detail import _block_span, _eq_of_block, _l1_layout


def test_rel_accepts_path_and_str(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _rel("a/b.py") == "a/b.py"
    assert _rel(Path("a/b.py")) == "a/b.py"
    assert isinstance(_rel(Path("a.py")), str)


def test_layer_of_thresholds():
    assert _layer_of("hub", {"hub": 0}, {"hub": 5}) == 0
    assert _layer_of("leaf", {"leaf": 3}, {"leaf": 1}) == 2
    assert _layer_of("middle", {"middle": 2}, {"middle": 2}) == 1
    # 3 consumers but more than one outgoing edge -> not a sink.
    assert _layer_of("x", {"x": 3}, {"x": 2}) == 1


def test_module_map_only_existing_dirs(tmp_path):
    (tmp_path / "modules" / "alpha").mkdir(parents=True)
    (tmp_path / "app").mkdir()
    mods = _module_map(tmp_path, ["modules/alpha", "app", "missing"])
    assert set(mods) == {"modules.alpha", "app"}
    assert mods["modules.alpha"]["short"] == "alpha"
    assert mods["app"]["dir"] == (tmp_path / "app").resolve()


def test_l1_layout_places_hubs_and_sinks():
    mods = {k: {"short": k, "root": k} for k in "abcdefg"}
    real = {("a", x) for x in "bcdef"} | {("b", "g"), ("c", "g"), ("d", "g")}
    laid, edges, _w, _h = _l1_layout(mods, real, {("a", "b"): [("B", "sym", "Atom")]})

    layers = {n["long"]: n["layer"] for n in laid}
    assert layers["a"] == 0, "5 outgoing edges -> top row"
    assert layers["g"] == 2, "3 incoming, 0 outgoing -> bottom row"
    assert layers["b"] == 1

    positions = [(n["x"], n["y"]) for n in laid]
    assert len(set(positions)) == len(positions), "no two nodes may overlap"

    ab = [e for e in edges if (e["from_long"], e["to_long"]) == ("a", "b")]
    assert len(ab) == 1
    assert "B" in ab[0]["label"] and "[Atom]" in ab[0]["label"]


def test_block_span_follows_decorator_to_function():
    text = "# @link:block B\n\nasync def f():\n    return 1\n"
    funcs = [FuncSpan("f", 3, 4, True)]
    assert _block_span(text, 1, funcs) == (1, 4)


def test_block_span_without_function_is_single_line():
    assert _block_span("x = 1\n", 1, []) == (1, 1)


def test_eq_of_block_prefix_and_atom_fallback():
    index = {"m": {"App": ["Think", "Guard"], "Other": ["Emit"]}}
    # name mirrors the eq
    assert _eq_of_block("App_Think", "Think", "m", index) == "App"
    # exact eq name
    assert _eq_of_block("Other", "Emit", "m", index) == "Other"
    # name does not mirror any eq -> atom fallback (this used to be dead code)
    assert _eq_of_block("Foo", "Emit", "m", index) == "Other"
    assert _eq_of_block("Foo", "Unknown", "m", index) is None
