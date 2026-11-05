"""v2 L1 assembly: use/port wiring checks (E101/E102/E103/E104)."""

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
            # think 在链尾，它的产出交给调用方 perceive（返回端口）
            accept PerceiveLoop: [Write, Emit]

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


def test_e102_mid_chain_output_not_consumed(tmp_path):
    """中间模块产出没人接：既然后继存在，就必须由它的 input 接住。"""
    files = ok_proj()
    files["app.link"] = """
        use perceive := perception.PerceiveLoop
        use think    := thinking.ThinkLoop
        use emit     := perception.PerceiveLoop

        input PerceiveLoop: [Think]
        output PerceiveLoop: [Think, Emit]
        input ThinkLoop: [Think]
        output ThinkLoop: [Write, Emit]
        accept PerceiveLoop: [Write, Emit]

        eq MainFlow = perceive · think · emit
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e102 = [d for d in diags if d.code == "E102"]
    # perceive 的 Emit 与 think 的 Emit 都不在 emit 的 input 里
    assert e102, [(d.code, d.message) for d in diags]
    assert any("does not take it" in d.message for d in e102), [(d.code, d.message) for d in diags]


def test_e102_chain_tail_needs_accepted_return(tmp_path):
    """链尾输出不再因“在末尾”而豁免：必须由调用方的 accept 声明。"""
    files = ok_proj()
    files["app.link"] = """
        use perceive := perception.PerceiveLoop
        use think    := thinking.ThinkLoop

        input PerceiveLoop: [Think]
        output PerceiveLoop: [Think, Emit]
        input ThinkLoop: [Think, Emit]
        output ThinkLoop: [Write, Emit]
        accept PerceiveLoop: [Write]

        eq MainFlow = perceive · think
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e102 = [d for d in diags if d.code == "E102"]
    assert len(e102) == 1, [(d.code, d.message) for d in diags]
    assert "`Emit`" in e102[0].message and "think" in e102[0].message, e102[0].message
    assert "ends the chain" in e102[0].message, e102[0].message


def test_e104_accept_names_atom_no_callee_produces(tmp_path):
    """accept 不能凭空声明：没人产出的原子等于一个消音器。"""
    files = ok_proj()
    files["app.link"] = """
        use perceive := perception.PerceiveLoop
        use think    := thinking.ThinkLoop

        input PerceiveLoop: [Think]
        output PerceiveLoop: [Think, Emit]
        input ThinkLoop: [Think, Emit]
        output ThinkLoop: [Write, Emit]
        accept PerceiveLoop: [Write, Emit, Guard]

        eq MainFlow = perceive · think
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e104 = [d for d in diags if d.code == "E104"]
    assert e104 and "Guard" in e104[0].message, [(d.code, d.message) for d in diags]


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
