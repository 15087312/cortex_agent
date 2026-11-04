"""v2 L1 assembly: use/port wiring checks (E101/E102/E103)."""

import textwrap

from logiclink.check import run


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


def codes(diags):
    return sorted(f"{d.level}[{d.code}]" for d in diags)


def ok_proj():
    return {
        "link.toml": "",
        "app.link": """
            use perceive := perception.PerceiveLoop
            use think    := thinking.ThinkLoop

            input PerceiveLoop: [Think]
            output PerceiveLoop: [Think, Emit]
            input ThinkLoop: [Think, Emit]
            output ThinkLoop: [Write, Emit]

            eq MainFlow = perceive · think
            """,
        "modules/perception/perception.link": "export PerceiveLoop\neq PerceiveLoop = Loop(Think)\n",
        "modules/thinking/thinking.link": "export ThinkLoop\neq ThinkLoop = Loop(Retrieve · Think · Guard · (Write | Emit))\n",
        # Real wiring so the L3 call-graph check also passes.
        "modules/perception/impl.py": "import modules.thinking\n\ndef run_p():\n    pass\n",
    }


def test_flow_ok(tmp_path):
    root = write_proj(tmp_path, ok_proj())
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code.startswith("E10")]


def test_e101_input_not_provided(tmp_path):
    files = ok_proj()
    # think needs Guard but perceive never produces it.
    files["app.link"] = """
        use perceive := perception.PerceiveLoop
        use think    := thinking.ThinkLoop

        input PerceiveLoop: [Think]
        output PerceiveLoop: [Think, Emit]
        input ThinkLoop: [Think, Emit, Guard]
        output ThinkLoop: [Write, Emit]

        eq MainFlow = perceive · think
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e101 = [d for d in diags if d.code == "E101"]
    assert e101 and "Guard" in e101[0].message, [(d.code, d.message) for d in diags]


def test_e102_output_dangling(tmp_path):
    files = ok_proj()
    # perceive outputs Emit but nothing downstream declares input Emit.
    files["app.link"] = """
        use perceive := perception.PerceiveLoop
        use think    := thinking.ThinkLoop

        input PerceiveLoop: [Think]
        output PerceiveLoop: [Think, Emit]
        input ThinkLoop: [Think]
        output ThinkLoop: [Write, Emit]

        eq MainFlow = perceive · think
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e102 = [d for d in diags if d.code == "E102"]
    assert e102 and "perceive" in e102[0].message, [(d.code, d.message) for d in diags]


def test_e103_disjoint_ports(tmp_path):
    files = ok_proj()
    # Adjacent modules declare disjoint port atom sets.
    files["app.link"] = """
        use perceive := perception.PerceiveLoop
        use think    := thinking.ThinkLoop

        input PerceiveLoop: [Think]
        output PerceiveLoop: [Write]
        input ThinkLoop: [Emit]
        output ThinkLoop: [Write, Emit]

        eq MainFlow = perceive · think
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e103 = [d for d in diags if d.code == "E103"]
    assert e103, [(d.code, d.message) for d in diags]
    # Disjoint ports are one broken splice: E103 only, no "all inputs missing" E101.
    assert not [d for d in diags if d.code == "E101"], [
        (d.code, d.message) for d in diags
    ]


def test_bare_port_binds_to_nearest_export(tmp_path):
    files = ok_proj()
    files["modules/thinking/thinking.link"] = """
        export ThinkLoop
        input: [Think, Emit]
        output: [Write, Emit]
        eq ThinkLoop = Loop(Retrieve · Think · Guard · (Write | Emit))
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
