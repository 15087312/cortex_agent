"""缝隙目录生成器 — 「插件今天到底能接管什么」只能从代码里算出来。

为什么要有这个文件：本项目不做插件安全（无沙箱、无权限声明、允许一切代码），
于是「一个插件能碰到什么」没有声明可查，唯一可信的答案是从源码里读出来的调用
图。手写清单会漂：端口加了一个、少接了一个，清单不会告诉你，而模型按清单写插件
就会撞上「这个名字其实是硬 import 的，接管不到」。

算的是三件事：

1. **端口**（``bootstrap._safe_register("name", factory)`` 注册的能力）：端口名、
   provider 是谁、有哪些文件真的通过 ``get_capability("name")`` 取它。没有消费者
   的端口是死端口——它声明了可接管，但代码根本不经过它。
2. **单例访问器**（模块级 ``def get_x()`` 且返回模块级名字，或 ``@classmethod
   get_instance``）：谁硬 import 了它。硬 import 的读者越多，这一处就越不像能接管
   的样子——插件挂上端口也换不掉这些调用点。
3. **端口优先访问器**（函数体里写了 ``get_capability("name")``，取不到才回退内置）：
   这是「把一条缝降级成端口」之后的标准形状——调用点全都写在访问器上，插件接管
   端口就换掉全部调用点，所以它的读者**不算绕过端口**，算「在这条端口的路上」，
   条数单独报出来供人核对。一个函数经多个端口取用则不算（分不清它属于哪条，宁可不
   说也不瞎归）。同一张表还会**拒绝**这样一种接线：某端口的 provider 恰好就是那条
   端口优先访问器自己——那是一调用就自我递归，写文档看不出来，只能当场算出来。
4. **import 期快照**（模块级 ``x = get_y()``）：实现被抓在 import 那一刻，之后接管
   端口换不掉这个变量，而它长得和普通模块变量一模一样——用它的文件算作那条端口
   **没接上**的读者。只认模块层这一种写法；函数里现场取的每次都重读端口，不算。
5. **访问器 ↔ 端口的对应**：provider 体里 import / 调用到谁，那条端口包的就是谁。
   对不上号的访问器就是还没降级的特权核心；对上了号却还有硬 import 读者的，是
   **只接了一半**——比没有端口更难查，所以单独一张表。

读者怎么算：每个文件里的引用点先解析成 ``模块:成员.属性`` 的符号记成边，最后再把
边归到「记录」上。记录 = 访问器 ∪ 端口包装到的符号，一条边只落到**最具体**的那个
记录（先精确名，再最长前缀）。为什么要等到最后：端口包的常常不是 ``get_x()`` 而是
模块级对象（``mgr.go()``），这种边带方法名后缀，只对着访问器表当场查会一条都落不上，
于是那一处硬 import 凭空消失，目录就把「只接了一半」说成「已经可切换」。

三遍扫描：先收所有模块 id（才分得清 ``from m import sub`` 是子模块还是符号），再收
每个 ``__init__.py`` 的转口表（``from pkg import name`` 要能追到定义处），最后才逐文件
记引用。相对导入（``from .x import y``）按文件所在目录展开——本仓库一百多处，不展
开就是一片读者凭空消失。漏算比报错更糟，所以解析失败直接抛，不静默跳过；解析不出
来的调用（第三方库、局部同名遮蔽）算「不归本工具管」，但它们不会因此被说成可接管。

已知口径限制（都偏向「说得不够」而不是「说得多」）：端口名要写成字符串字面量才
认得到消费者；``getattr`` 动态取的名字不算读者；定义访问器的那个文件自己引用不算
读者（那是实现内部）；符号解析不出来的（第三方、局部同名遮蔽）也不算。

只读，不 import 被扫描的代码（只用 stdlib + ast），因此在任何解释器下都能跑，也不
会因为某个模块 import 失败而漏算——漏算的东西不该伪装成「没有」。

用法::

    python scripts/seam_catalogue.py                       # markdown
    python scripts/seam_catalogue.py --json                # 机器读
    python scripts/seam_catalogue.py --out docs/SEAMS.md   # 写文档
    python scripts/seam_catalogue.py --check               # 文档与代码漂了就退出 1
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("modules", "infra", "config", "api", "cortex", "cli_tui", "utils", "tools")
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".venv", "migrations"}
# 第二套端口注册表所在模块（点号名端口）。它自己不参与缝隙统计，但它的
# register/resolve 是端口接线/取用的证据。
PORTS_MODULE = "infra.ports"
BUSINESS_SYSTEM = "业务"
PORTS_SYSTEM = "门面"


def _doc_path() -> Path:
    """生成物落在 ``docs/SEAMS.md``。做成函数而不是常量：测试要把 ROOT 换到临时树。"""
    return ROOT / "docs" / "SEAMS.md"

#: 访问器符号的规范形式是 ``模块 id:成员``，成员可以是 ``get_x`` 或 ``Class.get_instance``。
SEP = ":"


def _bootstrap_path() -> Path:
    pkg = ROOT / "bootstrap" / "__init__.py"
    return pkg if pkg.exists() else ROOT / "bootstrap.py"


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _sources() -> List[Path]:
    files: List[Path] = []
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if SKIP_DIR_NAMES & set(path.parts):
                continue
            files.append(path)
    return sorted(files)


def _module_id(path: Path) -> str:
    """``modules/thinking/x.py`` -> ``modules.thinking.x``（``__init__`` 归到包名）。"""
    parts = list(path.relative_to(ROOT).parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _pkg_base(path: Path) -> str:
    """文件所在目录的 dotted 名 —— 相对导入的起点。

    ``modules/a/__init__.py`` 和 ``modules/a/thing.py`` 的起点都是 ``modules.a``：
    前者的 ``from .x`` 指包里的兄弟模块，后者的 ``from .x`` 也是，语义一致。
    """
    parts = list(path.relative_to(ROOT).parts)
    parts.pop()
    return ".".join(parts)


def _symbol(module: str, member: str) -> str:
    return f"{module}{SEP}{member}"


def _is_classmethod(fn: ast.FunctionDef) -> bool:
    return any(
        (isinstance(dec, ast.Name) and dec.id == "classmethod")
        or (isinstance(dec, ast.Attribute) and dec.attr == "classmethod")
        for dec in fn.decorator_list
    )


def _dotted(node: ast.AST) -> Optional[str]:
    """``a.b.c`` -> ``"a.b.c"``；根不是裸名字时返回 None（对象属性，不归本工具管）。"""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _owner_symbols(node: ast.AST, parent: dict, module: str) -> List[str]:
    """这个引用所在的**每一层**函数，从最里层往外：``Cls.method`` / ``func``。

    逐层都要记：转口函数的函数体里再套一层闭包时，只记最内层的话，从 provider 出发
    走不到被它转口的那个访问器，自我递归的端口就检不出来。
    """
    out: List[str] = []
    cur = parent.get(node)
    seen = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            holder = None
            up = parent.get(cur)
            while isinstance(up, ast.AST):
                if isinstance(up, ast.ClassDef):
                    holder = up.name
                    break
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
                up = parent.get(up)
            out.append(f"{holder}.{cur.name}" if holder else cur.name)
        cur = parent.get(cur)
    return [_symbol(module, name) for name in out]

def _local_bindings(fn: ast.FunctionDef) -> Set[str]:
    """函数内自己绑住的名字：参数、赋值目标、for/with/as/推导式/walrus 的目标。

    ``global``/``nonlocal`` 声明的名字除外——那些恰恰是指回模块级。不区分这两者就会
    误判：``setup_logger`` 末尾 ``return logger``，而 ``logger`` 同时是模块级变量名，
    看着像「把模块级单例交出去」，其实返回的是现场新建的日志器，列进目录就是假缝隙。
    """
    bound: Set[str] = set()
    declared: Set[str] = set()
    a = fn.args
    for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
        if arg is not None:
            bound.add(arg.arg)
    for item in ast.walk(fn):
        if isinstance(item, (ast.Global, ast.Nonlocal)):
            declared |= set(item.names)
        elif isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
            bound.add(item.id)
        elif isinstance(item, ast.ExceptHandler) and item.name:
            bound.add(item.name)
    return bound - declared


def _capability_literals(fn: ast.FunctionDef) -> Set[str]:
    """函数体里以字面量端口名调用的 ``get_capability(...)``。

    只看 ``get_capability`` 这个名字没被函数内重新绑过的调用：``get_capability = 别的``
    之后再调用就不是端口取用了，把它算成消费者会把「没人经端口取」说成「在用」。
    """
    if "get_capability" in _local_bindings(fn):
        return set()
    out: Set[str] = set()
    for item in ast.walk(fn):
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "get_capability"
            and item.args
            and isinstance(item.args[0], ast.Constant)
            and isinstance(item.args[0].value, str)
        ):
            out.add(item.args[0].value)
    return out


def _annotation_nodes(tree: ast.AST) -> Set[int]:
    """类型标注子树里的节点 id 集合。

    ``def _get_store(self) -> EventStore`` 和 ``store: EventStore = None`` 里的类名是
    **文档**，不是取用：接管端口后返回的对象未必是那个类的实例，标注就此失真，但该行
    为一点没变——它没有「绕过端口」。把它和真读者混在一列里，目录就会说「这个端口还有
    5 个文件绕着」，而实际上一个都没有；照着表去找绕过点的人白找一圈，回来就说这表不准。
    所以标注单独一列：仍然列出来（名字写死在该文件里是事实，改不改由人判断），
    但不计入「绕过」。
    """
    out: Set[int] = set()
    for node in ast.walk(tree):
        fields: List[Optional[ast.AST]] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            fields = [node.returns]
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None:
                    fields.append(arg.annotation)
        elif isinstance(node, ast.AnnAssign):
            fields = [node.annotation]
        for field in fields:
            if field is not None:
                out |= {id(item) for item in ast.walk(field)}
    return out


@dataclass
class Accessor:
    """一处「全局单例入口」的定义（读者归属在 ``build()`` 里做，不在这里）。"""

    module: str
    member: str          # ``get_x`` 或 ``Class.get_instance``
    file: str
    kind: str            # module_singleton / class_get_instance / port_first

    @property
    def symbol(self) -> str:
        return _symbol(self.module, self.member)


class Scanner:
    """扫一遍源码：认定义、认绑定、认调用点归属。

    调用点先全部记成边（``symbol -> 读者文件``），等 bootstrap 的端口也算进「值得关
    注的符号」之后再一次归属。反过来做（只认已知的单例访问器）会漏：端口包的东西
    往往不是 ``get_x()`` 而是模块级对象，漏算一个读者，目录就会把「只接了一半」的
    地方说成「已经可切换」。
    """

    def __init__(self, known_modules: Set[str]) -> None:
        self._known_modules = known_modules
        self.by_symbol: Dict[str, Accessor] = {}
        self.port_factories: Dict[str, Tuple[str, Set[str]]] = {}
        self.port_consumers: Dict[str, Set[str]] = {}
        # 第二套端口注册表：``infra/ports.py`` 的点号端口（``security.gate`` 这种横切
        # 契约）。它们和 bootstrap 填的业务端口是两条独立的路，之前只扫后者，于是
        # 「58 条端口全部可接管」这句话其实漏了安全门、感知系统、身份表那一半。
        self.port_registrations: Dict[str, Tuple[str, Set[str]]] = {}
        self.port_system: Dict[str, str] = {}
        self.str_consts: Dict[str, str] = {}   # 模块级字符串常量：符号 -> 字面量
        self.call_edges: List[Tuple[str, str]] = []   # (解析出的符号, 读者文件)
        self.ann_edges: List[Tuple[str, str]] = []    # 只在类型标注里出现的引用
        self.port_first: Dict[str, str] = {}    # 端口优先访问器：符号 -> 它经的端口名
        self.calls: Dict[str, Set[str]] = {}    # 谁调用谁（同一文件内可解析的边）
        self.snapshots: Dict[str, str] = {}     # 模块级快照：名字 -> 它当场取的东西
        self.reexports: Dict[str, str] = {}     # 包转口：``包:名字`` -> 定义处符号
        self._bindings: Dict[str, str] = {}   # 本文件局部名 -> 模块 id 或 符号 id
        self._local_defs: Set[str] = set()    # 本文件自己定义的函数/类名
        self._module = ""                     # 本文件的模块 id

    # ── 绑定 ─────────────────────────────────────────────────────────

    def _bind(self, node: ast.AST, base: str) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    self._bindings[alias.asname] = alias.name
                else:
                    self._bindings[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            origin = _relative_origin(base, node.level, node.module)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                child = f"{origin}.{alias.name}" if origin else alias.name
                self._bindings[local] = (
                    child if child in self._known_modules else _symbol(origin, alias.name)
                )

    def _file_bindings(self, tree: ast.AST, base: str) -> Dict[str, str]:
        saved, self._bindings = self._bindings, {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._bind(node, base)
        out = dict(self._bindings)
        self._bindings = saved
        return out

    def _canon(self, symbol: str) -> str:
        """跟着包的转口走一跳：``from pkg import name`` 用的是 ``pkg`` 里那个名字，
        真正定义在别的模块。不追这一跳，经包名取用的读者就全丢了，而它们和直接
        import 定义处是同一回事。转口成环时停在第一次见过的符号上。"""
        seen = set()
        while symbol in self.reexports and symbol not in seen:
            seen.add(symbol)
            symbol = self.reexports[symbol]
        return symbol

    def index_reexports(self, path: Path) -> None:
        """先把每个 ``__init__.py`` 的转口表建起来，再扫读者（读者归属要用它）。"""
        if path.name != "__init__.py":
            return
        module = _module_id(path)
        for local, target in self._file_bindings(
            ast.parse(path.read_text(encoding="utf-8")), _pkg_base(path)
        ).items():
            if SEP in target and target.split(SEP, 1)[0] != module:
                self.reexports[_symbol(module, local)] = target

    def index_str_consts(self, path: Path) -> None:
        """模块级 ``NAME = "字符串"``。点号端口的名字常写成这种常量再传给 register/resolve。

        这一遍必须**先于**逐文件扫描：``infra/security_gate.py`` 在自己的文件里定义常量、
        也在同一个文件里用它，但别处（以及测试）会 ``from ... import PORT_GATE``，
        晚一步就解不出端口名，那条端口整个从表里消失。
        """
        module = _module_id(path)
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            # 和 scan_file 同样的口径：解析不了就炸。这一遍跑在逐文件扫描**之前**，
            # 少了这层包装，坏文件会先以裸 SyntaxError 冒出来，而「漏算比报错更糟」这条
            # 主张的判据是「以目录自己的错炸」，不是「以任何错炸」。
            raise RuntimeError(f"{_rel(path)} 解析失败：{exc}") from exc
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.str_consts[_symbol(module, target.id)] = node.value.value

    def _resolve(self, dotted: str) -> Optional[str]:
        """调用目标 -> 符号（已跟过包转口）；解析不出来返回 None。

        转口要在**拼上方法名之前**跟：``from pkg import mgr`` 之后写 ``mgr.run()``，
        拼出来的是 ``pkg:mgr.run``，而转口表记的是 ``pkg:mgr`` —— 拼完再查就一条都对
        不上，经包名取用的读者又回到凭空消失。

        返回 ``None`` 的两种情况是有意的：绑到的是第三方（不认识），以及整段点号只是
        「取一个模块」而不是取一个名字（``import a.b`` 后写 ``a.b``）——后者不是缝隙。
        """
        parts = dotted.split(".")
        for cut in range(len(parts), 0, -1):
            head = ".".join(parts[:cut])
            target: Optional[str] = None
            if cut == 1:
                target = self._bindings.get(parts[0])
                if target is None and parts[0] in self._local_defs:
                    # 本文件自己定义的函数/类。跨文件的边只认 import 绑定，所以这一跳
                    # 只会造出「文件内」的边——而 ``x = get_x()`` 这种模块级快照取的
                    # 正是本文件里的访问器，不认这一跳它就永远是「没检测出来」，目录会
                    # 把还冻在 import 那一刻的路说成已经可切换。
                    target = _symbol(self._module, parts[0])
            if target is None and head in self._known_modules:
                target = head
            if target is None:
                continue
            target = self._canon(target)
            rest = ".".join(parts[cut:])
            if SEP in target:                     # 绑到某模块里的符号（类或函数）
                module, member = target.split(SEP, 1)
                return _symbol(module, f"{member}.{rest}" if rest else member)
            return _symbol(target, rest) if rest else None
        return None

    # ── 单文件 ───────────────────────────────────────────────────────

    def scan_file(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            raise RuntimeError(f"{_rel(path)} 解析失败：{exc}") from exc
        module = _module_id(path)
        here = _rel(path)
        self._bindings = {}
        self._module = module
        self._local_defs = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._bind(node, _pkg_base(path))
        top_names: Set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                top_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                top_names.add(node.target.id)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                acc = self._module_accessor(node, module, here, top_names)
                if acc:
                    self._add(acc)
                literals = _capability_literals(node) | self._port_literals(node)
                if len(literals) == 1:
                    # 只经一个端口取用才算「端口优先访问器」；经多个的分不清属于哪条，
                    # 宁可不归类（它们照旧进 port_consumers，所以端口不会显示成死的）。
                    self.port_first[_symbol(module, node.name)] = next(iter(literals))
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (
                        isinstance(item, ast.FunctionDef)
                        and _is_classmethod(item)
                        and item.name in {"get_instance", "instance"}
                    ):
                        self._add(
                            Accessor(
                                module=module,
                                member=f"{node.name}.{item.name}",
                                file=here,
                                kind="class_get_instance",
                            )
                        )
                    if isinstance(item, ast.FunctionDef) and _is_classmethod(item):
                        # 端口优先也可以写在类方法里：``Cls.get_instance()`` 自己先查
                        # 端口，全部调用点（本仓库 82 处）就一处不改也能被接管。在外面
                        # 另起一个 ``get_x()`` 再迁移每个调用点是同一件事的十倍工作量。
                        literals = _capability_literals(item) | self._port_literals(item)
                        if len(literals) == 1:
                            self.port_first[
                                _symbol(module, f"{node.name}.{item.name}")
                            ] = next(iter(literals))
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                # 模块级快照：``db_manager = get_db_manager()`` 在 import 那一刻就把
                # 实现抓住了。之后接管端口换不掉它，而它长得和普通模块变量一样，是
                # 这类接线里最难发现的一种——所以单独记一笔，按端口包着的东西来算读者。
                called = self._resolve(_dotted(node.value.func) or "")
                if not called:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and "." not in target.id:
                        self.snapshots[_symbol(module, target.id)] = called
        self._record_uses(tree, here, module)

    @staticmethod
    def _module_accessor(
        node: ast.FunctionDef, module: str, here: str, top_names: Set[str]
    ) -> Optional[Accessor]:
        """模块级 ``def get_x()``：只有把模块级名字**交出去**的才算单例入口。

        判据是「返回一个模块级、且函数内没有重新绑过的名字」——把全局递出来的函数
        才有可以换掉的东西；只是动一下模块状态、返回现场造的对象的不算（列进去会把
        目录淹成看不出重点的噪音，见 :func:`_local_bindings`）。
        """
        bound = _local_bindings(node)
        handed_out = {
            item.value.id
            for item in ast.walk(node)
            if isinstance(item, ast.Return) and isinstance(item.value, ast.Name)
        }
        if not ((handed_out - bound) & top_names):
            return None
        return Accessor(module=module, member=node.name, file=here, kind="module_singleton")

    def _add(self, acc: Accessor) -> None:
        self.by_symbol.setdefault(acc.symbol, acc)

    # ── 第二套端口：infra/ports.py 的点号端口 ────────────────────────────

    def _ports_fn(self, func: ast.AST) -> Optional[str]:
        """这个调用的被调方是不是 ``infra.ports.register`` / ``.resolve``。

        三种写法都要认出来：``ports.register(...)``、``_ports.register(...)``（别名）、
        以及 ``from infra.ports import resolve`` 之后的裸 ``resolve(...)``。判据是被调方
        解析到的符号落在 ``infra.ports`` 里——只看名字会误伤 ``ToolRegistry.register``
        这种同名方法，而那条注册和端口毫无关系。

        属性写法要按**模块**判，不能拿 :meth:`_resolve` 的结果比：``ports.register`` 里
        ``ports`` 解出来的是「一个模块」，而 `_resolve` 对纯模块引用是**故意**返回 None 的
        （它只认取到名字的那种）。用它的返回值比就等于永远不匹配，安全门那一整根 9 条
        端口一条也登记不上——而目录不会报错，只会安静地说「这些端口不存在」。
        """
        dotted = _dotted(func)
        if not dotted:
            return None
        if isinstance(func, ast.Attribute):
            base = _dotted(func.value)
            if not base:
                return None
            module = self._module_of_chain(base)
            return func.attr if module == PORTS_MODULE else None
        target = self._resolve(dotted)
        if target and target.startswith(f"{PORTS_MODULE}{SEP}"):
            return target.split(SEP, 1)[1]
        return None

    def _module_of_chain(self, chain: str) -> Optional[str]:
        """``ports`` / ``_ports`` / ``infra.ports`` -> 模块 id；绑的不是模块则 None。

        ``import infra.ports`` 的绑定只记了首段（``infra -> infra``），后面的点号要自己
        接回去；``from infra import ports as _ports`` 的绑定直接就是完整模块 id。两种
        写法都得认，否则同一份注册代码会因为作者选了哪种 import 而时隐时现。
        """
        parts = chain.split(".")
        target = self._bindings.get(parts[0])
        if not target or SEP in target:
            return None
        return ".".join([target, *parts[1:]])

    def _port_name(self, node: ast.AST) -> Optional[str]:
        """端口名：字面量，或指向字符串常量的名字（``PORT_GATE`` 这种）。

        点号端口的名字大量以模块级常量存在（``PORT_IDENTITIES = "security.identities"``）。
        只认字面量的话这一整类端口一条都进不了表——而「表里没有」会被读成「没有缝」。
        常量表要用**定义处**的符号 id 查：``infra/security_gate.py`` 里 9 个端口名全是本文件
        常量、也在本文件里被用，而它们既不在 import 绑定里（没人 import 自己），也不在
        ``_local_defs`` 里（那只收函数和类）——不补这一跳，安全门那整根一条都登记不上。
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        dotted = _dotted(node)
        if not dotted:
            return None
        symbol = self._resolve(dotted)
        if symbol is None and isinstance(node, ast.Name):
            symbol = _symbol(self._module, node.id)
        if symbol:
            value = self.str_consts.get(symbol)
            if value:
                return value
        return None

    def _record_ports_call(self, node: ast.Call, here: str) -> None:
        """把 ``ports.register/resolve`` 记成端口接线与取用。

        为什么两套注册表要合成一张表：插件接管业务端口就换得掉实现，换不掉门面端口
        （安全门、感知系统、身份表那一半）。分成两张表看会让人以为「58 条端口全部可
        接管」这句话成立——目录把第二套整个省掉时，「表里没有」会被读成「没有缝」，
        而这恰恰是漏掉的那一半。所以它们进同一张端口表，只多标一列「体系」。

        provider 是函数参数时（``def register_gate(provider): ports.register(PORT_GATE,
        provider)``）包到的东西当场解不出来——那是**转口登记**，端口仍然算活着、仍然
        列出来，只是 provider 显示成参数名。不猜它包了什么：猜出来的 wrapped 列表会让
        目录说「接管这条就换掉那些」，而那些其实跟它没关系。
        """
        kind = self._ports_fn(node.func)
        if kind not in {"register", "resolve"} or not node.args:
            return
        name = self._port_name(node.args[0])
        if name is None:
            return
        self.port_system.setdefault(name, PORTS_SYSTEM)
        if kind == "resolve":
            self.port_consumers.setdefault(name, set()).add(here)
            return
        if len(node.args) < 2:
            return
        ref = _dotted(node.args[1]) or "?"
        # ``ports.as_factory(...)`` 这类注册表自身的成员不是被包的实现，列进去会把
        # 「infra.ports」当成某条端口的内容物。
        symbols = {
            s
            for s in self._factory_symbols(node.args[1])
            if s.split(SEP, 1)[0] != PORTS_MODULE
        }
        previous = self.port_registrations.get(name)
        if previous is None:
            self.port_registrations[name] = (ref, symbols)
        else:
            # 同名端口登记两次：谁生效取决于 import 顺序，静态层面分不出胜负。只留最后
            # 一次会把前一份实现从表里抹掉，而它恰恰可能是唯一真正被 import 的那份。
            # provider 那一格把两次都写出来，免得只显示其中一个。
            shown = previous[0] if ref in previous[0].split(", ") else f"{previous[0]}, {ref}"
            self.port_registrations[name] = (shown, previous[1] | symbols)

    def _port_literals(self, fn: ast.AST) -> Set[str]:
        """函数体里取用的点号端口名（``ports.resolve(PORT_GATE)``）。

        和 :func:`_capability_literals` 同一套判据：名字被函数内重新绑过就不算取用，
        分不清属于哪条端口（用到两条）时宁可不归类。两套端口都要认，否则 security_gate
        那批「端口优先取实现」的访问器会被列成硬 import 读者——它明明已经在端口路上。
        """
        bound = _local_bindings(fn)
        out: Set[str] = set()
        for item in ast.walk(fn):
            if not isinstance(item, ast.Call) or not item.args:
                continue
            if isinstance(item.func, ast.Name) and item.func.id in bound:
                continue
            if self._ports_fn(item.func) != "resolve":
                continue
            name = self._port_name(item.args[0])
            if name:
                out.add(name)
        return out

    def _record_uses(self, tree: ast.AST, here: str, module: str) -> None:
        """引用点先记成边，不在这里归属。

        归属要等到 bootstrap 的端口也算进「值得关注的符号」之后才能做：端口包装的
        往往不是 ``get_x()`` 而是模块级对象（``from ... import mgr`` 然后 ``mgr.go()``），
        这一类边解析出的符号带方法名后缀，当场对着访问器表查是查不到的。

        记的是**任何读取**，不只是调用：模块级单例很大一部分用法是读属性、当参数递出
        去（``mgr.run()``、``handler = mgr``、``Cls._instance = None``），只统计调用会漏
        掉这些读者，而漏一个读者就会把「只接了一半」说成「已经可接管」。

        一条点号链只记**最长的那一段**：``Cls.get_instance()`` 是一个引用（访问器），
        不是两个引用（访问器 + 类）。逐段都记会让类的读者数虚增成「凡是用过访问器的都
        算」，第二类缝隙的存在意义就没了——类这一行要说的恰恰是「谁绕开访问器直接碰类」。
        裸名字的赋值/删除左侧（``mgr = Mgr()``）是绑定，不算引用。
        """
        parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        ann_ids = _annotation_nodes(tree)
        # ``get_capability`` 被函数内重新绑过，那一段里的调用就不是端口取用（和
        # :func:`_capability_literals` 同一个判据）。两边口径必须一致，否则一处会把
        # 没人真的经端口取的端口说成「在用」。
        shadowed = {
            id(item)
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "get_capability" in _local_bindings(fn)
            for item in ast.walk(fn)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "get_capability" and node.args:
                    first = node.args[0]
                    if (
                        isinstance(first, ast.Constant)
                        and isinstance(first.value, str)
                        and id(node) not in shadowed
                    ):
                        self.port_consumers.setdefault(first.value, set()).add(here)
                self._record_ports_call(node, here)
            if not isinstance(node, (ast.Name, ast.Attribute)):
                continue
            up = parent.get(node)
            if isinstance(up, ast.Attribute) and up.value is node:
                continue
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                continue
            dotted = _dotted(node)
            if not dotted:
                continue
            symbol = self._resolve(dotted)
            if symbol:
                if id(node) in ann_ids:
                    # 只出现在标注里：名字写死了，但取用没绕过端口（见 _annotation_nodes）
                    self.ann_edges.append((symbol, here))
                    continue
                self.call_edges.append((symbol, here))
                owner = _owner_symbols(node, parent, module)
                if symbol not in owner:
                    # 谁调用谁，只记同一份 AST 里能认出来的边。端口优先访问器现在是
                    # 「在 get_instance() 里面」而不是「在外面包一层」，装配层给的
                    # provider 可能隔着一次转口才撞上它 —— 没有这张调用图，那条
                    # 自我递归的端口就检不出来。外层函数也要记：转口函数的函数体里
                    # 再套一层闭包时，边要能从 provider 走得到访问器。
                    for own in owner:
                        self.calls.setdefault(own, set()).add(symbol)

    # ── bootstrap 的端口 ───────────────────────────────────────────────

    def scan_bootstrap(self) -> None:
        tree = ast.parse(_bootstrap_path().read_text(encoding="utf-8"))
        # provider 是 ``register_business_capabilities()`` 里的**嵌套** ``def _x()``，
        # 不在模块层 body 里——只扫 body 会一条都找不到，端口就永远显示「没包装任何
        # 东西」，而这是这份表最容易看过去的一个洞。
        defs = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "_safe_register" or len(node.args) < 2:
                continue
            port = node.args[0]
            if not (isinstance(port, ast.Constant) and isinstance(port.value, str)):
                continue
            ref = _dotted(node.args[1]) or "?"
            # provider 通常是 bootstrap 里的一个同级 ``def _x()``：要看的是它的**函
            # 数体**，不是 ``_safe_register`` 里那个光秃秃的名字——只走参数会一条都
            # 匹配不上，端口就永远显示「没包装任何东西」。
            self.port_factories[port.value] = (ref, self._factory_symbols(defs.get(ref, node.args[1])))

    def _factory_symbols(self, node: ast.AST) -> Set[str]:
        """provider 体里 import / 引用到的符号：这条端口包的就是它们。"""
        saved, self._bindings = self._bindings, {}
        symbols: Set[str] = set()
        for item in ast.walk(node):
            if isinstance(item, (ast.Import, ast.ImportFrom)):
                self._bind(item, "")
        for item in ast.walk(node):
            # 走 Name/Attribute 本体而不是 Call.func：``return CausalGraph.get_instance``
            # 返回的是**未调用的方法对象**，不是 Call，只看 Call 会漏掉一半端口。
            if not isinstance(item, (ast.Name, ast.Attribute)):
                continue
            dotted = _dotted(item)
            if not dotted:
                continue
            resolved = self._resolve(dotted)
            if resolved:
                symbols.add(resolved)
        self._bindings = saved
        return symbols


def _relative_origin(base: str, level: int, module: Optional[str]) -> str:
    """``from .x import y`` / ``from ..pkg import y`` 的绝对 origin。

    ``base`` 是**当前文件所在目录**的 dotted 名：对 ``__init__.py`` 就是包自己，对
    普通模块就是它所属的包。相对导入不算这一层就会把 ``from .thing import mgr`` 解
    成 ``thing:mgr`` 这种不存在的模块，读者凭空消失（本仓库有 123 处相对 import）。
    """
    if not level:
        return module or ""
    parts = base.split(".") if base else []
    drop = level - 1
    head = ".".join(parts[: len(parts) - drop]) if drop else ".".join(parts)
    if head and module:
        return f"{head}.{module}"
    return head or (module or "")


def _owners(records: List[str]) -> List[str]:
    """长符号在前：一条边落到**最具体**的那个记录，父子记录不重复计同一个读者。"""
    return sorted(records, key=lambda s: (-len(s), s))


def _match(edge: str, owners: List[str]) -> Optional[str]:
    for key in owners:
        if edge == key or edge.startswith(f"{key}."):
            return key
    return None


def build() -> Dict[str, object]:
    files = _sources()
    scanner = Scanner({_module_id(p) for p in files})
    # 先建包转口表再扫读者：`from pkg import name` 的读者要能追到定义处，否则那一处
    # 硬 import 凭空消失，目录就把「只接了一半」说成「已经可接管」。
    for path in files:
        scanner.index_reexports(path)
    # 常量端口名要在逐文件扫描之前建好：``infra/security_gate.py`` 在自己的文件里定义
    # 也使用，晚一遍的话别处（和测试）认不出端口名，那条端口整个从表里消失。
    for path in files:
        scanner.index_str_consts(path)
    for path in files:
        scanner.scan_file(path)
    scanner.scan_bootstrap()
    file_of = {_module_id(p): _rel(p) for p in files}

    ports: List[Dict[str, object]] = []
    port_of: Dict[str, str] = {}
    # 两套注册表合成一张表。业务端口（bootstrap 用 ``register_capability`` 填）和门面
    # 端口（``infra/ports.py`` 的点号名，提供方模块在 import 期自己登记）判据完全共用：
    # 活着吗、包住了什么、谁绕过它。分开列会让人以为「业务端口全部可接管」这句话覆盖
    # 了全部端口，而安全门、感知系统、身份表那一根本来就不在表里。
    registrations = [
        (name, BUSINESS_SYSTEM, factory, symbols)
        for name, (factory, symbols) in sorted(scanner.port_factories.items())
    ] + [
        (name, PORTS_SYSTEM, factory, symbols)
        for name, (factory, symbols) in sorted(scanner.port_registrations.items())
    ]
    # 有人经端口取、却没检测到登记点的端口也要列。门面端口里有一批实现是动态传进来的
    # （``security_gate.register_provider(name, provider)`` 这种转口，name 是参数），静态
    # 层面看不见谁注册了它。不列的话这句话就变成「表里没有 → 没有这条缝」，而这恰恰是
    # 生成器看不见、人看得见的那种洞——目录的职责是把看不见的部分说成看不见。
    registered_names = {name for name, _, _, _ in registrations}
    for name in sorted(scanner.port_consumers):
        if name in registered_names or scanner.port_system.get(name) != PORTS_SYSTEM:
            continue
        registrations.append((name, PORTS_SYSTEM, "(没检测到登记点)", set()))
    for name, system, factory, symbols in registrations:
        for symbol in symbols:
            port_of.setdefault(symbol, name)
        consumers = sorted(scanner.port_consumers.get(name, set()))
        accessors = sorted(s for s in symbols if s in scanner.by_symbol)
        ports.append(
            {
                "name": name,
                "system": system,
                "provider": factory,
                "wrapped": sorted(symbols),
                "wrapped_accessors": accessors,
                "wrapped_objects": sorted(s for s in symbols if s not in accessors),
                "consumers": consumers,
                "live": bool(consumers),
            }
        )

    # 候选缝隙 = 检测到的访问器 ∪ 端口包装到的符号。后者必须进表：端口包的是模块级
    # 对象时它不是访问器，只算访问器的话这些对象的硬 import 读者就凭空消失，表会把
    # 「只接了一半」说成「已经可切换」。
    records: Dict[str, Dict[str, object]] = {}

    def _new_record(symbol: str, kind: str, **extra: object) -> None:
        record = {
            "symbol": symbol,
            "file": file_of.get(symbol.split(SEP, 1)[0]),
            "kind": kind,
            "readers": set(),
            "ann_readers": set(),
            "sites": set(),
            "resolves_to": None,
            "snapshot_port": None,
            "snapshot_of": None,
        }
        record.update(extra)
        records.setdefault(symbol, record)

    for acc in scanner.by_symbol.values():
        _new_record(acc.symbol, acc.kind, file=acc.file)
    for symbol in port_of:
        _new_record(symbol, "object")
    # 端口优先访问器也是记录：它自己就是那条端口的取用形状，调用点全写在它身上。
    # 不列进来的话，迁移完一批调用点后目录会**什么都不说**——「没检测出来」被读成
    # 「没有缝」，正是这份表最不能犯的错。
    for symbol, port in scanner.port_first.items():
        _new_record(symbol, "port_first")
        records[symbol]["resolves_to"] = port

    # 模块级快照挂在「它当场取的那个东西」所属的端口上：端口换了实现，快照不会重取，
    # 所以用它的文件是那条端口**没接上**的读者——哪怕它自己只是个普通变量名。
    for symbol, called in scanner.snapshots.items():
        port = port_of.get(called) or scanner.port_first.get(called)
        _new_record(symbol, "snapshot")
        records[symbol]["snapshot_port"] = port
        records[symbol]["snapshot_of"] = called

    # 装配自举的端口不能由自己的访问器提供：provider 经这条端口取、端口又指向
    # provider，第一次调用就自我递归。这种接线在文档里看不出来，只能当场算出来。
    # 要顺着调用走一跳以上：``get_file_history()`` 自己不查端口，转口去调
    # ``FileHistory.get_instance()``，而后者查的就是 file_history 端口 —— provider 指
    # 向前者同样是端口→provider→访问器→端口 的死循环，只看 provider 那一层会放过它。
    for name, system, factory, symbols in registrations:
        origin = "bootstrap" if system == BUSINESS_SYSTEM else f"{PORTS_MODULE}.register"
        for symbol in sorted(symbols):
            if scanner.port_first.get(symbol) == name:
                raise RuntimeError(
                    f"端口 {name} 的 provider（`{origin}` 包到的 `{symbol}`）"
                    f"自己经 {name} 取用 —— 调用即无限递归；"
                    f"provider 要指向内置实现，端口优先访问器另起一个名字"
                )
            reach, pending, seen = set(), [symbol], {symbol}
            while pending:
                for nxt in scanner.calls.get(pending.pop(), ()):
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    reach.add(nxt)
                    pending.append(nxt)
            turned = sorted(
                s for s in reach if scanner.port_first.get(s) == name
            )
            if turned:
                raise RuntimeError(
                    f"端口 {name} 的 provider（`{origin}` 包到的 `{symbol}`）"
                    f"转口调用 {turned}，而那些调用点查的正是 {name} —— 调用即无限递归；"
                    f"provider 要指向不读端口的内置实现（例如 ``_builtin_instance``）"
                )

    owners = _owners(list(records))
    for edge, reader in scanner.call_edges:
        hit = _match(edge, owners)
        if hit is None or records[hit]["file"] == reader:
            continue
        if records[hit]["resolves_to"] is not None:
            # 调用点在端口路上：接管端口就换掉它，所以它不是「绕过端口的读者」。
            records[hit]["sites"].add(reader)  # type: ignore[attr-defined]
            continue
        records[hit]["readers"].add(reader)  # type: ignore[attr-defined]

    # 标注引用单独归一列：它不构成「绕过」（接管端口换得掉行为，只是那一行的类名
    # 就此失真），但它确实是把名字写死在该文件里的事实，列出来由人判断要不要改。
    for edge, reader in scanner.ann_edges:
        hit = _match(edge, owners)
        if hit is None or records[hit]["file"] == reader:
            continue
        records[hit]["ann_readers"].add(reader)  # type: ignore[attr-defined]

    # 快照抓的是「import 那一刻访问器给的东西」。谁 import 这个快照名字，谁就是那条
    # 访问器的硬 import 读者：端口还没建的，把读者并回访问器名下，因为要做的动作在
    # 访问器那里（建端口 + 让调用点运行时取），挂在快照名字上只是多一行读不懂的人名。
    # 端口已经建好的留在快照自己名下——那一行说的是「这条端口换不掉它」，主体不同。
    portless = []
    for symbol, rec in records.items():
        if rec["kind"] != "snapshot" or rec["snapshot_port"] is not None:
            continue
        hit = records.get(rec["snapshot_of"])
        if hit is not None and hit["kind"] != "snapshot":
            for reader in rec["readers"]:
                if reader != hit["file"]:
                    hit["readers"].add(reader)
            for reader in rec["ann_readers"]:
                if reader != hit["file"]:
                    hit["ann_readers"].add(reader)
        portless.append(symbol)
    for symbol in portless:
        del records[symbol]

    seams: List[Dict[str, object]] = []
    for rec in records.values():
        readers = sorted(rec["readers"])  # type: ignore[attr-defined]
        sites = sorted(rec["sites"])  # type: ignore[attr-defined]
        anns = sorted(rec["ann_readers"])  # type: ignore[attr-defined]
        port = port_of.get(rec["symbol"]) or rec["resolves_to"] or rec["snapshot_port"]
        seams.append(
            {
                "symbol": rec["symbol"],
                "file": rec["file"],
                "kind": rec["kind"],
                "readers": readers,
                "reader_count": len(readers),
                "sites": sites,
                "site_count": len(sites),
                "ann_readers": anns,
                "behind_port": port,
                "resolves_to": rec["resolves_to"],
                # 快照那一格要说它当场取的是哪个访问器，否则「import 期快照」只是一句
                # 没有主语的话——读表的人得自己去文件里找那行赋值。
                "snapshot_of": rec["snapshot_of"],
                # 有端口却仍有硬 import 的读者 = 端口是**部分**接线：接管得到端口，
                # 接管不到这些调用点。这比「完全没有端口」更容易被忽略。
                "bypassed": bool(readers) and port is not None,
            }
        )
    seams.sort(key=lambda s: (-s["reader_count"], s["symbol"]))
    return {
        "generated_by": _rel(Path(__file__).resolve()),
        "scan_dirs": list(SCAN_DIRS),
        "ports": ports,
        "seams": seams,
    }


# ── 渲染 ──────────────────────────────────────────────────────────────


def render_markdown(data: Dict[str, object]) -> str:
    ports = data["ports"]
    seams = data["seams"]
    live = [p for p in ports if p["live"]]
    dead = [p for p in ports if not p["live"]]
    live_names = {p["name"] for p in live}
    accessors = [s for s in seams if s["kind"] != "object"]
    objects = [s for s in seams if s["kind"] == "object"]
    port_first = [s for s in seams if s["resolves_to"]]
    snapshots = [s for s in seams if s["kind"] == "snapshot"]
    plain_accessors = [
        s for s in accessors if not s["resolves_to"] and s["kind"] != "snapshot"
    ]
    ready = [
        s
        for s in seams
        if not s["reader_count"]
        and s["behind_port"] in live_names
        # import 期快照没有「接管端口就跟着换」这回事，它抓的是那一刻的实现；
        # 没有读者时更是死物一条，放进第一档只会让人以为那条路已经通了。
        and s["kind"] != "snapshot"
        # 端口访问器没有外部调用点时不进第一档：它只是那条端口的内部包装，端口
        # 自己那一行已经说过同一件事，列两次会把「可接管的缝」数出虚高的来。
        and (not s["resolves_to"] or s["site_count"])
    ]
    naked = [s for s in seams if not s["behind_port"] and s["reader_count"]]
    bypass = [s for s in seams if s["bypassed"]]
    ann_only = [s for s in seams if s["ann_readers"] and not s["readers"]]
    biz = [p for p in ports if p["system"] == BUSINESS_SYSTEM]
    facade = [p for p in ports if p["system"] == PORTS_SYSTEM]
    lines: List[str] = [
        "# 缝隙目录（生成物，别手改）",
        "",
        f"由 `{data['generated_by']}` 扫描 `{', '.join(data['scan_dirs'])}` 得到。",
        "重跑：`python scripts/seam_catalogue.py --out docs/SEAMS.md`；漂移检查：`--check`。",
        "",
        "本项目不做插件安全，所以「插件能碰到什么」没有声明可查——这份表是从源码的",
        "import/调用关系算出来的，不是人写的。它按「今天能不能真的接管」分三档：",
        "",
        f"- 端口 {len(ports)} 个（业务 {len(biz)} / 门面 {len(facade)}）："
        f"有人经端口取用 {len(live)} 个，**死端口 {len(dead)} 个**",
        f"- 候选缝隙 {len(seams)} 处：单例访问器 {len(plain_accessors)} 处，"
        f"端口优先访问器 {len(port_first)} 处，端口包装的对象 {len(objects)} 处，"
        f"import 期快照 {len(snapshots)} 处",
        f"- ✅ 已可接管（端口在用、无人硬 import）：**{len(ready)} 处**",
        f"- ❌ 还没有端口、又被别的文件硬 import：**{len(naked)} 处**",
        f"- ⚠️ 有端口但代码**行为上**绕过它（只接了一半）：**{len(bypass)} 处**",
        f"- 只在类型标注里点名、行为不绕过的：{len(ann_only)} 处（见文末附表）",
        "",
        "## 端口",
        "",
        "| 端口 | 体系 | provider | 包装的东西 | 经端口取用的文件 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for p in ports:
        wrapped = ", ".join(
            [f"`{w}`" for w in p["wrapped_accessors"]]
            + [f"（对象）`{w}`" for w in p["wrapped_objects"][:2]]
        ) or "—"
        consumers = ", ".join(f"`{c}`" for c in p["consumers"][:4]) or "—"
        if len(p["consumers"]) > 4:
            consumers += f" 等 {len(p['consumers'])} 处"
        state = "在用" if p["live"] else "**死端口**：注册了，但没人经端口取"
        lines.append(
            f"| `{p['name']}` | {p['system']} | `{p['provider']}` | {wrapped} | "
            f"{consumers} | {state} |"
        )
    lines += [
        "",
        "「体系」说的是这条端口由哪套注册表管：**业务** = bootstrap 调",
        "`register_capability` 填的蛇形名端口（`ctx.provide_capability` 直接接管）；"
        "**门面** = 提供方模块在自己 import 期调 `infra/ports.py::register` 登记的点号名",
        "端口（`security.gate` 这种横切契约）。两套的接管入口不同，但「换掉实现」这件事",
        "一样成立，所以判据（在用/死端口/包装了什么/谁绕过）共用同一套算法，合成一张表。",
        "分表列会让「业务端口全部可接管」被读成「全部端口可接管」，而门面那一半本来",
        "不在任何表里。",
        "",
        "「在用」说的是**有文件真的 ``get_capability(name)``**。这只说明有人经端口取，",
        "不等于接管生效——生效还要求同一条路径上没人硬 import 原实现，所以下面按",
        "「硬 import 读者数」再分档。",
        "",
        "「（对象）」是 provider 体里引用到、但不算单例访问器的符号（模块级实例、类、",
        "工具函数）。它们同样被这条端口包着，接管端口就会换掉它们，所以也是一等候选",
        "缝隙；只算访问器会漏掉整类接管点。",
        "",
        "**端口优先访问器**（下表标「端口访问器」的）是降级完成后的标准形状：函数自己",
        "先取端口（业务端口 ``get_capability(name)``，门面端口 ``ports.resolve(name)``），",
        "取不到才回退内置。调用点全都写在它身上，所以它的",
        "调用点**不算绕过端口**（接管端口就换掉了它们），单独按「调用点」计数列出。",
        "它的 provider 必须是内置实现而不是它自己——否则端口→访问器→端口，第一次调用就",
        "无限递归；那种接线本工具直接报错，不写进表里。",
        "",
        "## ✅ 一、已可接管",
        "",
        "端口注册了、有人经端口取用、且没有任何别的文件**取用**这个符号（只在类型标注",
        "里点名不算取用，那类单列在文末附表）。",
        "插件挂上去这条路径的行为就整个换掉：业务端口用 ``ctx.provide_capability(name, ...)``，"
        "门面（点号名）端口用 ``ctx.provide_port(name, ...)``——用哪个由上面端口表的",
        "「体系」那一列决定。",
        "",
        "| 符号 | 种类 | 端口 | 调用点 | 标注引用 |",
        "|---|---|---|---|---|",
    ]
    for s in ready[:80]:
        sites = s["site_count"] or "—"
        anns = len(s["ann_readers"]) or "—"
        lines.append(
            f"| `{s['symbol']}` | {_kind_cell(s)} | `{s['behind_port']}` | {sites} | {anns} |"
        )
    lines += [
        "",
        f"（本表列前 80 行，共 {len(ready)} 处。）",
        "",
        "## ❌ 二、还没有端口的特权核心",
        "",
        "直接 ``from x import get_y`` 就引用它的文件数（= 硬 import 的读者）。这些调用点",
        "插件接管不到：得先把访问器降级成端口、把读者改成经端口取，切换才成立。",
        "按读者数降序：读者越多，改成经端口取的代价越大，也越说明这一处不该指望插件接管。",
        "",
        "| 访问器 | 文件 | 硬 import 的读者 |",
        "|---|---|---|",
    ]
    for s in naked[:60]:
        lines.append(f"| `{s['symbol']}` | {_file_cell(s)} | {_readers_cell(s)} |")
    lines += [
        "",
        f"（本表列前 60 行，共 {len(naked)} 处；完整数据用 `--json` 取。）",
        "",
        "## ⚠️ 三、有端口、但代码绕过它的（只接了一半）",
        "",
        "端口注册了，也确实有人经端口取；可是同一个符号还有别的文件在硬 import。",
        "这种地方最容易被当成已经可切换：插件接管端口后，经端口的路径换了，硬 import",
        "的那几处还在用原实现——同一个能力跑出两套行为，比完全没有端口更难查。",
        "",
        "硬 import 本身不一定是错（例如按人设各自 new 一个实例、切换数据库时重置单例）；",
        "这张表只说明「接管端口换不掉这些调用点」，要不要让它们也走端口是设计判断。",
        "",
        "「引用」这里只算**取用**：调用它、读它的属性、把它递出去。只在类型标注里出现",
        "（``def _get_embedder(self) -> EmbeddingEngine``、``store: PetStore = None``）不算",
        "这一档——标注是文档，接管端口后返回的对象未必是那个类的实例，名字就此失真，但",
        "行为没有第二套。那类单列在文末附表，改不改是命名判断，不是接线缺口。",
        "",
        "标「import 期快照」的那几行是另一类绕过，也是最难看出来的一类：模块级写",
        "``db_manager = get_db_manager()``，实现被抓在 import 那一刻，之后接管端口换不",
        "掉这个变量——它长得和普通模块变量一模一样。修法是把调用点改回取访问器。",
        "",
        "| 符号 | 种类 | 端口 | 仍在硬 import 的文件 | 仅标注引用 |",
        "|---|---|---|---|---|",
    ]
    for s in bypass[:60]:
        lines.append(
            f"| `{s['symbol']}` | {_kind_cell(s)} | `{s['behind_port']}` | {_readers_cell(s)} | "
            f"{_ann_cell(s)} |"
        )
    lines += [
        "",
        f"（本表列前 60 行，共 {len(bypass)} 处。）",
        "",
        "## 附、名字写死在类型标注里的",
        "",
        "这些符号**行为上已经可切换**（上面的档里没它们），但文件里把内置实现的类名写死",
        "在标注上。插件接管后返回的对象通常不是那个类的实例，标注就成了假话：读代码的人",
        "会被它误导，静态检查也会报不匹配。要修的是名字（改成协议类型或放宽标注），不是接线。",
        "",
        "| 符号 | 端口 | 把名字写死在该文件标注里的 |",
        "|---|---|---|",
    ]
    for s in ann_only[:60]:
        port = f"`{s['behind_port']}`" if s["behind_port"] else "—"
        lines.append(f"| `{s['symbol']}` | {port} | {_ann_files_cell(s)} |")
    lines += [
        "",
        f"（本表列前 60 行，共 {len(ann_only)} 处。）",
        "",
        f"其余 {len(seams) - len(ready) - len(naked) - len(bypass)} 处不进表：没有硬 import 的读者，",
        "也没有「在用的端口」——要么是模块内部自己的封装，要么端口注册了却没人经端口取。",
        "口径限制见生成器 docstring：端口名要写成字面量；第三方、局部同名遮蔽等",
        "解析不出来源的引用不算读者。",
        "",
    ]
    return "\n".join(lines)


def _file_cell(seam: Dict[str, object]) -> str:
    return f"`{seam['file']}`" if seam["file"] else "—（扫描范围外）"


def _kind_cell(seam: Dict[str, object]) -> str:
    if seam["resolves_to"]:
        return "端口访问器"
    if seam["kind"] == "snapshot":
        of = seam["snapshot_of"]
        return f"import 期快照（取 `{of.split(SEP, 1)[-1]}`）" if of else "import 期快照"
    if seam["kind"] == "object":
        return "端口包装"
    return "访问器"


def _readers_cell(seam: Dict[str, object]) -> str:
    readers = seam["readers"]
    if not readers:
        return "—"
    cell = ", ".join(f"`{r}`" for r in readers[:5])
    if len(readers) > 5:
        cell += f" 等 {len(readers)} 处"
    return cell


def _ann_cell(seam: Dict[str, object]) -> str:
    """⚠️ 表里那一列只给个数：绕过与否看上一列，这里只是「名字还写死了几处」。"""
    return str(len(seam["ann_readers"])) if seam["ann_readers"] else "—"


def _ann_files_cell(seam: Dict[str, object]) -> str:
    readers = seam["ann_readers"]
    if not readers:
        return "—"
    cell = ", ".join(f"`{r}`" for r in readers[:5])
    if len(readers) > 5:
        cell += f" 等 {len(readers)} 处"
    return cell


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="seam_catalogue", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--check", action="store_true", help="与 docs/SEAMS.md 比对，漂了退出 1")
    parser.add_argument("--out", type=Path, default=None, help="写到文件而不是 stdout")
    args = parser.parse_args(argv)

    text = (
        json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.json
        else render_markdown(build())
    )
    if args.check:
        doc = _doc_path()
        if not doc.exists():
            print(f"{_rel(doc)} 不存在 —— 先用 --out 生成一次")
            return 1
        if doc.read_text(encoding="utf-8").strip() != text.strip():
            print(f"{_rel(doc)} 与代码漂了：python scripts/seam_catalogue.py --out docs/SEAMS.md")
            return 1
        print(f"{_rel(doc)} 与代码一致。")
        return 0
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"写入 {_rel(args.out)}", file=sys.stderr)
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
