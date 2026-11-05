"""v2 L4 state machine: declarations vs real code (E401/E402/E403)."""

import textwrap

from logiclink.check import run


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


def ok_proj():
    return {
        "link.toml": "",
        "modules/blackboard/blackboard.link": """
            state blackboard
            init = empty
            trans = "empty --AcceptEvent--> partial"
            trans = "partial --merge--> complete"
            trans = "complete --flush--> empty"
            """,
        "modules/blackboard/impl.py": """
            def handle():
                accept_event()
                state = "partial"
                merge()
                state = "complete"
                flush()
                state = "empty"
            """,
    }


def test_state_ok(tmp_path):
    root = write_proj(tmp_path, ok_proj())
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code.startswith("E40")]


def test_e401_undeclared_state_literal(tmp_path):
    files = ok_proj()
    files["modules/blackboard/impl.py"] = """
        def handle():
            accept_event()
            state = "unknown_state"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e401 = [d for d in diags if d.code == "E401"]
    assert e401 and "unknown_state" in e401[0].message, [(d.code, d.message) for d in diags]


def test_e402_declared_event_not_implemented(tmp_path):
    files = ok_proj()
    # flush() is declared as an event but never called in the module.
    files["modules/blackboard/impl.py"] = """
        def handle():
            accept_event()
            state = "partial"
            merge()
            state = "complete"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e402 = [d for d in diags if d.code == "E402"]
    assert e402 and "flush" in e402[0].message, [(d.code, d.message) for d in diags]


def test_e403_reached_without_declared_transition(tmp_path):
    files = ok_proj()
    # "partial" is a declared state but only a transition *source*: reaching it
    # directly (no declared transition targets it, init is "complete") is E403.
    files["modules/blackboard/blackboard.link"] = """
        state blackboard
        init = complete
        trans = "partial --merge--> complete"
        """
    files["modules/blackboard/impl.py"] = """
        def handle():
            merge()
            state = "partial"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e403 = [d for d in diags if d.code == "E403"]
    assert e403 and "partial" in e403[0].message, [(d.code, d.message) for d in diags]


def test_snake_case_event_matching(tmp_path):
    # Snake_case call matches the declared event name.
    files = ok_proj()
    files["modules/blackboard/blackboard.link"] = """
        state blackboard
        init = empty
        trans = "empty --accept_event--> partial"
        """
    files["modules/blackboard/impl.py"] = """
        def handle():
            accept_event()
            state = "partial"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


# ---------------------------------------------------------------- Enum machines
#
# 真实代码里的状态机通常不是 `state = "literal"`，而是 Enum 成员：
# `self.state: TurnState = TurnState.IDLE` / `transition_to(TurnState.PLANNING)`。
# 原来的正则只认字符串字面量，于是这类机器一个状态都看不到（E401/E403 永不触发）。


POOL_PY = """
    from enum import Enum


    class TurnState(str, Enum):
        IDLE = "idle"
        PLANNING = "planning"
        EXECUTING = "executing"
        COMPLETE = "complete"


    class TurnContext:
        def __init__(self):
            self.state: TurnState = TurnState.IDLE

        def transition_to(self, new_state: TurnState) -> None:
            self.state = new_state
    """


def enum_proj() -> dict:
    return {
        "link.toml": "",
        "modules/thinking/thinking.link": "export Engine\n",
        "modules/thinking/machine.link": """
            state turn
            enum = TurnState
            init = idle
            trans = "idle --transition_to--> planning"
            trans = "planning --transition_to--> executing"
            trans = "executing --transition_to--> complete"
            """,
        "modules/thinking/context/pool.py": POOL_PY,
        "modules/thinking/orchestrator.py": """
            from modules.thinking.context.pool import TurnState


            def run(ctx):
                ctx.transition_to(TurnState.PLANNING)
                ctx.transition_to(TurnState.EXECUTING)
                ctx.transition_to(TurnState.COMPLETE)
            """,
    }


def test_enum_machine_ok(tmp_path):
    root = write_proj(tmp_path, enum_proj())
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
    assert not [d for d in diags if d.code.startswith("E40")]


def test_enum_e401_undeclared_member(tmp_path):
    files = enum_proj()
    files["modules/thinking/orchestrator.py"] = """
        from modules.thinking.context.pool import TurnState


        def run(ctx):
            ctx.transition_to(TurnState.FINISHED)
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e401 = [d for d in diags if d.code == "E401"]
    assert e401 and "FINISHED" in e401[0].message, [(d.code, d.message) for d in diags]


def test_enum_e403_set_state_without_declared_transition(tmp_path):
    files = enum_proj()
    # complete 是声明过的状态，但没有任何 trans 指向它，代码却直接设进去。
    files["modules/thinking/machine.link"] = """
        state turn
        enum = TurnState
        init = idle
        trans = "idle --transition_to--> planning"
        trans = "planning --transition_to--> executing"
        trans = "complete --restart--> idle"
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e403 = [d for d in diags if d.code == "E403"]
    assert e403 and "complete" in e403[0].message, [(d.code, d.message) for d in diags]


def test_enum_reads_are_not_reaches(tmp_path):
    """读状态（`state in (TurnState.X,)`）不能被当成到达该状态。"""
    files = enum_proj()
    files["modules/thinking/orchestrator.py"] = """
        from modules.thinking.context.pool import TurnState


        def run(ctx):
            ctx.transition_to(TurnState.PLANNING)
            if ctx.state in (TurnState.COMPLETE,):
                return True
            return False
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    # complete 被读取但从未被设置：没有声明指向它也不是 init，故只有 E402 提到它。
    assert not [d for d in diags if d.code == "E403"], [(d.code, d.message) for d in diags]
    assert [d for d in diags if d.code == "E402"], [(d.code, d.message) for d in diags]


def test_enum_e402_declared_transition_never_reached(tmp_path):
    files = enum_proj()
    files["modules/thinking/machine.link"] = """
        state turn
        enum = TurnState
        init = idle
        trans = "idle --transition_to--> planning"
        trans = "planning --transition_to--> executing"
        trans = "executing --fail--> complete"
        """
    files["modules/thinking/orchestrator.py"] = """
        from modules.thinking.context.pool import TurnState


        def run(ctx):
            ctx.transition_to(TurnState.PLANNING)
            ctx.transition_to(TurnState.EXECUTING)
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e402 = [d for d in diags if d.code == "E402"]
    assert len(e402) == 1 and "complete" in e402[0].message, [(d.code, d.message) for d in diags]


def test_enum_machine_sees_states_set_outside_declaring_dir(tmp_path):
    """机器声明在 core/，状态却由模块根目录的代码设置：必须被检查到。

    回归：模块根原先取「.link 所在目录」，声明搬到子目录后扫描面缩小，
    E401/E403 对这些文件永远不可见。
    """
    files = enum_proj()
    del files["modules/thinking/machine.link"]
    files["modules/thinking/core/machine.link"] = """
        state turn
        enum = TurnState
        init = idle
        trans = "idle --transition_to--> planning"
        trans = "planning --transition_to--> executing"
        trans = "executing --transition_to--> complete"
        """
    files["modules/thinking/orchestrator.py"] = """
        from modules.thinking.context.pool import TurnState


        def run(ctx):
            ctx.transition_to(TurnState.UNKNOWN)
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    assert [d for d in diags if d.code == "E401"], [(d.code, d.message) for d in diags]
