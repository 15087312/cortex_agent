"""桌宠链路（CustomLoop）logiclink 专项测试

验证桌宠独立 ReAct 循环的 @link 标注与 pet.link 等式一致：
- 子块拼装的原子集合 == 等式原子集合（E004 检查）
- 每个原子在对应块区间有代码证据（E002 检查：Throttle=sleep、
  Think=_think/run、Tool=_run_tool、Observe=_observe、Write=save_pair、
  Emit=return、Guard=分支）
"""
import textwrap

import pytest

from logiclink.check import run
from logiclink.cli import main as cli_main


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


TOML = """
[scan]
roots = ["."]
exclude = []
extensions = [".py", ".link"]

[atoms.Throttle]
calls = ["sleep"]

[atoms.Think]
calls = ["run", "_think", "chat_stream"]

[atoms.Tool]
calls = ["_run_tool"]

[atoms.Observe]
calls = ["_observe"]

[atoms.Write]
calls = ["save_pair"]

[atoms.Emit]
returns = true

[atoms.Guard]
requires_branch = true

[ops.Loop]
needs = ["while", "for"]
"""

PET_LINK = """
export PetChat
eq PetChat = Guard · Think
export PetLoop
eq PetLoop = Throttle · Loop(Think · (Tool · Observe | Emit)) · Write
"""

PET_ENGINE = """
async def chat(self, text: str) -> str:
    # @link:block Pet_Guard = Guard
    if not confirm_frontend_connection():
        return ""
    # @link:block Pet_Think = Think
    reply = await self.loop.run(self, text)
    return reply or ""
"""

REACT_LOOP = """
class CustomLoop:
    # @link:block PetLoop
    async def run(self, engine, text, on_token=None, extra_system=""):
        interval = float(engine.config.get("min_think_interval") or 0)
        # @link:block PetThrottle = Throttle
        if interval > 0:
            await asyncio.sleep(interval)
        rounds = 3
        obs = ""
        reply = ""
        for i in range(rounds):
            # @link:block PetThink = Think
            out = await self._think(engine, text, obs)
            if out.get("tool"):
                # @link:block PetTool = Tool
                obs = await self._run_tool(engine, out["tool"])
                # @link:block PetObserve = Observe
                obs = self._observe(obs)
                continue
            reply = out.get("text", "")
            break
        if reply:
            # @link:block PetWrite = Write
            engine.save_pair(text, reply)
        # @link:block PetEmit = Emit
        return reply

    async def _think(self, engine, text, obs):
        import json
        # 证据：chat_stream 在 Think.calls（此处仅为满足证据收集）
        _ = engine.client.chat_stream
        return {"text": "hi"}

    async def _run_tool(self, engine, name):
        return f"{name} result"

    @staticmethod
    def _observe(raw):
        return raw[:800]
"""


@pytest.fixture
def proj(tmp_path):
    files = {
        "link.toml": TOML,
        "pet.link": PET_LINK,
        "pet_engine.py": PET_ENGINE,
        "react_loop.py": REACT_LOOP,
    }
    return write_proj(tmp_path, files)


def errors(diags):
    return sorted(f"{d.code}" for d in diags if d.level == "error")


def test_pet_chain_clean(proj):
    """完整桌宠链路（PetChat + PetLoop）零 error"""
    diags, code = run(proj)
    assert code == 0, [f"{d.code} {d.message}" for d in diags if d.level == "error"]


def test_pet_loop_missing_tool_block_fails(proj, tmp_path):
    """删除 Tool 块标注 → 子块缺 Tool → E004/E002"""
    src = REACT_LOOP.replace('                # @link:block PetTool = Tool\n', "")
    files = {
        "link.toml": TOML, "pet.link": PET_LINK,
        "pet_engine.py": PET_ENGINE, "react_loop.py": src,
    }
    root = write_proj(tmp_path / "no_tool", files)
    diags, code = run(root)
    assert code == 1
    assert "E004" in errors(diags) or "E002" in errors(diags)


def test_pet_loop_missing_think_evidence_fails(proj, tmp_path):
    """PetThink 区间无 Think 证据（去掉 _think 调用）→ E002"""
    src = REACT_LOOP.replace("out = await self._think(engine, text, obs)", "out = self._noop()")
    files = {
        "link.toml": TOML, "pet.link": PET_LINK,
        "pet_engine.py": PET_ENGINE, "react_loop.py": src,
    }
    root = write_proj(tmp_path / "no_think", files)
    diags, code = run(root)
    assert code == 1
    assert any(d.code == "E002" and "Think" in d.message for d in diags)


def test_pet_chat_guard_branch_required(proj, tmp_path):
    """Guard 需要分支：去掉 if → E002"""
    src = PET_ENGINE.replace(
        '    # @link:block Pet_Guard = Guard\n    if not confirm_frontend_connection():\n        return ""',
        '    # @link:block Pet_Guard = Guard\n    confirm_frontend_connection()',
    )
    files = {
        "link.toml": TOML, "pet.link": PET_LINK,
        "pet_engine.py": src, "react_loop.py": REACT_LOOP,
    }
    root = write_proj(tmp_path / "no_branch", files)
    diags, code = run(root)
    assert code == 1
    assert any(d.code == "E002" and "Guard" in d.message for d in diags)


def test_pet_loop_cli_exit_zero(proj, capsys, monkeypatch):
    """CLI 入口：桌宠链路项目退出码 0"""
    import sys
    monkeypatch.setattr(sys, "argv", ["logiclink", proj])
    code = cli_main()
    assert code == 0
