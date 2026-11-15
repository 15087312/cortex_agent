"""插件契约 — 一个插件被允许依赖的全部表面。

三个来自 Cordis 的想法，刻意不掺别处：

* **Ctx 是具名服务槽的容器，不是启动时序的清单。** 装配写成「依次调用」就
  意味着卸载要另写一遍倒序清单——两份清单迟早对不上。子作用域沿父链解析，
  插件看得见宿主给它开的槽，而不必 import 宿主。
* **effect 是插件说「我走的时候把这撤销」的唯一方式。** 每次绑定都压进一个
  LIFO 栈，卸载时弹栈。这里没有 ``deactivate`` 钩子：一个会忘记撤销某个东西
  的 deactivate 钩子，和根本没有它无法区分。
* **Plugin.apply(ctx) 是唯一的钩子，绑定只发生在里面。** import 期的注册不可
  撤销——被 import 副作用注册起来的东西，不重启进程卸不掉。这条对本仓尤其
  要紧：``infra/tool_manager/tools/__init__.py`` 的 pkgutil 扫描就是这个形状。

启动是不动点不是日程：装配顺序由调用方决定，需要别家能力的插件在**调用时**
从容器里取，而不是在 import 时假设它存在。

作用域边界：本模块不碰依赖方向。注册面原语（provide / 帧 / 可见集）留在
``infra.tool_manager`` 里，这里只负责把它包成「随作用域生灭」的绑定——
modules → infra 是正常方向，反过来会让 infra 依赖业务层。

范围：无权限模型、无签名校验、无隔离。按既定策略，插件代码在宿主进程内以
解释器的全部权限运行。代替信任边界的是可撤销、失败不留半挂载、以及下面这条
不变量：**模型可见的 == 日志里查得到的**。
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, Callable, ClassVar, Dict, List, Optional

from infra import ports
from infra.tool_manager import service_registry
from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import get_logger

__all__ = [
    "ROOT",
    "Ctx",
    "DuplicateServiceError",
    "Plugin",
    "UnknownServiceError",
    "install",
    "mounted",
    "unmount",
]

logger = get_logger("plugin_api")

_MISSING = object()


class DuplicateServiceError(KeyError):
    """同一个作用域里这个槽已经被占了。"""


class UnknownServiceError(KeyError):
    """整条作用域链上没有任何一层提供这个服务。"""


class Ctx:
    """具名服务槽 + LIFO 撤销栈 + 子作用域。"""

    def __init__(self, name: str = "root", parent: "Optional[Ctx]" = None, *,
                 takeover: bool = False) -> None:
        self.name = name
        self.parent = parent
        # takeover：本作用域的绑定是否是「明确接管」。默认 False，也就是两个互不
        # 知情的提交抢同一个名字会被注册表拒绝；按持久化顺序重新恢复时调用方知道自己
        # 是在接管，那份保护就成了障碍。
        self.takeover = takeover
        self._services: Dict[str, Any] = {}
        self._children: Dict[str, "Ctx"] = {}
        self._effects: List[Callable[[], Any]] = []
        self._closed = False

    def __repr__(self) -> str:
        chain = self.name if self.parent is None else f"{self.parent!r} > {self.name}"
        return f"<Ctx {chain} services={sorted(self.visible())}{' closed' if self._closed else ''}>"

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def path(self) -> str:
        """``root/plugin/sub`` —— 槽地址里稳定的那一段，日志和报错都用它。"""
        return self.name if self.parent is None else f"{self.parent.path}/{self.name}"

    # ── 服务槽 ──────────────────────────────────────────────────────────

    def provide(self, key: str, value: Any) -> None:
        """在*本*作用域占一个槽。遮蔽父层可以，同一层占两次不行。"""
        self._check_open()
        if key in self._services:
            raise DuplicateServiceError(
                f"服务 {key!r} 在作用域 {self.path!r} 里已经有人提供了"
            )
        self._services[key] = value

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """由内向外沿父链解析。"""
        scope: Optional[Ctx] = self
        while scope is not None:
            if key in scope._services:
                return scope._services[key]
            scope = scope.parent
        if default is _MISSING:
            raise UnknownServiceError(
                f"作用域链 {self.path!r} 里没有服务 {key!r}；可见：{sorted(self.visible())}"
            )
        return default

    def __contains__(self, key: str) -> bool:
        scope: Optional[Ctx] = self
        while scope is not None:
            if key in scope._services:
                return True
            scope = scope.parent
        return False

    def visible(self) -> set:
        """从这里能解析到的所有槽名。"""
        names: set = set()
        scope: Optional[Ctx] = self
        while scope is not None:
            names.update(scope._services)
            scope = scope.parent
        return names

    # ── 效应栈 ──────────────────────────────────────────────────────────

    def effect(self, disposer: Callable[[], Any]) -> Callable[[], Any]:
        """压入一个撤销操作，关闭时后进先出地执行。

        同步、异步都收：返回可等待对象的会被 await，所以
        ``ctx.effect(ToolRegistry.provide(...))`` 和一个 async 清理函数能躺在
        同一个栈里。
        """
        self._check_open()
        self._effects.append(disposer)
        return disposer

    def child(self, name: str, *, takeover: bool = False) -> "Ctx":
        """开一个嵌套作用域，随本作用域一起关闭。"""
        self._check_open()
        if name in self._children:
            raise DuplicateServiceError(f"子作用域 {name!r} 在 {self.path!r} 里已经开着")
        scope = Ctx(name, parent=self, takeover=takeover)
        self._children[name] = scope
        # 作为效应压栈，让子作用域和自身的绑定共用一个后进先出顺序。
        self._effects.append(scope.aclose)
        return scope

    async def aclose(self) -> None:
        """后进先出地撤销本作用域的一切，然后从父层摘掉。

        某个 disposer 抛了也要把剩下的走完——半撤销的作用域比一个报错的作用域
        更糟。第一个异常最后再抛，其余的挂在 notes 上，因为「卸载时这里泄漏了」
        正是排查时最需要看见的那一行。
        """
        if self._closed:
            return
        self._closed = True
        first_error: Optional[BaseException] = None
        for disposer in reversed(self._effects):
            try:
                result = disposer()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:  # noqa: BLE001 — 见 docstring
                if first_error is None:
                    first_error = exc
                else:
                    _add_note(first_error, f"撤销 {self.path!r} 时又出错：{exc!r}")
        self._services.clear()
        self._effects.clear()
        if self.parent is not None:
            self.parent._children.pop(self.name, None)
            # 把「开启本作用域」那条效应也摘掉。不摘的话每次挂/卸都会在父层
            # 留一个死作用域——挂载还是启动事件时看不见，一旦它变成对存活
            # 服务反复做的动作（模型自制插件），就是无界泄漏。
            self.parent._effects = [
                disposer for disposer in self.parent._effects if disposer != self.aclose
            ]
        if first_error is not None:
            raise first_error

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"作用域 {self.path!r} 已关闭")

    # ── 插件真正需要的两种绑定 ──────────────────────────────────────────

    def provide_tool(self, name: str, func: Callable[..., Any], **meta: Any) -> None:
        """占一个工具，随本作用域生灭。

        走 ``ToolRegistry.provide``。名字已被内置工具占用时是**有意遮蔽**（这就
        是插件换实现的机制），被另一个插件占用同一名字时由注册表拒绝——两个互不
        知晓的提交共用一个工具名是 bug，不是配置。

        绑定直接写在进程级注册表上，没有 per-run 帧：本仓读 ``_tools`` 的地方有
        二十多处、跨 6 个模块（含 tool_discovery、权限过滤、model_runner），把它
        们统一到「每轮冻结一次视图」是另一个量级的改动，不在这一层里顺手做。后
        果要写清楚：运行中途卸载插件，剩下的工具调用会解析回内置实现。
        """
        logger.info(f"[plugin] {self.path}: 挂上工具 {name}")
        self.effect(
            ToolRegistry.provide(
                name, func, plugin_name=self.path, replace=self.takeover, **meta
            )
        )

    def provide_capability(self, name: str, provider: Callable[..., Any]) -> None:
        """换一个业务能力的实现，随本作用域生灭。

        ``service_registry`` 是 infra 侧的能力端口（原本靠 bootstrap 在启动期一次
        性填满）。接管动作由端口自己给出撤销操作——它才知道原值是什么，这里只
        负责挂时效。infra 工具层的反向依赖本来就被这个端口切断，插件换实现同样
        不该让 infra 反过来 import modules。
        """
        logger.info(f"[plugin] {self.path}: 接管能力 {name}")
        self.effect(service_registry.provide_capability(name, provider))

    def provide_port(self, name: str, provider: Any) -> None:
        """接管一个横切端口（``security.tool_gate`` 这种点号名），随本作用域生灭。

        本项目其实有两套端口注册表，插件得看得见第二套才行：
        ``service_registry`` 装业务能力（bootstrap 在启动期填满，蛇形名），而
        ``infra/ports.py`` 装横切契约——安全门、权限控制器、身份表、感知系统都由提供方
        模块在自己的 import 期登记。之前只有前一套有接管入口，于是「候选缝全部可切换」
        这句话实际只覆盖了业务那一半：安全门这类最要紧的换实现点反而没人能换。

        名字必须是点号名（不是就直接报错）：两套表按名字形状分工，写成蛇形名的话端口
        表里多一条永远没人 ``resolve`` 的死登记，插件作者却会以为换掉了实现。
        """
        if "." not in name:
            raise ValueError(
                f"{name} 不是横切端口名（点号名，如 security.tool_gate）；"
                f"业务能力请用 ctx.provide_capability"
            )
        logger.info(f"[plugin] {self.path}: 接管横切端口 {name}")
        self.effect(ports.provide(name, provider))


class Plugin:
    """插件基类。子类写 ``name`` 和 ``apply``。"""

    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"

    def apply(self, ctx: Ctx) -> Any:
        """把本插件提供的一切都装上。同步或异步都行。

        这是唯一允许做绑定的地方：``__init__`` 跑在作用域存在之前，而 import
        期永远不会被反过来。
        """


ROOT = Ctx("root")


async def install(plugin: Plugin, *, root: Optional[Ctx] = None, name: Optional[str] = None,
                  takeover: bool = False) -> Ctx:
    """装上 *plugin*，返回它的作用域——卸载要用这个把手。

    ``takeover=True`` 表示这次挂载是「按已知顺序重新接管」（重启后恢复持久化的
    提交），不是两个互不知情的提交撞在一起。
    """
    if not (plugin.name or name):
        raise ValueError("Plugin 需要名字（类属性 name 或 install(name=...)）")
    parent = root or ROOT
    ctx = parent.child(name or plugin.name, takeover=takeover)
    try:
        result = plugin.apply(ctx)
        if inspect.isawaitable(result):
            await result
    except BaseException:
        # 半挂载比挂载失败更糟：apply 走到一半抛了，前面装上的东西必须撤回。
        await ctx.aclose()
        raise
    return ctx


async def unmount(ctx: Ctx) -> None:
    await ctx.aclose()


@asynccontextmanager
async def mounted(plugin: Plugin, *, root: Optional[Ctx] = None, name: Optional[str] = None):
    """在一个代码块期间装上插件，出块必卸。

    块体抛异常也要卸。需要活过本次挂载的运行自己开 ``tool_scope()`` 并往里
    绑定，这样它的绑定住在那个帧里，而不是住在被卸载重写的注册表里。
    """
    ctx = await install(plugin, root=root, name=name)
    try:
        yield ctx
    finally:
        await unmount(ctx)


def _add_note(exc: BaseException, note: str) -> None:
    """``BaseException.add_note`` 是 3.11+，本仓可跑的 3.9 环境上要降级。

    写成 ``__notes__`` 即可：3.11+ 的 traceback 渲染读的就是这个属性，3.9 上
    则只是个普通属性——比丢掉第二条错误好。
    """
    notes = getattr(exc, "__notes__", None)
    if notes is None:
        notes = []
        exc.__notes__ = notes
    notes.append(note)
