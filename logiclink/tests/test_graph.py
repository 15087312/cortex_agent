"""v2 L3 call graph: declared vs real wiring (E301/E302/E303) + --graph render."""

import textwrap

from logiclink.check import run
from logiclink.cli import main as cli_main
from logiclink.v2_graph import render_graph


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


def base_proj():
    return {
        "link.toml": "",
        "app.link": """
            use a := alpha.ASym
            use b := beta.BSym
            eq Flow = a · b
            """,
        "modules/alpha/alpha.link": "export ASym\neq ASym = Loop(Think)\n",
        "modules/beta/beta.link": "export BSym\neq BSym = Loop(Think)\n",
        "modules/alpha/impl.py": "def run_a():\n    pass\n",
        "modules/beta/impl.py": "def run_b():\n    pass\n",
    }


def test_graph_ok(tmp_path):
    files = base_proj()
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code.startswith("E30")]


def test_e301_declared_not_wired(tmp_path):
    # Declared a → b but alpha never imports beta.
    root = write_proj(tmp_path, base_proj())
    diags, code = run(root)
    assert code == 1
    e301 = [d for d in diags if d.code == "E301"]
    assert e301 and "alpha" in e301[0].message and "beta" in e301[0].message, [
        (d.code, d.message) for d in diags
    ]


def test_e302_real_not_declared(tmp_path):
    files = base_proj()
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    # Remove the eq chain so the real edge is undeclared.
    files["app.link"] = """
        use a := alpha.ASym
        use b := beta.BSym
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e302 = [d for d in diags if d.code == "E302"]
    assert e302 and "alpha" in e302[0].message and "beta" in e302[0].message, [
        (d.code, d.message) for d in diags
    ]


def test_e303_reverse_dependency(tmp_path):
    files = base_proj()
    # Declared a → b but real code imports b → a.
    files["modules/beta/impl.py"] = "import modules.alpha\n\ndef run_b():\n    pass\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e303 = [d for d in diags if d.code == "E303"]
    assert e303, [(d.code, d.message) for d in diags]


def mutual_files() -> dict:
    files = base_proj()
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    files["modules/beta/impl.py"] = "import modules.alpha\n\ndef run_b():\n    pass\n"
    return files


def test_mutual_dependency_is_reported(tmp_path):
    """A real cycle (a → b AND b → a) must not be invisible.

    Regression: the E302 loop used to skip any edge whose reverse was declared
    ("already reported as E303"), but the E303 branch only fires when the reverse
    is real and the forward is *not*.  When both directions were real -- exactly
    the signature of a dependency cycle -- neither diagnostic fired.
    """
    root = write_proj(tmp_path, mutual_files())
    diags, code = run(root)
    assert code == 1
    cycles = [d for d in diags if d.code == "E304"]
    assert len(cycles) == 1, [(d.code, d.message) for d in diags]
    assert "alpha" in cycles[0].message and "beta" in cycles[0].message, cycles[0].message
    # The cycle subsumes its edges: they are not also "undeclared" E302 noise.
    assert not [d for d in diags if d.code == "E302"]


def test_mutual_declared_is_still_a_cycle(tmp_path):
    """Declaring both directions documents the cycle; it does not remove it."""
    files = mutual_files()
    files["app.link"] = """
        use a := alpha.ASym
        use b := beta.BSym
        eq Flow = a · b
        eq Back = b · a
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1, [(d.code, d.message) for d in diags]
    assert [d for d in diags if d.code == "E304"], [(d.code, d.message) for d in diags]


def test_three_module_cycle_reported_once(tmp_path):
    files = base_proj()
    files["app.link"] = """
        use a := alpha.ASym
        use b := beta.BSym
        use g := gamma.GSym
        eq Chain = a · b · g
        """
    files["modules/gamma/gamma.link"] = "export GSym\neq GSym = Loop(Think)\n"
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    files["modules/beta/impl.py"] = "import modules.gamma\n\ndef run_b():\n    pass\n"
    files["modules/gamma/impl.py"] = "import modules.alpha\n\ndef run_g():\n    pass\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    cycles = [d for d in diags if d.code == "E304"]
    assert len(cycles) == 1, [(d.code, d.message) for d in diags]
    for name in ("alpha", "beta", "gamma"):
        assert name in cycles[0].message, cycles[0].message


def test_acyclic_chain_has_no_e304(tmp_path):
    files = base_proj()
    files["app.link"] = """
        use a := alpha.ASym
        use b := beta.BSym
        use g := gamma.GSym
        eq Chain = a · b · g
        """
    files["modules/gamma/gamma.link"] = "export GSym\neq GSym = Loop(Think)\n"
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    files["modules/beta/impl.py"] = "import modules.gamma\n\ndef run_b():\n    pass\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code in ("E304", "W304")]


def test_allow_cycles_downgrades_to_warning(tmp_path):
    files = mutual_files()
    files["link.toml"] = "[arch]\nallow_cycles = [[\"alpha\", \"beta\"]]\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    w304 = [d for d in diags if d.code == "W304"]
    assert code == 0, [(d.code, d.message) for d in diags]
    assert w304 and w304[0].level == "warning", [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "E304"]
    assert not [d for d in diags if d.code == "E302"]


def test_render_graph_shows_mutual_cycle(tmp_path):
    """The graph must draw what check_graph reports: a real mutual pair.

    The renderer used to skip any real edge whose reverse was declared, so the
    E302 that the checker reports for a cycle never appeared in ``--graph``.
    """
    files = base_proj()
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    files["modules/beta/impl.py"] = "import modules.alpha\n\ndef run_b():\n    pass\n"
    root = write_proj(tmp_path, files)
    decls = _collect(root)
    drift = render_graph(decls, drift_only=True)
    assert "alpha -->|真实✓| beta" in drift, drift
    assert "beta -.->|环E304" in drift, drift


def test_scan_root_comes_from_use_not_from_symbol_location(tmp_path):
    """Moving a symbol into ``core/`` must not shrink the module's scan root.

    The symbol only proves the module is wired.  When the scan root was derived
    from the declaring file's directory, the import in ``modules/alpha/sibling.py``
    was invisible and this project failed with a phantom E301.
    """
    files = {
        "link.toml": textwrap.dedent(
            """\
            [scan]
            roots = ["app.link", "modules/alpha", "modules/beta"]
            exclude = []
            extensions = [".py", ".link"]
            """
        ),
        "app.link": "use a := alpha.ASym\nuse b := beta.BSym\neq Flow = a · b\n",
        "modules/alpha/core/alpha.link": "export ASym\neq ASym = Loop(Think)\n",
        "modules/alpha/sibling.py": "import modules.beta\n\ndef run_a():\n    pass\n",
        "modules/beta/beta.link": "export BSym\neq BSym = Loop(Think)\n",
        "modules/beta/impl.py": "def run_b():\n    pass\n",
    }
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_use_declares_symbol_owned_by_another_module(tmp_path):
    """``use a := alpha.BSym`` must fail: BSym belongs to beta.

    Regression: only the symbol segment used to be resolved, so a wrong module
    segment was decorative -- the declaration claimed a capability alpha does
    not export and the checker stayed silent (exit 0).
    """
    files = base_proj()
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    files["app.link"] = """
        use a := alpha.BSym
        use b := beta.BSym
        eq Flow = a · b
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1, [(d.code, d.message) for d in diags]
    e005 = [d for d in diags if d.code == "E005"]
    assert len(e005) == 1, [(d.code, d.message) for d in diags]
    assert "alpha.BSym" in e005[0].message, e005[0].message
    assert "beta.link" in e005[0].message, e005[0].message


def test_use_symbol_moved_into_module_subdir_still_resolves(tmp_path):
    """A symbol declared in ``modules/alpha/core/`` belongs to alpha.

    The module check must not turn the F3 layout (declaration in a sub-directory
    of the module) into a false E005.
    """
    files = base_proj()
    del files["modules/alpha/alpha.link"]
    files["link.toml"] = textwrap.dedent(
        """\
        [scan]
        roots = ["app.link", "modules/alpha", "modules/beta"]
        exclude = []
        extensions = [".py", ".link"]
        """
    )
    files["modules/alpha/core/alpha.link"] = "export ASym\neq ASym = Loop(Think)\n"
    files["app.link"] = """
        use a := alpha.ASym
        use b := beta.BSym
        eq Flow = a · b
        """
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_relative_import_across_packages_is_a_real_edge(tmp_path):
    """``from ...beta import BSym`` inside ``alpha/core`` crosses into beta."""
    files = base_proj()
    files["modules/alpha/core/impl.py"] = "from ...beta import BSym\n\ndef run_a():\n    pass\n"
    files["modules/alpha/core/__init__.py"] = ""
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code.startswith("E30")]


def test_relative_import_inside_package_is_not_a_cross_edge(tmp_path):
    """``from .beta import B`` stays inside alpha: it must not declare alpha→beta.

    Matching on names would turn the local ``alpha/beta.py`` into the wired
    module ``beta`` and invent a dependency that does not exist.
    """
    files = base_proj()
    files["modules/alpha/impl.py"] = "from .beta import B\n\ndef run_a():\n    pass\n"
    files["modules/alpha/beta.py"] = "B = 1\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    # Declared a → b but nothing really imports the beta module.
    assert code == 1
    assert [d for d in diags if d.code == "E301"], [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code == "E302"]


def test_render_graph_ok(tmp_path):
    files = base_proj()
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    root = write_proj(tmp_path, files)
    decls = _collect(root)
    out = render_graph(decls)
    assert "alpha -->|真实✓| beta" in out, out
    drift = render_graph(decls, drift_only=True)
    assert "真实✓" in drift and "E301" not in drift and "E302" not in drift


def test_render_graph_drift(tmp_path):
    root = write_proj(tmp_path, base_proj())
    decls = _collect(root)
    out = render_graph(decls)
    assert "声明无真实(E301)" in out, out
    drift = render_graph(decls, drift_only=True)
    assert "E301" in drift


def _collect(root):
    from pathlib import Path

    from logiclink.collect import collect
    from logiclink.config import load_config

    cfg = load_config(Path(root))
    return collect(Path(root), cfg.scan_roots, cfg.exclude, cfg.extensions).decls


def test_cli_graph(tmp_path, capsys):
    files = base_proj()
    files["modules/alpha/impl.py"] = "import modules.beta\n\ndef run_a():\n    pass\n"
    root = write_proj(tmp_path, files)
    assert cli_main([root, "--graph"]) == 0
    assert "flowchart LR" in capsys.readouterr().out
