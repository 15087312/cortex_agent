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
