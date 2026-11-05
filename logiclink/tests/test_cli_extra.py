"""CLI 层测试：覆盖 README 声明的命令行功能。

补全缺口：
- --strict-warnings 退出码语义
- --allow-arch / forbid_eq_change 架构门禁
- --recall（列出全部命名块 / 按名召回）
- --html / --html-flow / --html-detail 渲染落盘
- --graph --drift-only CLI 入口
- 退出码矩阵（error→1 / warning→0 / --strict-warnings→1）
"""

import textwrap

from logiclink.cli import main as cli_main

OK_REACT = textwrap.dedent(
    """\
    # @link:eq ReAct = Loop(Think · Guard · (Tool · Observe | Emit))
    # @link:block ReAct
    def react(state):
        while True:
            # @link:block Think
            msg = llm.invoke(state)
            # @link:block Guard
            if not getattr(msg, "tool_calls", None):
                # @link:block Emit
                return msg
            # @link:block Tool
            out = tools.invoke(msg.tool_calls[0])
            # @link:block Observe
            state = observe(state, out)
    """
)


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


# ---------------------------------------------------------------- 退出码矩阵


def test_warning_only_exits_zero(tmp_path, capsys):
    # 仅 W010 warning → 退出码 0
    src = textwrap.dedent(
        """\
        # @link:block Think
        def f(state):
            msg = llm.invoke(state)
            out = tools.invoke(msg)
        """
    )
    root = write_proj(tmp_path, {"react.py": src})
    assert cli_main([root]) == 0
    capsys.readouterr()


def test_error_exits_one(tmp_path):
    src = OK_REACT.replace("        msg = llm.invoke(state)\n", "")
    root = write_proj(tmp_path, {"react.py": src})
    assert cli_main([root]) == 1


def test_strict_warnings_exits_one(tmp_path, capsys):
    # --strict 把 W010 变 error → 1；--strict-warnings 也应让任意 warning → 1
    src = textwrap.dedent(
        """\
        # @link:block Think
        def f(state):
            msg = llm.invoke(state)
            out = tools.invoke(msg)
        """
    )
    root = write_proj(tmp_path, {"react.py": src})
    assert cli_main([root, "--strict-warnings"]) == 1
    capsys.readouterr()


# ---------------------------------------------------------------- 架构门禁


ARCH_TOML = textwrap.dedent(
    """\
    [scan]
    roots = ["."]
    exclude = []
    extensions = [".py", ".link"]

    [arch]
    forbid_eq_change = true
    """
)


def arch_proj(tmp_path) -> str:
    return write_proj(tmp_path, {"link.toml": ARCH_TOML, "react.py": OK_REACT})


def test_forbid_eq_change_requires_baseline(tmp_path, capsys):
    """The gate is a baseline diff now: without a baseline it says how to make one."""
    root = arch_proj(tmp_path)
    assert cli_main([root]) == 1
    out = capsys.readouterr().out
    assert "E305" in out and "--arch-update" in out


def test_arch_update_records_baseline_then_run_is_clean(tmp_path, capsys):
    root = arch_proj(tmp_path)
    assert cli_main([root, "--arch-update"]) == 0
    assert (tmp_path / ".logiclink" / "arch.json").is_file()
    capsys.readouterr()

    assert cli_main([root]) == 0
    assert "E305" not in capsys.readouterr().out


def test_arch_update_ignores_cosmetic_eq_edits(tmp_path, capsys):
    """Fingerprints hash the normalized AST: `·` vs `>>` and spacing are not changes."""
    root = arch_proj(tmp_path)
    assert cli_main([root, "--arch-update"]) == 0
    capsys.readouterr()

    cosmetic = OK_REACT.replace("Loop(Think · Guard · (Tool · Observe | Emit))", "Loop(Think>>Guard>>(Tool·Observe | Emit))")
    (tmp_path / "react.py").write_text(cosmetic, encoding="utf-8")
    assert cli_main([root]) == 0
    assert "E305" not in capsys.readouterr().out


def test_eq_change_blocks_and_allow_arch_only_warns(tmp_path, capsys):
    root = arch_proj(tmp_path)
    assert cli_main([root, "--arch-update"]) == 0
    capsys.readouterr()

    (tmp_path / "react.py").write_text(OK_REACT + "# @link:eq Extra = Think\n", encoding="utf-8")
    assert cli_main([root]) == 1
    out = capsys.readouterr().out
    assert "E305" in out and "Extra" in out and "added" in out

    # --allow-arch lets this run through, but the change is still shown.
    assert cli_main([root, "--allow-arch"]) == 0
    out = capsys.readouterr().out
    assert "E305" in out and "Extra" in out


# ---------------------------------------------------------------- --recall


def test_recall_lists_all_blocks(tmp_path, capsys):
    root = write_proj(tmp_path, {"react.py": OK_REACT})
    assert cli_main([root, "--recall"]) == 0
    out = capsys.readouterr().out
    assert "named blocks" in out
    # OK_REACT 含 ReAct/Think/Guard/Emit/Tool/Observe 命名块
    for name in ("ReAct", "Think", "Guard", "Emit", "Tool", "Observe"):
        assert name in out, name


def test_recall_by_name(tmp_path, capsys):
    root = write_proj(tmp_path, {"react.py": OK_REACT})
    assert cli_main([root, "--recall", "ReAct"]) == 0
    out = capsys.readouterr().out
    assert "== ReAct" in out
    assert "def react(state)" in out


def test_recall_unknown_name(tmp_path, capsys):
    root = write_proj(tmp_path, {"react.py": OK_REACT})
    assert cli_main([root, "--recall", "Nope"]) == 0
    out = capsys.readouterr().out
    assert "no block named" in out


# ---------------------------------------------------------------- 无 link.toml 内置默认原子


def test_no_link_toml_uses_builtin_atoms(tmp_path, capsys):
    # README：无 link.toml 时使用内置默认原子表照样能跑
    root = write_proj(tmp_path, {"react.py": OK_REACT})
    assert cli_main([root]) == 0
    capsys.readouterr()


# ---------------------------------------------------------------- --graph --drift-only


def graph_proj():
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


def test_cli_drift_only(tmp_path, capsys):
    root = write_proj(tmp_path, graph_proj())
    assert cli_main([root, "--graph", "--drift-only"]) == 0
    assert "flowchart LR" in capsys.readouterr().out


# ---------------------------------------------------------------- --html / --html-flow / --html-detail


def test_html_outputs(tmp_path, capsys):
    root = write_proj(tmp_path, graph_proj())
    out_html = tmp_path / "pan.html"
    assert cli_main([root, "--html", str(out_html)]) == 0
    capsys.readouterr()
    assert out_html.exists() and out_html.stat().st_size > 0


def test_html_flow_outputs(tmp_path, capsys):
    root = write_proj(tmp_path, graph_proj())
    out_html = tmp_path / "flow.html"
    assert cli_main([root, "--html-flow", str(out_html)]) == 0
    capsys.readouterr()
    assert out_html.exists() and out_html.stat().st_size > 0


def test_html_detail_outputs(tmp_path, capsys):
    root = write_proj(tmp_path, graph_proj())
    out_html = tmp_path / "detail.html"
    assert cli_main([root, "--html-detail", str(out_html)]) == 0
    capsys.readouterr()
    assert out_html.exists() and out_html.stat().st_size > 0


# ---------------------------------------------------------------- 示例项目冒烟（README §安装）


def test_examples_agent_react_ok(capsys):
    # README：`logiclink examples/agent_react` → logiclink: ok
    from pathlib import Path

    examples = Path(__file__).resolve().parent.parent / "examples" / "agent_react"
    assert examples.exists()
    assert cli_main([str(examples)]) == 0
    assert "ok" in capsys.readouterr().out