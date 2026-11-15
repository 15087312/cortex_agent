"""自制插件的模型面入口 — 把 ``plugin_authoring`` 的四个动作说给模型听。

这是 L5 的那句话：**模型自己写源码，当场挂上，立刻能用，重启还在**。四个工具
就是 ``author / drop / status / remove`` 的口语版，语义一薄不去改它——尤其不去
绕过试运行和冲突检查，那两条是这一层仅有的保护。

依赖方向与探针工具一致：本模块 import infra，由 ``bootstrap`` 在装配末尾触发
注册（装饰器式自注册），infra 不反过来 import modules。

工具函数是同步的：工具执行走 ``asyncio.to_thread``（见 runtime_expert），而
挂载/卸载是协程，所以这里带一个 ``_run`` 桥。worker 线程里没有正在跑的循环，
直接开一个新循环即可；调用方已经在一个循环里时（测试、直调）把协程送到独立
线程的循环里跑——绝不在别人的循环上调 ``asyncio.run``。
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from modules.shared import plugin_authoring as pa
from utils.logger import get_logger

logger = get_logger("plugin_authoring_tools")

__all__ = ["mount_plugin", "unmount_plugin", "list_plugins", "delete_plugin"]


def _run(coro) -> Any:
    """在同步工具面里跑一个协程：没有循环就自己开，有循环就换线程开。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _payload(exc: BaseException) -> str:
    """报错文本 + traceback。迭代插件的人要的是自己那一段代码里的哪一行。"""
    info = pa.failure(exc)
    return f"{info['error']}\n{info['traceback']}"


def _claims(mount: pa.Mount) -> str:
    tools = ", ".join(sorted(mount.tools)) or "无"
    caps = ", ".join(sorted(mount.capabilities)) or "无"
    ports = ", ".join(sorted(mount.ports)) or "无"
    return f"工具[{tools}]、能力[{caps}]、横切端口[{ports}]"


@ToolRegistry.register(
    name="mount_plugin",
    description=(
        "提交一段 Python 源码作为运行期插件并当场挂上：源码是一个带 apply(ctx) 的"
        "模块主体，ctx.provide_tool(名字, 函数) 交出新工具，"
        "ctx.provide_capability(名字, 提供者) 接管业务能力，"
        "ctx.provide_port(名字, 提供者) 接管横切端口（security.tool_gate 这种点号名）。"
        "挂上的工具立刻进入你的"
        "工具列表，卸载即消失。源码会持久化，重启后自动恢复。"
        "试运行（trial）通过才会留下并写盘；不通过就当场撤销并把 traceback 还给你。"
        "名字已被另一个自制插件占用时会被拒绝——先 unmount_plugin 它，或带 "
        "replace=true 明确接管。"
    ),
    params={
        "name": "插件名（1-64 字符，首字符为字母/数字/下划线，其后允许 . 和 -）",
        "source": "插件源码：带 apply(ctx) 的 Python 模块主体",
        "trial": '可选，试运行 {"tool": "本次新挂上的工具名", "args": {...}} 的 JSON 对象',
        "replace": "可选，true 表示明确接管别的插件正在应答的名字",
    },
    risk_level="MEDIUM",
    category="mutation",
    tags=["plugin", "authored", "l5"],
    core=True,
)
def mount_plugin(name: str, source: str, trial: Any = "", replace: bool = False) -> str:
    """挂载自制插件（试运行通过才写盘）。

    Args:
        name: 插件名
        source: 带 apply(ctx) 的模块源码
        trial: 试运行，dict 或其 JSON 字符串
        replace: 明确接管

    Returns:
        结果文本；失败时带 traceback
    """
    trial_obj: Optional[Dict[str, Any]]
    if not trial:
        trial_obj = None
    elif isinstance(trial, dict):
        trial_obj = trial
    else:
        try:
            parsed = json.loads(str(trial))
        except (json.JSONDecodeError, TypeError):
            return "❌ trial 不是有效的 JSON 对象：应为 {\"tool\": 名字, \"args\": {...}}"
        if not isinstance(parsed, dict):
            return "❌ trial 必须是一个 JSON 对象：{\"tool\": 名字, \"args\": {...}}"
        trial_obj = parsed

    try:
        mount = _run(
            pa.author(
                name=name,
                source=source,
                trial=trial_obj,
                replace=bool(replace),
                store=pa.get_default_store(),
            )
        )
    except (pa.PluginSourceError, pa.PluginConflictError) as exc:
        return f"❌ {_payload(exc)}"
    except Exception as exc:  # noqa: BLE001 — 插件代码怎么炸都不该炸了这轮对话
        # 无沙箱就意味着这里收得到任何东西。不 raise、不 exception 级日志：提交人
        # 要的是 traceback，而这一轮对话还在继续。
        logger.warning(f"[plugin] mount_plugin({name!r}) 失败: {type(exc).__name__}: {exc}")
        return f"❌ {_payload(exc)}"

    tried = ""
    if trial_obj:
        tried = f"；试运行 {trial_obj.get('tool')} 已通过"
    return (
        f"✅ 插件 {mount.name} 已挂载并持久化，提供{_claims(mount)}{tried}。"
        "新工具现在就在这个列表里，直接用。"
    )


@ToolRegistry.register(
    name="unmount_plugin",
    description=(
        "卸掉一个正在跑的自制插件：它提供的工具与接管的能力当场退回原实现。"
        "持久化的那一行不动，所以重启后它还会回来；要永久删除用 delete_plugin。"
    ),
    params={"name": "插件名"},
    risk_level="MEDIUM",
    category="mutation",
    tags=["plugin", "authored", "l5"],
    core=True,
)
def unmount_plugin(name: str) -> str:
    """卸载（不动盘上的行）。

    Args:
        name: 插件名

    Returns:
        结果文本
    """
    dropped = _run(pa.drop(name))
    if not dropped:
        return f"❌ 插件 {name!r} 没在跑（当前在跑的：{', '.join(sorted(pa.MOUNTED)) or '无'}）"
    return f"✅ 插件 {name} 已卸载；盘上的源码仍在，重启后会回来。要彻底删走 delete_plugin。"


@ToolRegistry.register(
    name="list_plugins",
    description=(
        "列出所有自制插件：盘上提交的每一条，是否在跑，各自主答哪些工具与能力。"
        "这就是模型可见集与日志必须同的那一份——想知道某个工具是谁提供的，看这里。"
    ),
    params={},
    risk_level="LOW",
    category="query",
    tags=["plugin", "authored", "l5"],
    core=True,
)
def list_plugins() -> str:
    """列出提交与在跑状态。

    Returns:
        逐行文本；一条也没有时说明如何开始
    """
    rows = pa.status()
    if not rows:
        return "（还没有提交过自制插件。用 mount_plugin 交一段带 apply(ctx) 的源码。）"
    lines = []
    for row in rows:
        state = "在跑" if row["mounted"] else ("启动时没挂上" if row["status"] == "failed" else "未挂载")
        detail = f" ← {row['error']}" if row.get("error") else ""
        tools = ", ".join(row["tools"]) or "无"
        caps = ", ".join(row["capabilities"]) or "无"
        lines.append(
            f"- {row['name']}（{state}）工具[{tools}] 能力[{caps}]{detail}"
        )
    return "\n".join(lines)


@ToolRegistry.register(
    name="delete_plugin",
    description=(
        "彻底删掉一个自制插件：卸掉正在跑的作用域并删去持久化的那一行，"
        "重启后也不会再回来。只想临时停掉的话用 unmount_plugin。"
    ),
    params={"name": "插件名"},
    risk_level="MEDIUM",
    category="mutation",
    tags=["plugin", "authored", "l5"],
    core=True,
)
def delete_plugin(name: str) -> str:
    """卸载 + 删行。

    Args:
        name: 插件名

    Returns:
        结果文本
    """
    dropped = _run(pa.drop(name))
    removed = pa.get_default_store().remove(name)
    if not dropped and not removed:
        return f"❌ 没有名为 {name!r} 的自制插件（既没在跑，盘上也没这一行）"
    where = []
    if dropped:
        where.append("已卸载")
    if removed:
        where.append("已删除持久化的行")
    return f"✅ 插件 {name}：" + "、".join(where)
