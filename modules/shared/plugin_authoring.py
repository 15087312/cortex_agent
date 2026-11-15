"""运行期自制插件 — 源码进来，挂载出去（agent 侧 L5）。

和 flow 侧 ``cortexflow/plugin_authoring.py`` 是同一形状，宿主只换掉了
「这个插件提供了什么」的定义：flow 看节点类型，这里看**模型可见的工具集**和
能力端口。四条没换的东西：

* **声明换成观察。** 挂载前后各读一次可见集，差集就是这个插件提供了什么——
  没有清单字段，也就没有可以少报的地方；日志与模型看到的仍是同一份。
* **冲突只对同类拒绝。** 遮蔽内置工具是接管机制本身（``provide`` 允许）；两个
  互不知情的提交抢同一个工具名才是 bug，这种由这里拒绝，除非显式 ``replace``。
* **重提交先撤后装，装不回去就当场把旧版再装上。** 改坏的源码不会顺手毁掉原本能
  跑的那一版。作用域名带代数计数（``name#3``），因为同一插件名的两次挂载必须是两
  个作用域才谈得上「先撤掉前一个」。
* **试运行通过才写盘。** 盘上的每一条都曾经成功过一次，重启后回来的东西因此
  不必再被现场验证。

装配对称性是 B3 退出栈的直接收益：``load_authored()`` 在 lifespan 里挂载，
紧跟着一句 ``_push(stack, unload_authored)``——启动装上的，收尾一定卸。

无沙箱、无签名、无权限模型是既定策略：源码在宿主进程内以解释器的全部权限运行，
和 ``create_tool`` 的 ``exec`` 同一待遇（甚至比它宽——自创工具还带一层内置函数
白名单）。代替信任边界的是可撤销、失败不留半挂载。
"""

from __future__ import annotations

import inspect
import itertools
import json
import os
import re
import threading
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

from infra import ports
from infra.tool_manager import service_registry
from infra.tool_manager.tool_registry import DuplicateToolError, ToolRegistry
from modules.shared.plugin_api import Ctx, Plugin, install, unmount
from utils.logger import get_logger

__all__ = [
    "AuthoredPluginStore",
    "MOUNTED",
    "Mount",
    "PluginConflictError",
    "PluginSourceError",
    "SourcePlugin",
    "author",
    "drop",
    "failure",
    "get_default_store",
    "load_authored",
    "mount_source",
    "run_trial",
    "status",
    "unload_authored",
]

logger = get_logger("plugin_authoring")

# 名字既是 traceback 标签也是主键，不是路径：分隔符或空格会让 <plugin:...> 失去意义
_NAME_RE = re.compile(r"\w[\w.-]{0,63}", re.UNICODE)

# 持久化文件（可用环境变量改到别处，测试与非默认部署用）
_DEFAULT_STORE_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "authored_plugins.json"
)

# 活着的作用域，按插件名。内置函数/端口由谁应答以这里为准，不以盘上的行为准：
# 两个提交抢同一个名字时，是挂载那一刻决定了现在谁在位。
MOUNTED: Dict[str, "Mount"] = {}

# 只用于作用域名：区分同一个插件的第 n 次挂载
_generations = itertools.count(1)


class PluginSourceError(ValueError):
    """进来的文本不能成为插件：没有名字、没有源码、没有 apply。"""


class PluginConflictError(ValueError):
    """另一个已挂载的自制插件正在应答这个工具名。"""


@dataclass
class Mount:
    """一个活着的自制插件，以及它当前应答的名字。"""

    name: str
    plugin: "SourcePlugin"
    ctx: Ctx
    tools: set = field(default_factory=set)
    capabilities: set = field(default_factory=set)
    ports: set = field(default_factory=set)

    async def undo(self) -> None:
        """卸载，并且不再应答任何东西。

        效应栈按后进先出走完，不管这次挂载是否还占着那些绑定：后来接管同一个
        名字的挂载会保住它——注册表的 undo 只改写它自己还认得的那一格。
        """
        MOUNTED.pop(self.name, None)
        await unmount(self.ctx)


class SourcePlugin(Plugin):
    """源码本身就是插件主体，而不是磁盘上的一个模块。"""

    version: ClassVar[str] = "authored"

    def __init__(self, *, name: str, source: str) -> None:
        self.name = _check_name(name)
        if not (source or "").strip():
            raise PluginSourceError(f"插件 {self.name!r} 没有带源码来")
        self.source = source

    def apply(self, ctx: Ctx) -> Any:
        module = _compile(self.name, self.source)
        entry = getattr(module, "apply", None)
        if not callable(entry):
            found = "没有名为 apply 的东西" if entry is None else f"apply 是个 {type(entry).__name__}"
            raise PluginSourceError(
                f"{self.name}: 插件是一个带 apply(ctx) 的模块；这里{found}"
            )
        return entry(ctx)


def _visible() -> Dict[str, Any]:
    """当前模型可见的一切 → 它的实现身份。

    工具走 ``get_tools_for_api(["*"])``（那就是发给模型的那一份），业务端口走
    ``registered_names()``，横切端口走 ``ports.snapshot()``。用身份而不是名字判断变化，
    插件少报不了自己挂上去的东西：可见集里多了什么就是什么。

    第三套是本仓有两张端口表这件事的直接后果：只观察业务那张表时，一个换掉了工具安全门
    （``security.tool_gate``）的自制插件在冲突检查、``list_plugins`` 和日志里**完全不存在**
    ——它改变了模型能做的事，却谁也说不出现谁在应答。
    """
    seen: Dict[str, Any] = {}
    for spec in ToolRegistry.get_tools_for_api(["*"]):
        name = spec["function"]["name"]
        seen[f"tool:{name}"] = ToolRegistry.get_tool(name)
    for name in service_registry.registered_names():
        seen[f"cap:{name}"] = service_registry.get_capability(name)
    for name, provider in ports.snapshot().items():
        # 取登记物本身而不是 resolve() 的结果：工厂端口每次 resolve 都返回新对象，用身份
        # 判变化就会把「插件什么都没做」看成「插件换了实现」。
        seen[f"port:{name}"] = provider
    return seen


def _owners() -> Dict[str, str]:
    """现在每个被占的名字由哪个自制插件应答（以观察到的挂载记录为准）。"""
    out: Dict[str, str] = {}
    for mount in MOUNTED.values():
        for tool in mount.tools:
            out[f"tool:{tool}"] = mount.name
        for cap in mount.capabilities:
            out[f"cap:{cap}"] = mount.name
        for port in mount.ports:
            out[f"port:{port}"] = mount.name
    return out


def _names(claims: Dict[str, Any], kind: str) -> set:
    """从差集里取出某一类（``tool`` / ``cap`` / ``port``）的名字。"""
    prefix = f"{kind}:"
    return {key[len(prefix):] for key in claims if key.startswith(prefix)}


def _owner_by_scope(scope_path: str) -> Optional[str]:
    """作用域路径（``root/name#3``）→ 插件名；不是自制挂载就认不出。"""
    for mount in MOUNTED.values():
        if mount.ctx.path == scope_path:
            return mount.name
    return None


def _conflict_text(name: str, clash: Dict[str, str]) -> str:
    listed = ", ".join(f"{key}（由 {who} 应答）" for key, who in sorted(clash.items()))
    return (
        f"{name} 要占的名字已有别的自制插件在应答：{listed}。"
        "先卸掉那一个，或带 replace 提交明确接管。"
    )


async def _restore(mount: Mount) -> None:
    """把刚撤下的那一版重新装回去（同名重提交失败时的兜底）。

    这是补救，不是第二条正常路径：源码还在手上，重新走一遍 apply 就是那一版本来
    的样子。装不回去才是真要报警的事——槽位空了，日志会说明是哪一格。
    """
    try:
        ctx = await install(
            mount.plugin, name=f"{mount.name}#{next(_generations)}", takeover=True
        )
    except BaseException as exc:  # noqa: BLE001 — 兜底失败要说，不要静默
        logger.error(
            f"[plugin] 自制插件 {mount.name} 的旧版恢复失败，它的槽位现在是空的: {exc}"
        )
        return
    mount.ctx = ctx
    MOUNTED[mount.name] = mount
    logger.warning(f"[plugin] 同名重提交未成功，已把 {mount.name} 的旧版装回去")


async def mount_source(*, name: str, source: str, replace: bool = False) -> Mount:
    """编译并应用 *source*；只有在观察到的接管干净时才留下这次挂载。

    声明就是 ``apply`` 返回之后可见集与之前的差集。拒绝冲突有两道，缺一不可：
    ``ToolRegistry.provide`` 在绑定那一刻拒（它才知道现在谁在位，报错里带着那个
    名字）；这里的观察兜住**绕过 provide 的绑定**——没有沙箱就意味着插件源码可
    以直接写 ``register_tool`` 甚至改 ``_tools``，那同样改变模型看到的东西，而改
    变模型看到的东西就得过一次冲突检查。``replace`` 同时放行两道：调用方声明了
    这是按已知顺序重新接管（重启恢复），此时「后来者赢」要成立，拒绝就不是保护。

    已存在的同名提交**先撤后装，装不回去就当场把旧版再装上**。反过来（先装后撤）
    会漏一个看不见的东西：新版撤销时登记的是「放回它遮蔽的那一格」，而那一格当时
    住的是旧版自己人——于是卸载把工具放回一个已经没有插件拥有的实现，模型还看得见
    它，日志里却查不到归属。
    """
    plugin = SourcePlugin(name=name, source=source)
    previous = MOUNTED.pop(plugin.name, None)
    if previous is not None:
        await previous.undo()
    before = _visible()
    try:
        try:
            ctx = await install(
                plugin, name=f"{plugin.name}#{next(_generations)}", takeover=replace
            )
        except DuplicateToolError as exc:
            # 注册表在绑定那一刻就拒了（它才知道现在谁在位）。这里只把它说的
            # 作用域路径换回提交人认得的插件名——报错该说「谁在应答」，不是说
            # 「哪一格作用域」，后者是这一层的实现细节。
            owner = _owner_by_scope(exc.owner)
            if owner is None or not exc.name:
                raise
            raise PluginConflictError(
                _conflict_text(plugin.name, {f"tool:{exc.name}": owner})
            ) from exc
        after = _visible()
        claims = {key: impl for key, impl in after.items() if before.get(key) is not impl}
        clash = {key: who for key, who in _owners().items() if key in claims}
        if clash and not replace:
            await unmount(ctx)
            raise PluginConflictError(_conflict_text(plugin.name, clash))
    except BaseException:
        if previous is not None:
            await _restore(previous)
        raise

    claimed_tools = _names(claims, "tool")
    claimed_caps = _names(claims, "cap")
    claimed_ports = _names(claims, "port")
    for other in MOUNTED.values():
        other.tools -= claimed_tools
        other.capabilities -= claimed_caps
        other.ports -= claimed_ports

    mount = Mount(
        name=plugin.name,
        plugin=plugin,
        ctx=ctx,
        tools=claimed_tools,
        capabilities=claimed_caps,
        ports=claimed_ports,
    )
    MOUNTED[mount.name] = mount
    logger.info(
        f"[plugin] 自制插件 {mount.name} 已挂载："
        f"工具 {sorted(mount.tools) or '无'}、能力 {sorted(mount.capabilities) or '无'}、"
        f"横切端口 {sorted(mount.ports) or '无'}"
    )
    return mount


async def author(
    *,
    name: str,
    source: str,
    trial: Optional[Dict[str, Any]] = None,
    replace: bool = False,
    store: Optional["AuthoredPluginStore"] = None,
) -> Mount:
    """挂载提交进来的源码，验证它，然后才留下。

    ``trial`` 形如 ``{"tool": 名字, "args": {...}}``，是提交人认为这个插件该办的
    一件事。它真的跑一次；抛异常就撤销挂载并原样抛出——一个连自己那件事都做不
    成的插件不该停在位上。试运行点名的工具必须是这次新挂上的，否则这次试的是
    内置实现，等于没试。写盘是最后一步，所以活过重启的那些都曾成功过一次。
    """
    mount = await mount_source(name=name, source=source, replace=replace)
    if trial is not None:
        trial_tool = str((trial or {}).get("tool") or "")
        if trial_tool not in mount.tools:
            await mount.undo()
            raise PluginSourceError(
                f"试运行要点名这次挂载提供的工具，不是已有工具：{trial_tool!r} "
                f"不在 {sorted(mount.tools) or '（本次没挂上任何工具）'} 里"
            )
        try:
            await run_trial(trial)
        except BaseException:
            await mount.undo()
            raise
    if store is not None:
        store.upsert(mount.name, mount.plugin.source)
    return mount


async def run_trial(trial: Dict[str, Any]) -> Any:
    """跑一次试运行：``{"tool": 名字, "args": {...}}``。同步异步都收。"""
    if not isinstance(trial, dict):
        raise PluginSourceError("试运行需要 {\"tool\": 名字, \"args\": {...}}")
    tool_name = str(trial.get("tool") or "")
    args = trial.get("args") or {}
    if not tool_name:
        raise PluginSourceError("试运行需要指定 tool")
    if not isinstance(args, dict):
        raise PluginSourceError("试运行的 args 必须是个对象")
    func = ToolRegistry.get_func(tool_name)
    if func is None:
        raise PluginSourceError(f"试运行找不到工具 {tool_name!r}")
    result = func(**args)
    if inspect.isawaitable(result):
        result = await result
    return result


async def load_authored(store: "AuthoredPluginStore") -> List[Mount]:
    """启动时挂上盘上的每一条，最老的声明先装。

    装不上的源码（依赖挪了、槽没了）记在它自己那一行上然后跳过：一个半成完的
    插件不该成为进程起不来的理由，而「这一条现在没在跑」必须写在提交人会看的
    地方，不是只在一个已经滚过去的终端里。行按创建顺序回来，所以两个提交抢
    同一个名字时，重启之后仍是后来者赢。
    """
    mounts: List[Mount] = []
    for row in store.list():
        try:
            mounts.append(
                await mount_source(name=row["name"], source=row["source"], replace=True)
            )
        except Exception as exc:  # noqa: BLE001 — 一个插件的失败不该致命
            store.set_state(row["name"], status="failed", error=failure(exc)["error"])
            logger.warning(f"[plugin] 自制插件 {row['name']} 未能挂载: {exc}")
    return mounts


async def unload_authored() -> int:
    """卸掉全部活着的自制插件。lifespan 收尾用——挂载和卸载对称是这套东西的前提。"""
    count = len(MOUNTED)
    for name in list(MOUNTED):
        await drop(name)
    return count


async def drop(name: str) -> bool:
    """*name* 在跑就卸掉；返回是否真卸了。盘上的行不动——卸掉不等于删掉。"""
    mount = MOUNTED.get(name)
    if mount is None:
        return False
    await mount.undo()
    logger.info(f"[plugin] 自制插件 {name} 已卸载")
    return True


def failure(exc: BaseException) -> Dict[str, Any]:
    """把提交失败该还给提交人的东西组好。

    traceback 是载荷本身，不是附属品：迭代插件的人要看见的是自己那段代码里的
    哪一行，而 ``File "<plugin:name>", line 7`` 就是它出现的地方。
    """
    return {
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


def _check_name(name: str) -> str:
    key = str(name or "").strip()
    if not _NAME_RE.fullmatch(key):
        raise PluginSourceError(
            f"插件名 {name!r} 必须是 1-64 个字符——首字符是词字符，"
            "其后可以是字母、数字、'_'、'.' 或 '-'"
        )
    return key


def _compile(name: str, source: str) -> types.ModuleType:
    """把 *source* 当模块主体执行，交出它的命名空间。

    文件名就是插件代码里的报错指向的东西，所以坏掉的那一行带着它所属插件的名字
    回来。模块**不进** ``sys.modules``：重提交同一个名字不该撞上陈旧条目，而一个
    谁都卸不掉的条目恰恰是这一层要避免的东西。
    """
    module = types.ModuleType(f"cortex.plugin.{name}")
    try:
        code = compile(source, f"<plugin:{name}>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise PluginSourceError(f"{name}: 源码编译不过 — {exc}") from exc
    exec(code, module.__dict__)  # noqa: S102 — 按既定策略在宿主进程内执行
    return module


class AuthoredPluginStore:
    """自制插件的持久化：名字、源码、状态、失败原因。

    行的顺序就是创建顺序（JSON 对象保留插入顺序，更新已有键不动位置），所以
    ``list()`` 天然 oldest-first。这里只存文本，不做任何执行——是否挂载由
    ``load_authored`` 决定，失败也只是写在行上。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        raw = path or os.environ.get("CORTEX_AUTHORED_PLUGINS_FILE") or _DEFAULT_STORE_FILE
        self._path = Path(raw)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with self._lock:
                data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[plugin] 自制插件存储读取失败: {e}")
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, rows: Dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self._path.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        except Exception as e:
            logger.warning(f"[plugin] 自制插件存储写入失败: {e}")

    def upsert(self, name: str, source: str) -> None:
        rows = self._read()
        old = rows.get(name) or {}
        # 状态不继承：重新提交意味着「这一版还没被验证过」，而 ok 只在挂载成功后写
        rows[name] = {
            "source": source,
            "status": "ok",
            "error": "",
            "created": old.get("created") or _now(),
            "updated": _now(),
        }
        self._write(rows)

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        row = self._read().get(name)
        return None if row is None else {"name": name, **row}

    def list(self) -> List[Dict[str, Any]]:
        return [{"name": n, **r} for n, r in self._read().items()]

    def set_state(self, name: str, *, status: str, error: str = "") -> None:
        rows = self._read()
        if name not in rows:
            return
        rows[name] = {**rows[name], "status": status, "error": error, "updated": _now()}
        self._write(rows)

    def remove(self, name: str) -> bool:
        rows = self._read()
        if name not in rows:
            return False
        del rows[name]
        self._write(rows)
        return True


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


_default_store: Optional[AuthoredPluginStore] = None


def get_default_store() -> AuthoredPluginStore:
    """端口优先取自制插件存储：接管 ``authored_plugin_store`` 端口 = 换掉全部调用点。

    取不到端口才回退内置单例（脚本、没跑过 bootstrap 的入口）。bootstrap 注册
    的 provider 必须是 ``_default_plugin_store`` 而不是本函数，否则端口→本函数→端口
    一调用就递归。
    """
    from infra.tool_manager.service_registry import get_capability

    factory = get_capability("authored_plugin_store")
    if factory is None:
        return _default_plugin_store()
    return factory()


def _default_plugin_store() -> AuthoredPluginStore:
    """装配层与工具面共用的那一个存储（路径可用环境变量挪走）。"""
    global _default_store
    if _default_store is None:
        _default_store = AuthoredPluginStore()
    return _default_store


def status() -> List[Dict[str, Any]]:
    """盘上的每一条现在跑没在跑。日志与模型看到的必须是同一份，这里就是那份。"""
    rows = get_default_store().list()
    live = set(MOUNTED)
    out = []
    for row in rows:
        mounted = row["name"] in live
        mount = MOUNTED.get(row["name"])
        out.append({
            **row,
            "mounted": mounted,
            "tools": sorted(mount.tools) if mount else [],
            "capabilities": sorted(mount.capabilities) if mount else [],
            "ports": sorted(mount.ports) if mount else [],
        })
    return out
