"""scripts/seam_catalogue.py —— 缝隙目录的每条主张都要能被测试推翻。

为什么测得这么细：这份目录是「插件今天到底能接管什么」的唯一依据（本项目不做插件
安全，没有权限声明可查），也是下一轮「把特权核心降级成端口」的工单来源。它出错的
方向很固定，就是**漏算**：某处硬 import 没被认成读者，目录就会把只接了一半的端口说
成已经可切换，那一处于是永远排不进工单——而没人会去查一个显示为绿的表。

所以这里钉住的每一条都是「会少算」的判断：端口包装的不是访问器而是模块级对象、
引用不只有调用、点号链只算最长一段、定义者自己不算读者、局部同名遮蔽不算访问器。
外加真实仓库的两条：bootstrap 里的端口一个都不能漏/多，落盘的 docs/SEAMS.md 不许漂。
"""
import itertools
import re
import textwrap

import pytest

import scripts.seam_catalogue as sc

# 本文件的「真实树」用例会扫描整个仓库源码（几十个文件）并做 AST 分析：
# 本机一次 ~34s，带 coverage tracer 时最慢的一条 ~96s，而 pytest.ini 的全局
# timeout 只有 60s —— CI runner 比本机慢一倍以上，必然被全局 timeout 误杀
# （v0.2.4 的 coverage job 就是这么挂的）。这里整文件放宽到 300s。
pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """把一棵假源码树挂到生成器眼前（它只读文件、不 import，所以假树够用）。

    每次 ``tree()`` 调用占一个**独立子目录**：``tmp_path`` 是按测试共享的，一个测试
    要两棵树（例如 ``ann_tree`` + ``only_ann_tree``）时两批文件会写进同一个目录，
    读者和标注互相污染。
    """

    seq = itertools.count()

    def write(files: dict) -> "object":
        root = tmp_path / f"tree{next(seq)}"
        for rel, src in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
        monkeypatch.setattr(sc, "ROOT", root)
        return root

    return write


def _port(data, name):
    return next(p for p in data["ports"] if p["name"] == name)


def _seam(data, symbol):
    return next(s for s in data["seams"] if s["symbol"] == symbol)


def _has(data, symbol) -> bool:
    return any(s["symbol"] == symbol for s in data["seams"])


# ── 假树：端口包的是模块级对象，不是 get_x() ─────────────────────────────


@pytest.fixture
def mgr_tree(tree):
    tree(
        {
            # provider 是嵌套 def：只扫模块层会一条端口都找不到
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _mgr():
                        from modules.a.thing import mgr
                        return mgr

                    def _lonely():
                        from modules.b.lonely import get_lonely
                        return get_lonely()

                    def _steady():
                        from modules.b.ready import get_ready
                        return get_ready()

                    _safe_register("mgr", _mgr)
                    _safe_register("lonely", _lonely)
                    _safe_register("steady", _steady)
            """,
            "modules/a/__init__.py": """
                from modules.a.thing import mgr
                from modules.a.thing import Mgr as Manager
            """,
            "modules/a/thing.py": """
                class Mgr:
                    def run(self):
                        return 1

                mgr = Mgr()

                def internal():
                    return mgr.run()
            """,
            "modules/b/caller.py": """
                from modules.a.thing import mgr

                def use():
                    return mgr.run()
            """,
            # 没有调用，只是把对象当值递出去：同样是硬 import 的读者
            "modules/c/passing.py": """
                from modules.a.thing import mgr

                def give():
                    return {"m": mgr}
            """,
            # 经包名绕一层再取：``thing.mgr.run()`` 也是同一个单例的读者
            "modules/e/via_package.py": """
                from modules.a import thing

                def indirect():
                    return thing.mgr.run()
            """,
            # 重新绑定不是引用：这行是写，不是「硬 import 来用」
            "modules/d/rebind.py": """
                from modules.a.thing import mgr
                from modules.a.thing import Mgr

                def reset():
                    global mgr
                    mgr = Mgr()
            """,
            # 经包的转口取：modules.a 只是把名字转口过去，用的还是 thing 里那个对象
            "modules/f/via_pkg.py": """
                from modules.a import mgr

                def reexported():
                    return mgr.run()
            """,
            # 相对导入：本仓库一百多处，不展开就是一片读者凭空消失
            "modules/g/relative.py": """
                from ..a.thing import mgr

                def sibling():
                    return mgr.run()
            """,
            "modules/b/lonely.py": """
                _the_one = None

                def get_lonely():
                    global _the_one
                    if _the_one is None:
                        _the_one = object()
                    return _the_one
            """,
            # 有端口、有人经端口取、没人硬 import —— 唯一真正「今天就能接管」的一档
            "modules/b/ready.py": """
                _engine = None

                def get_ready():
                    global _engine
                    if _engine is None:
                        _engine = object()
                    return _engine
            """,
            "infra/tool_manager/service_registry.py": """
                _caps = {}

                def get_capability(name):
                    return _caps.get(name)
            """,
            "infra/tool_manager/tools/holder.py": """
                from infra.tool_manager.service_registry import get_capability

                def call():
                    svc = get_capability("mgr")
                    other = get_capability("steady")
                    return svc.run() or other
            """,
        }
    )
    return sc.build()


def test_ports_come_from_nested_provider_defs(mgr_tree):
    assert {p["name"] for p in mgr_tree["ports"]} == {"mgr", "lonely", "steady"}


def test_port_wrapping_a_module_object_still_gets_its_readers(mgr_tree):
    """端口包的不是 ``get_x()`` 而是模块级实例时，读者也必须算出来。

    这一条是整份目录的可信度所在：只认访问器的实现会把 ``mgr.run()`` 这类调用点
    整段漏掉，于是「只接了一半」显示成「已经可接管」。
    """
    seam = _seam(mgr_tree, "modules.a.thing:mgr")
    assert seam["kind"] == "object"
    assert seam["readers"] == [
        "modules/b/caller.py",
        "modules/c/passing.py",
        "modules/e/via_package.py",
        "modules/f/via_pkg.py",
        "modules/g/relative.py",
    ]
    assert seam["behind_port"] == "mgr"
    assert seam["bypassed"] is True


def test_indirections_are_not_readers(mgr_tree):
    """包 ``__init__`` 的转口行本身不算使用：它只是把名字再递一次。"""
    readers = _seam(mgr_tree, "modules.a.thing:mgr")["readers"]
    assert "modules/a/__init__.py" not in readers


def test_defining_file_is_not_a_reader(mgr_tree):
    """模块自己引用自己的单例是实现内部，不是绕过端口。"""
    assert "modules/a/thing.py" not in _seam(mgr_tree, "modules.a.thing:mgr")["readers"]


def test_rebinding_is_not_a_reader(mgr_tree):
    """赋值左侧的名字是写，不是「硬 import 来用」——按引用算会把重置点当成接管点。"""
    assert "modules/d/rebind.py" not in _seam(mgr_tree, "modules.a.thing:mgr")["readers"]


def test_non_call_reference_counts_as_reader(mgr_tree):
    """``{"m": mgr}`` 这种纯引用也是读者：漏了它就低估了切换的代价。"""
    assert "modules/c/passing.py" in _seam(mgr_tree, "modules.a.thing:mgr")["readers"]


def test_port_without_consumer_is_dead(mgr_tree):
    """注册了但没人 ``get_capability`` —— 接管它什么也不会发生，必须显式标出来。"""
    assert _port(mgr_tree, "lonely")["live"] is False
    assert _port(mgr_tree, "lonely")["consumers"] == []
    assert _port(mgr_tree, "mgr")["live"] is True
    assert _port(mgr_tree, "mgr")["consumers"] == ["infra/tool_manager/tools/holder.py"]


def test_dead_port_shows_in_markdown(mgr_tree):
    text = sc.render_markdown(mgr_tree)
    assert "死端口" in text
    assert "`lonely`" in text


def test_classification_separates_ready_from_half_wired(mgr_tree):
    """有端口不等于可接管：还有硬 import 读者的那条路必须落在「只接了一半」。"""
    ready = _seam(mgr_tree, "modules.b.ready:get_ready")
    assert ready["reader_count"] == 0
    assert _seam(mgr_tree, "modules.a.thing:mgr")["bypassed"] is True
    assert _seam(mgr_tree, "modules.b.lonely:get_lonely")["bypassed"] is False

    text = sc.render_markdown(mgr_tree)
    ready_section = text.split("## ✅ 一、已可接管")[1].split("## ❌ 二")[0]
    assert "modules.b.ready:get_ready" in ready_section
    assert "modules.a.thing:mgr" not in ready_section
    # 端口是死的：包着的东西再干净也不算「今天就能接管」
    assert "modules.b.lonely:get_lonely" not in ready_section


# ── 端口优先访问器：降级完成后的形状 ─────────────────────────────────────


@pytest.fixture
def pf_tree(tree):
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _engine():
                        from modules.a.eng import _default_engine
                        return lambda: _default_engine()

                    def _other():
                        from modules.a.eng import _default_engine
                        return _default_engine

                    _safe_register("engine", _engine)
                    _safe_register("other", _other)
            """,
            "modules/a/eng.py": """
                from infra.tool_manager.service_registry import get_capability

                class Engine:
                    def run(self):
                        return 1

                def _default_engine():
                    return Engine()

                def get_engine():
                    factory = get_capability("engine")
                    if factory is not None:
                        return factory()
                    return _default_engine()

                def _engine_or_none():
                    factory = get_capability("engine")
                    return factory() if factory else None
            """,
            # 调用点写在端口优先访问器上：它在这条端口的路上，不是绕过
            "modules/b/user.py": """
                from modules.a.eng import get_engine

                def use():
                    return get_engine().run()
            """,
            # 直接硬 import 内置实现：接管端口换不掉它，必须留在「只接了一半」
            "modules/c/direct.py": """
                from modules.a.eng import _default_engine

                def use():
                    return _default_engine()
            """,
            # 经两个端口取用：分不清属于哪条，不归类（但两个端口都算有消费者）
            "modules/d/multi.py": """
                from infra.tool_manager.service_registry import get_capability

                def two():
                    a = get_capability("engine")
                    b = get_capability("other")
                    return a or b
            """,
            # 名字被局部遮蔽后的调用不是端口取用
            "modules/e/shadow.py": """
                def fake():
                    get_capability = lambda name: None
                    return get_capability("engine")
            """,
            "infra/tool_manager/service_registry.py": """
                _caps = {}

                def get_capability(name):
                    return _caps.get(name)
            """,
        }
    )
    return sc.build()


def test_port_first_accessor_lists_sites_instead_of_readers(pf_tree):
    """降级好的缝：调用点按「端口路上的站点」计，不按「绕过端口的读者」计。

    这是 B6b 那批迁移的形状。若它仍被算成读者，目录会把每条降级好的缝都显示成
    「只接了一半」，于是没人再去补下一条。
    """
    seam = _seam(pf_tree, "modules.a.eng:get_engine")
    assert seam["kind"] == "port_first"
    assert seam["behind_port"] == "engine"
    assert seam["readers"] == []
    assert seam["sites"] == ["modules/b/user.py"]
    assert seam["bypassed"] is False


def test_port_first_accessor_shows_as_switchable_in_markdown(pf_tree):
    text = sc.render_markdown(pf_tree)
    ready_section = text.split("## ✅ 一、已可接管")[1].split("## ❌ 二")[0]
    assert "modules.a.eng:get_engine" in ready_section
    assert "端口访问器" in ready_section
    # 站点数写在同一行，人可以拿去数调用点
    assert "modules/b/user.py" not in ready_section


def test_port_first_accessor_with_no_external_site_is_not_a_seam_row(pf_tree):
    """没有外部调用点的包装只是端口自己的形状，列出来会把可接管的缝数虚高。"""
    text = sc.render_markdown(pf_tree)
    ready_section = text.split("## ✅ 一、已可接管")[1].split("## ❌ 二")[0]
    assert "_engine_or_none" not in ready_section


def test_hard_import_of_the_builtin_stays_half_wired(pf_tree):
    """端口优先访问器不许把「直接 import 内置实现」的那条也洗白。"""
    seam = _seam(pf_tree, "modules.a.eng:_default_engine")
    assert seam["readers"] == ["modules/c/direct.py"]
    assert seam["bypassed"] is True


def test_multi_port_function_is_not_attributed_but_still_a_consumer(pf_tree):
    """经多个端口取用的函数不归到任何一条，但它仍让两个端口显示「在用」。

    后半句要紧：不算访问器是保守，把端口说成死端口是**反方向的错**。
    """
    assert not _has(pf_tree, "modules.d.multi:two")
    assert _port(pf_tree, "engine")["live"] is True
    assert "modules/d/multi.py" in _port(pf_tree, "other")["consumers"]


def test_shadowed_capability_name_is_not_a_port_first_accessor(pf_tree):
    assert not _has(pf_tree, "modules.e.shadow:fake")
    assert "modules/e/shadow.py" not in _port(pf_tree, "engine")["consumers"]


def test_provider_that_is_its_own_port_first_accessor_raises(tree):
    """端口→访问器→端口：第一次调用就无限递归。文档看不出来，只能当场算出来。"""
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _loop():
                        from modules.a.loop import get_loop
                        return get_loop

                    _safe_register("loop", _loop)
            """,
            "modules/a/loop.py": """
                from infra.tool_manager.service_registry import get_capability

                def get_loop():
                    factory = get_capability("loop")
                    return factory() if factory else object()
            """,
            "infra/tool_manager/service_registry.py": """
                def get_capability(name):
                    return None
            """,
        }
    )
    with pytest.raises(RuntimeError, match="递归"):
        sc.build()


def test_import_time_snapshot_is_a_bypassing_reader(tree):
    """``x = get_y()`` 写在模块层：抓的是 import 那一刻的实现，接管端口换不掉它。

    这类接线最难发现（它就是个普通模块变量），目录不说「已经可切换」就是没说错；
    把它列成「端口没接上」的那一档，才有人去把调用点改回取访问器。
    """
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _ready():
                        from modules.b.ready import _default_ready
                        return _default_ready

                    _safe_register("ready", _ready)
            """,
            "modules/b/ready.py": """
                from infra.tool_manager.service_registry import get_capability

                _obj = None

                def get_ready():
                    factory = get_capability("ready")
                    if factory is None:
                        return _default_ready()
                    return factory()

                def _default_ready():
                    global _obj
                    if _obj is None:
                        _obj = object()
                    return _obj

                ready = get_ready()
            """,
            "modules/h/snap_user.py": """
                from modules.b.ready import ready

                def use():
                    return ready
            """,
            "infra/tool_manager/service_registry.py": """
                def get_capability(name):
                    return None
            """,
        }
    )
    data = sc.build()
    snap = _seam(data, "modules.b.ready:ready")
    assert snap["kind"] == "snapshot"
    assert snap["behind_port"] == "ready"
    assert snap["readers"] == ["modules/h/snap_user.py"]
    assert snap["bypassed"] is True

    text = sc.render_markdown(data)
    assert "import 期快照" in text
    ready_section = text.split("## ✅ 一、已可接管")[1].split("## ❌ 二")[0]
    assert "modules.b.ready:ready" not in ready_section


def test_function_local_capture_is_not_a_snapshot(tree):
    """函数内现场取的不算快照：每次调用都重读端口，接管得到。"""
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass
            """,
            "modules/b/ready.py": """
                def make():
                    obj = get_ready()
                    return obj

                def get_ready():
                    return object()
            """,
        }
    )
    assert not _has(sc.build(), "modules.b.ready:make")


# ── 点号链：只算最长那一段 ───────────────────────────────────────────────


@pytest.fixture
def chain_tree(tree):
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _graph():
                        from modules.a.g import Graph
                        return Graph.get_instance()

                    _safe_register("graph", _graph)
            """,
            "modules/a/g.py": """
                class Graph:
                    _instance = None

                    @classmethod
                    def get_instance(cls):
                        if cls._instance is None:
                            cls._instance = Graph()
                        return cls._instance
            """,
            "modules/b/via_accessor.py": """
                from modules.a.g import Graph

                def use():
                    return Graph.get_instance()
            """,
            "modules/c/via_class.py": """
                from modules.a.g import Graph

                def make():
                    return Graph()
            """,
        }
    )
    return sc.build()


def test_one_dotted_reference_is_attributed_once(chain_tree):
    """``Graph.get_instance()`` 是一个引用（访问器），不是两个（访问器 + 类）。

    逐段都记会把类的读者数虚增成「凡是用过访问器的都算」，类那一行就没意义了：它要
    说的恰恰是谁绕开访问器直接碰类。
    """
    assert _seam(chain_tree, "modules.a.g:Graph.get_instance")["readers"] == [
        "modules/b/via_accessor.py"
    ]
    assert _seam(chain_tree, "modules.a.g:Graph")["readers"] == ["modules/c/via_class.py"]


def test_class_accessor_is_detected_from_classmethod(chain_tree):
    seam = _seam(chain_tree, "modules.a.g:Graph.get_instance")
    assert seam["kind"] == "class_get_instance"
    assert seam["behind_port"] == "graph"


# ── 访问器判据：真把模块级名字交出去才算 ─────────────────────────────────


def test_local_shadow_is_not_an_accessor(tree):
    """局部同名遮蔽不算：返回现场造的对象，接管不了任何东西。"""
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass
            """,
            "modules/a/log.py": """
                logger = None

                def setup_logger(name):
                    logger = name
                    return logger

                def get_real():
                    global logger
                    logger = object()
                    return logger
            """,
        }
    )
    data = sc.build()
    assert not _has(data, "modules.a.log:setup_logger")
    assert _seam(data, "modules.a.log:get_real")["kind"] == "module_singleton"


# ── 类型标注：名字写死 ≠ 行为绕过 ────────────────────────────────────────


@pytest.fixture
def ann_tree(tree):
    """内置单例是「类 + 端口优先 classmethod」那一款（对应 EventStore 等 6 个）。

    标注规则只有在这种形状上才看得出来：端口包的是 ``Store._builtin_instance``，
    于是类名 ``Store`` 自己成一格，硬 import 它的文件会被算成这条端口的读者。
    """
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _store():
                        from modules.a.s import Store
                        return Store._builtin_instance

                    _safe_register("store", _store)
            """,
            "modules/a/s.py": """
                from infra.tool_manager.service_registry import get_capability

                class Store:
                    _instance = None

                    @classmethod
                    def get_instance(cls):
                        factory = get_capability("store")
                        if factory is not None:
                            return factory()
                        return cls._builtin_instance()

                    @classmethod
                    def _builtin_instance(cls):
                        if cls._instance is None:
                            cls._instance = cls()
                        return cls._instance
            """,
            # 只有类名写死在标注上：取用一律经端口，接管换得掉行为
            "modules/h/annotated.py": """
                from typing import Optional

                from modules.a.s import Store

                def holder(store: Store) -> Store:
                    return store

                maybe: Optional[Store] = None
            """,
            # 真的碰对象：直接改内置单例，接管端口换不掉这一处
            "modules/h/touching.py": """
                from modules.a.s import Store

                def reset():
                    Store._instance = None
            """,
            "infra/tool_manager/service_registry.py": """
                def get_capability(name):
                    return None
            """,
        }
    )
    return sc.build()


def test_annotation_only_reference_is_not_a_bypass(ann_tree):
    """标注里的类名不算「绕过端口」的读者。

    反过来的代价很具体：仓库里 6 个类的端口各带着 3~5 个「读者」，全都是
    ``def _get_store(self) -> Store`` 这种标注。表说「这些端口只接了一半」，照着找
    的人一处行为缺口都找不到，最后认定表不准、继续硬读源码——表的输出就废了。
    标注仍然是事实（名字写死在该文件），所以它有自己的列和附表，只是不进 ⚠️。
    """
    seam = _seam(ann_tree, "modules.a.s:Store")
    assert seam["readers"] == ["modules/h/touching.py"]
    assert seam["ann_readers"] == ["modules/h/annotated.py"]
    # touching.py 真在改内置单例，所以这一格仍然进 ⚠️——标注规则不是把 ⚠️ 抹平
    assert seam["bypassed"] is True


@pytest.fixture
def only_ann_tree(tree):
    """一棵「类名只出现在标注里」的树：没有任何取值读者，用来验附表与 bypassed 为假。"""
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _store():
                        from modules.a.s import Store
                        return Store

                    _safe_register("store", _store)
            """,
            "modules/a/s.py": """
                class Store:
                    pass
            """,
            "modules/h/only_annotation.py": """
                from modules.a.s import Store

                def holder() -> Store:
                    raise NotImplementedError
            """,
        }
    )
    return sc.build()


def test_value_use_of_the_class_still_bypasses(only_ann_tree):
    """边界另一侧：只有标注时不得进 ⚠️。

    把「标注」整类判成非读者的反面是把真缺口也判没了，所以取值那一侧由
    :func:`ann_tree` 里那个 ``touching.py`` 钉住，这里钉住纯标注那一侧。
    """
    seam = _seam(only_ann_tree, "modules.a.s:Store")
    assert seam["readers"] == []
    assert seam["ann_readers"] == ["modules/h/only_annotation.py"]
    assert seam["bypassed"] is False


def test_annotations_are_listed_in_their_own_section(ann_tree, only_ann_tree):
    # 纯标注的符号：不进 ⚠️ 表，进附表
    text = sc.render_markdown(only_ann_tree)
    annex = text.split("## 附、名字写死在类型标注里的")[1]
    assert "modules/h/only_annotation.py" in annex
    warn = text.split("## ⚠️ 三")[1].split("## 附")[0]
    assert "modules/h/only_annotation.py" not in warn

    # 混合的符号：⚠️ 那行的读者列只有 touching.py，标注只报个数（名字写在附表里，
    # 那一行不给文件名——工单主体是「绕过的读者」，标注是附带提示）
    both = sc.render_markdown(ann_tree)
    row = [ln for ln in both.splitlines() if "modules/h/touching.py" in ln][0]
    assert "annotated" not in row
    # 读者列紧跟着的就是标注计数列
    assert "`modules/h/touching.py` | 1 |" in row


# ── import 期快照的两种归属 ──────────────────────────────────────────────


def test_snapshot_of_locally_defined_accessor_is_detected(tree):
    """``ready = get_ready()`` 取的访问器就在同一个文件里，也要认出是快照。

    跨文件的边只认 import 绑定；不认「本文件自己定义的函数」这一跳，同文件内的快照
    就永远检测不出来，而它恰恰是最常见的一种（访问器和单例变量住一起）。漏了它，
    目录会把还冻在 import 那一刻的路说成已经可切换。
    """
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _ready():
                        from modules.b.ready import _default_ready
                        return _default_ready

                    _safe_register("ready", _ready)
            """,
            "modules/b/ready.py": """
                from infra.tool_manager.service_registry import get_capability

                _obj = None

                def get_ready():
                    factory = get_capability("ready")
                    if factory is None:
                        return _default_ready()
                    return factory()

                def _default_ready():
                    global _obj
                    if _obj is None:
                        _obj = object()
                    return _obj

                ready = get_ready()          # 同一个文件里的访问器，没有 import
            """,
            "modules/h/use.py": """
                from modules.b.ready import ready

                def use():
                    return ready
            """,
            "infra/tool_manager/service_registry.py": """
                def get_capability(name):
                    return None
            """,
        }
    )
    snap = _seam(sc.build(), "modules.b.ready:ready")
    assert snap["kind"] == "snapshot"
    assert snap["snapshot_of"] == "modules.b.ready:get_ready"
    assert snap["behind_port"] == "ready"
    assert snap["readers"] == ["modules/h/use.py"]


def test_portless_snapshot_roll_up_readers_to_accessor(tree):
    """没端口的快照不该占一行：要做的动作在访问器那里（建端口 + 调用点改回取访问器）。

    挂在快照名字上只会多一行读不懂的变量名，而访问器那一行反而看不到这个读者。
    """
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass
            """,
            "modules/b/late.py": """
                _obj = None

                def get_late():
                    global _obj
                    if _obj is None:
                        _obj = object()
                    return _obj

                late = get_late()
            """,
            "modules/h/use.py": """
                from modules.b.late import late

                def use():
                    return late
            """,
        }
    )
    data = sc.build()
    assert not _has(data, "modules.b.late:late")
    assert _seam(data, "modules.b.late:get_late")["readers"] == ["modules/h/use.py"]


# ── provider 递归：直接的和转口的 ────────────────────────────────────────


def test_transitive_provider_recursion_raises(tree):
    """provider 隔一层转口撞上端口优先访问器，同样是调用即递归。

    只看 provider 那一层会放过它：``_loop()`` 返回的不是访问器而是 ``_helper``，
    但 ``_helper`` 里面调的就是查这个端口的 ``get_loop()``。仓库里真出现过
    （file_history 的 provider 经 ``FileHistory.get_instance`` 撞回自己那条端口）。
    """
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _loop():
                        from modules.a.loop import _helper
                        return _helper

                    _safe_register("loop", _loop)
            """,
            "modules/a/loop.py": """
                from infra.tool_manager.service_registry import get_capability

                def get_loop():
                    factory = get_capability("loop")
                    if factory is None:
                        return object()
                    return factory()

                def _helper():
                    return get_loop()
            """,
            "infra/tool_manager/service_registry.py": """
                def get_capability(name):
                    return None
            """,
        }
    )
    with pytest.raises(RuntimeError, match="转口调用"):
        sc.build()


def test_builtin_body_must_not_read_the_port(tree):
    """``_builtin_instance`` 里只要还查端口，provider 就把它包成了循环。

    迁移脚本手滑把内置实现也写成端口优先（真滑过：批量脚本被执行两遍，把
    ``_builtin_instance`` 又包了一层），目录必须当场拒绝，不能让这种树写出文档。
    """
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _store():
                        from modules.a.s import Store
                        return Store._builtin_instance

                    _safe_register("store", _store)
            """,
            "modules/a/s.py": """
                from infra.tool_manager.service_registry import get_capability

                class Store:
                    @classmethod
                    def get_instance(cls):
                        factory = get_capability("store")
                        if factory is not None:
                            return factory()
                        return cls._builtin_instance()

                    @classmethod
                    def _builtin_instance(cls):
                        factory = get_capability("store")
                        return factory() if factory else cls()
            """,
            "infra/tool_manager/service_registry.py": """
                def get_capability(name):
                    return None
            """,
        }
    )
    with pytest.raises(RuntimeError, match="递归"):
        sc.build()


def test_classmethod_port_first_lists_sites_not_readers(tree):
    """端口优先也可以是类方法：``Cls.get_instance()`` 自己先查端口。

    这一条撑起的是「82 处调用点一行不改就能被接管」的形状（不然得给每个类在外面
    另起访问器、再把每个调用点搬一遍）。它必须和模块函数那条同权：调用点算 sites
    不算 readers，否则刚补完的缝在表里还是「只接了一半」。
    """
    tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _store():
                        from modules.a.s import Store
                        return Store._builtin_instance

                    _safe_register("store", _store)
            """,
            "modules/a/s.py": """
                from infra.tool_manager.service_registry import get_capability

                class Store:
                    _instance = None

                    @classmethod
                    def get_instance(cls):
                        factory = get_capability("store")
                        if factory is not None:
                            return factory()
                        return cls._builtin_instance()

                    @classmethod
                    def _builtin_instance(cls):
                        if cls._instance is None:
                            cls._instance = cls()
                        return cls._instance
            """,
            "modules/h/caller.py": """
                from modules.a.s import Store

                def use():
                    return Store.get_instance()
            """,
            "infra/tool_manager/service_registry.py": """
                def get_capability(name):
                    return None
            """,
        }
    )
    data = sc.build()
    seam = _seam(data, "modules.a.s:Store.get_instance")
    assert seam["resolves_to"] == "store"
    assert seam["sites"] == ["modules/h/caller.py"]
    assert seam["readers"] == []
    assert seam["bypassed"] is False
    assert _seam(data, "modules.a.s:Store")["readers"] == []


def test_parse_failure_raises_instead_of_skipping(tree):
    """漏算比报错更糟：解析不了的文件必须炸，不能静默跳过。"""
    tree(
        {
            "bootstrap.py": "",
            "modules/a/broken.py": "def f(:\n    pass\n",
        }
    )
    with pytest.raises(RuntimeError):
        sc.build()


# ── 第二套端口注册表：infra/ports.py 的点号端口 ──────────────────────────
#
# 本仓有两套端口：bootstrap 用 register_capability 填的蛇形名业务端口，和提供方模块在
# 自己 import 期用 infra/ports.py::register 登记的点号名横切端口（安全门、感知系统、
# 身份表）。目录以前只看见前者，于是「58 条端口全部可接管」这句话实际只说了一半，而且
# 是以「表里没有」的形式说的——看不见的洞比看得见的洞危险。


_PORTS_STUB = '''
    """假的端口表模块。生成器只按路径和写法认它，从不 import。"""
    _PROVIDERS = {}

    def register(name, provider):
        _PROVIDERS[name] = provider

    def resolve(name, default=None):
        return _PROVIDERS.get(name, default)

    def as_factory(fn):
        return fn
'''


def test_facade_port_with_constant_name_is_catalogued(tree):
    """``ports.register(PORT_X, provider)``：常量端口名 + 属性写法都要认。

    这两条各漏一半都会让整根门面从表里消失：``infra/security_gate.py`` 的 9 个端口名
    全是本文件常量，而且登记写成 ``ports.register(...)`` —— 属性写法要按**模块**判，
    拿「解出的符号」比永远不相等（纯模块引用在 _resolve 里是故意返回 None 的）。
    """
    tree(
        {
            "bootstrap.py": "",
            "infra/ports.py": _PORTS_STUB,
            "infra/facade.py": """
                from infra import ports

                PORT_X = "x.y"

                def register_impl(provider):
                    ports.register(PORT_X, provider)

                def get_impl():
                    impl = ports.resolve(PORT_X)
                    if impl is not None:
                        return impl
                    return "builtin"
            """,
            "modules/consumer.py": """
                from infra.facade import get_impl

                def use():
                    return get_impl()
            """,
        }
    )
    data = sc.build()
    port = _port(data, "x.y")
    assert port["system"] == sc.PORTS_SYSTEM
    # provider 是函数参数：当场解不出包了什么，但端口仍然算活着、仍然列出来。
    # 猜一个 wrapped 列表会让目录说「接管这条换掉那些」，而那些跟它没关系。
    assert port["provider"] == "provider"
    assert port["wrapped"] == []
    assert port["live"] is True
    seam = _seam(data, "infra.facade:get_impl")
    assert seam["kind"] == "port_first"
    assert seam["behind_port"] == "x.y"
    assert seam["readers"] == []
    assert seam["sites"] == ["modules/consumer.py"]


def test_facade_port_via_bare_imported_names(tree):
    """``from infra.ports import register as _reg, resolve as _res`` 也算登记与取用。

    别名和裸名字是本仓真实写法（modules/perception、modules/thinking 都这么写）。只认
    ``ports.xxx`` 的话这批端口照样消失。
    """
    tree(
        {
            "bootstrap.py": "",
            "infra/ports.py": _PORTS_STUB,
            "infra/facade2.py": """
                from infra.ports import register as _reg, resolve as _res

                def get_impl2():
                    v = _res("z.w")
                    return v if v is not None else "builtin"

                def put(provider):
                    _reg("z.w", provider)
            """,
        }
    )
    data = sc.build()
    port = _port(data, "z.w")
    assert port["system"] == sc.PORTS_SYSTEM
    assert _seam(data, "infra.facade2:get_impl2")["behind_port"] == "z.w"


def test_same_named_register_is_not_a_port(tree):
    """同名方法不算端口：``ToolRegistry.register("security.fake", ...)`` 与端口无关。

    判据是「被调方解到 infra.ports」而不是「名字里带 register」——按名字判会把工具注册
    当成端口登记，凭空多出几十条假端口，而假端口比漏端口更糟：它会让人去接管一条
    根本不存在的路。
    """
    tree(
        {
            "bootstrap.py": "",
            "infra/ports.py": _PORTS_STUB,
            "infra/tool_registry2.py": """
                class ToolRegistry2:
                    def register(self, name, func):
                        pass
            """,
            "modules/uses_tools.py": """
                from infra.tool_registry2 import ToolRegistry2

                def bind(reg, name, func):
                    reg.register(name, func)

                def bind_hard(func):
                    ToolRegistry2().register("security.fake", func)
            """,
        }
    )
    names = {p["name"] for p in sc.build()["ports"]}
    assert "security.fake" not in names


def test_facade_port_resolved_but_never_registered_is_listed(tree):
    """只检测到取用、没检测到登记点的端口也要列出来，并说明是「没检测到」。

    提供方那边可能是动态传的（``register_provider(name, provider)``，name 是参数）。
    这种情况静态层面确实看不见，但目录不能把它说成「没有这条端口」。
    """
    tree(
        {
            "bootstrap.py": "",
            "infra/ports.py": _PORTS_STUB,
            "infra/facade3.py": """
                from infra import ports

                def get_impl3():
                    return ports.resolve("only.used")
            """,
        }
    )
    port = _port(sc.build(), "only.used")
    assert "没检测到登记点" in port["provider"]
    assert port["live"] is True


def test_facade_port_provider_reaching_its_own_accessor_raises(tree):
    """门面端口同样不许 provider 指回自己的端口优先访问器：一调用就无限递归。"""
    tree(
        {
            "bootstrap.py": "",
            "infra/ports.py": _PORTS_STUB,
            "infra/facade4.py": """
                from infra import ports

                def get_impl4():
                    impl = ports.resolve("self.loop")
                    return impl if impl is not None else "builtin"

                def install():
                    ports.register("self.loop", get_impl4)
            """,
        }
    )
    with pytest.raises(RuntimeError, match="self.loop"):
        sc.build()


# ── 防漂：--check ────────────────────────────────────────────────────────


def test_check_passes_on_fresh_doc_and_fails_after_code_changes(tree, capsys):
    root = tree(
        {
            "bootstrap.py": """
                def _safe_register(name, factory):
                    pass

                def register_business_capabilities():
                    def _mgr():
                        from modules.a.thing import mgr
                        return mgr

                    _safe_register("mgr", _mgr)
            """,
            "modules/a/thing.py": """
                class Mgr:
                    def run(self):
                        return 1

                mgr = Mgr()
            """,
            "modules/b/caller.py": """
                from modules.a.thing import mgr

                def use():
                    return mgr.run()
            """,
        }
    )
    doc = root / "docs" / "SEAMS.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    assert sc.main(["--out", str(doc)]) == 0
    assert sc.main(["--check"]) == 0

    # 代码里多一处硬 import 读者：文档不会自己改，必须报漂
    (root / "modules" / "b" / "second.py").write_text(
        "from modules.a.thing import mgr\n\n\ndef use2():\n    return mgr.run()\n",
        encoding="utf-8",
    )
    assert sc.main(["--check"]) == 1
    assert "漂了" in capsys.readouterr().out


# ── 真实仓库：目录不许比代码少说 ─────────────────────────────────────────


def test_real_tree_ports_match_bootstrap_literals():
    """bootstrap 里每一条 ``_safe_register("x", ...)`` 都要在目录里，一条不多一条不少。

    端口名用正则独立取（不是复用生成器的解析），否则生成器漏一个端口，测试跟着一起漏，
    两边一起错就永远不会被发现。

    只比对「业务」那一套：本仓有两套端口注册表（``service_registry`` 的蛇形名能力端口、
    ``infra/ports.py`` 的点号名横切端口），点号端口对账在
    :func:`test_real_tree_facade_ports_all_listed`。把它们混成一个集合比，等于宣布
    「业务端口的登记点就是所有端口的登记点」——那句话本来就不成立。
    """
    text = sc._bootstrap_path().read_text(encoding="utf-8")
    declared = set(re.findall(r'_safe_register\(\s*["\']([^"\']+)["\']', text))
    data = sc.build()
    listed = {p["name"] for p in data["ports"] if p["system"] == sc.BUSINESS_SYSTEM}
    assert listed == declared, f"缺 {declared - listed}；多 {listed - declared}"
    assert declared, "bootstrap 里一个端口都没找到 —— 扫描路径或写法变了"


def test_real_tree_facade_ports_all_listed():
    """点号端口（横切契约）一条都不能从表里消失。独立取法：正则，不复用生成器的 ast。

    取哪些文件：import 过 ``infra.ports`` 的。取哪些名字：``register(...)`` /
    ``resolve(...)`` 的第一个实参——字面量直接用，全大写名字回到**同一文件**里
    ``NAME = "x.y"`` 那一行查值。两条都得认，因为 ``infra/security_gate.py`` 的 9 个端口名
    全是本文件常量：生成器要是漏了「常量当端口名」这一路，或者漏了「属性写法要按模块判」
    那一路，这张表会安静地说「这些端口不存在」，而漏算的方向永远是让人以为缝更少。
    """
    declared: set = set()
    for path in sc._sources():
        text = path.read_text(encoding="utf-8")
        if "infra.ports" not in text and "from infra import ports" not in text:
            continue
        consts = dict(
            re.findall(
                r'^([A-Z_][A-Z_0-9]*)\s*=\s*["\']([^"\']+)["\']', text, re.M
            )
        )
        for raw in re.findall(
            r'(?:\bports|_ports|_register_port|(?<![.\w])register|(?<![.\w])resolve)'
            r'\s*\(\s*(["\'][^"\']+["\']|[A-Z_][A-Z_0-9]*)',
            text,
        ):
            name = raw.strip("\"'")
            if not raw.startswith(("'", '"')):
                name = consts.get(raw, "")
            if "." in name:
                declared.add(name)
    listed = {p["name"] for p in sc.build()["ports"] if p["system"] == sc.PORTS_SYSTEM}
    assert declared, "一个点号端口都没取到 —— 写法或扫描路径变了"
    missing = declared - listed
    assert not missing, f"目录漏掉这些横切端口：{missing}"


def test_real_tree_has_no_dead_ports():
    """每个端口都要有人经 ``get_capability`` 取用。

    没人取的端口 = 「看起来能接管，其实接管了也没用」，是这份表最坏的误导。
    """
    dead = [p["name"] for p in sc.build()["ports"] if not p["live"]]
    assert dead == [], f"死端口：{dead}"


def test_real_tree_seam_paths_exist_and_exclude_defining_file():
    data = sc.build()
    for seam in data["seams"]:
        if seam["file"]:
            assert (sc.ROOT / seam["file"]).exists(), seam
        for reader in seam["readers"]:
            assert (sc.ROOT / reader).exists(), seam
            assert reader != seam["file"], seam


def test_real_tree_seams_are_ranked_by_blast_radius():
    """表按「硬 import 读者数降序」排，读者多的先看到。

    排序键写死成 (−读者数, 符号)，不然同一份代码两次生成的行序会随集合走，
    下面的逐字节一致判定就没有意义了。
    """
    seams = sc.build()["seams"]
    keys = [(-s["reader_count"], s["symbol"]) for s in seams]
    assert keys == sorted(keys)


def test_real_tree_port_first_accessors_have_registered_ports():
    """每一条「端口优先访问器」经的端口都必须在 bootstrap 装配过。

    访问器只在代码里查端口、装配层从不注册，等于把内置实现藏在回退分支里：端口表
    查不到它，管理界面看不见它，接管与否只能靠读源码。既然目标是每条缝都既装配又
    可切换，这里就不许出现「有访问器、没装配」。
    """
    data = sc.build()
    declared = {p["name"] for p in data["ports"]}
    missing = sorted(
        {
            s["behind_port"]
            for s in data["seams"]
            if s["kind"] == "port_first" and s["behind_port"] not in declared
        }
    )
    assert missing == [], f"端口优先访问器经了没装配的端口：{missing}"


# 已经降级完的缝：访问器 -> (端口名, 它包住的内置入口)。每补一条就往这里加一行，
# 于是「补全切换」这件有过记录的事不会靠记忆维持——漂回去一次就红一次。
#
# 端口名那一半不是重复信息：只查「是端口优先」的话，把调用点挪去查另一条端口也算过，
# 而接管这条路的人拿不到实现。内置入口同样不是装饰：只查访问器的话，「补了个端口、
# 调用点没搬」也算过，那种端口接管了照样换不到行为。留 None 的是内置入口自己不成一条
# 缝（纯模块内部的名字，目录里没有它的记录）。
MIGRATED = {
    "config.prompts.loader:get_loader":
        ("prompt_loader", "config.prompts.loader:_default_loader"),
    "infra.data_process.core.cdp_scanner:get_cdp_scanner":
        ("cdp_scanner", "infra.data_process.core.cdp_scanner:_default_cdp_scanner"),
    "infra.data_process.core.video_analyzer:get_video_analyzer":
        ("video_analyzer", "infra.data_process.core.video_analyzer:_default_video_analyzer"),
    "infra.error_bus:get_error_bus":
        ("error_bus", "infra.error_bus:_default_error_bus"),
    "infra.mcp.factory:get_mcp_tool_service":
        ("mcp_tool_service", "infra.mcp.factory:_default_mcp_tool_service"),
    "infra.mcp.factory:get_server_manager":
        ("mcp_server_manager", "infra.mcp.factory:_default_server_manager"),
    # ↓ 这 8 条经的是门面（infra/ports.py 点号名）端口，不是 bootstrap 的业务端口。
    # 它们一直在源码里端口优先，之前只是目录看不见——登记写在本文件的常量上、又用
    # ``ports.register(...)`` 的属性形式调用，两条漏检叠起来就让整根安全门面不存在。
    "infra.security_gate:get_control_tools":
        ("thinking.control_tools", None),
    "infra.security_gate:get_identities":
        ("security.identities", None),
    "infra.security_gate:get_model_factory":
        ("thinking.model_factory", None),
    "infra.security_gate:get_model_permissions_cls":
        ("security.identities", None),
    "infra.security_gate:get_security_api":
        ("security.api", None),
    "infra.security_gate:get_tool_permission_controller":
        ("security.permission_controller", None),
    "infra.security_gate:get_tool_security_gate":
        ("security.tool_gate", None),
    "infra.security_gate:get_tool_security_gate_cls":
        ("security.tool_gate", None),
    "modules.cortex.file_history:get_file_history":
        ("file_history", "modules.cortex.file_history:_default_file_history"),
    "modules.database.connection:get_db_manager":
        ("db_manager", "modules.database.connection:_default_db_manager"),
    "modules.database.disk_cache:get_disk_cache":
        ("disk_cache", "modules.database.disk_cache:_default_disk_cache"),
    "modules.database.session_events:get_session_event_repo":
        ("session_event_repo", "modules.database.session_events:_default_session_event_repo"),
    "modules.database.session_projections:get_projection_repo":
        ("projection_repo", "modules.database.session_projections:_default_projection_repo"),
    "modules.database.session_repo:get_session_repo":
        ("session_repo", "modules.database.session_repo:_default_session_repo"),
    "modules.desktop_pet.pet_engine:get_running_pet_engine":
        ("pet_engine", "modules.desktop_pet.pet_engine:PetEngine._builtin_instance"),
    "modules.knowledge.service:get_knowledge_service":
        ("knowledge_base", "modules.knowledge.service:_default_knowledge_service"),
    "modules.memory.embedding:get_embedding_engine":
        ("embedding_engine", "modules.memory.embedding:EmbeddingEngine.get_instance"),
    "modules.memory.event_reducer:get_reducer":
        ("event_reducer", "modules.memory.event_reducer:_default_reducer"),
    "modules.memory.event_retrieval:get_event_retrieval":
        ("event_retrieval", "modules.memory.event_retrieval:_default_event_retrieval"),
    "modules.output_system.tts:get_tts_output_dir":
        ("tts_output_dir", "modules.output_system.tts:_default_tts_output_dir"),
    "modules.perception.difference.detector:get_detector":
        ("difference_detector", "modules.perception.difference.detector:_default_detector"),
    "modules.perception.difference.heartbeat:get_heartbeat":
        ("heartbeat", "modules.perception.difference.heartbeat:_default_heartbeat"),
    "modules.perception.difference.sources.mcp_screen_source:get_screen_diff_source":
        ("screen_diff_source",
         "modules.perception.difference.sources.mcp_screen_source:_default_screen_diff_source"),
    "modules.perception.events.bus:get_event_bus":
        ("event_bus", "modules.perception.events.bus:_default_event_bus"),
    "modules.perception.integration:get_perception_integrator":
        ("perception_integrator",
         "modules.perception.integration:_default_perception_integrator"),
    "modules.perception.setup:get_perception_system":
        ("perception_system", "modules.perception.setup:_default_perception_system"),
    # 包根那个同名转发访问器：它以前只是 ``from .setup import ...``，接管 perception.system
    # 换不到它，而 integration.py 恰恰 import 的是它。两条注册表之间的缝就是这么漏的。
    "modules.perception:get_perception_system":
        ("perception.system", "modules.perception:_builtin_perception_system"),
    "modules.security_system.api:get_security_api":
        ("security_api", "modules.security_system.api:_default_security_api"),
    "modules.security_system.interface:get_security_port":
        ("security_port", "modules.security_system.interface:_default_security_port"),
    "modules.security_system.tool_permission_controller:get_tool_permission_controller":
        ("tool_permission_controller",
         "modules.security_system.tool_permission_controller:_default_tool_permission_controller"),
    "modules.shared.plugin_authoring:get_default_store":
        ("authored_plugin_store", "modules.shared.plugin_authoring:_default_plugin_store"),
    "modules.thinking.api_stream:get_thinking_system":
        ("thinking_system", "modules.thinking.api_stream:_default_thinking_system"),
    "modules.thinking.communication.message_bus:get_message_bus":
        ("message_bus", "modules.thinking.communication.message_bus:_default_message_bus"),
    "modules.thinking.conscience:get_conscience":
        ("conscience", "modules.thinking.conscience:_default_conscience"),
    "modules.thinking.context.compression:get_compression_engine":
        ("compression_engine",
         "modules.thinking.context.compression:_default_compression_engine"),
    "modules.thinking.context.controller:get_context_controller":
        ("context_controller",
         "modules.thinking.context.controller:_default_context_controller"),
    "modules.thinking.context.dialog_memory:history_read_limit":
        ("context_history_read_limit",
         "modules.thinking.context.dialog_memory:_default_history_read_limit"),
    "modules.thinking.identity:get_identities":
        ("identities", "modules.thinking.identity:_default_identities"),
    "modules.thinking.identity:load_external_identities":
        ("external_identities",
         "modules.thinking.identity:_builtin_load_external_identities"),
    "modules.thinking.model_factory:get_model_factory":
        ("model_factory", "modules.thinking.model_factory:_default_model_factory"),
    "modules.thinking.probes.probe_permission:get_probe_permission_manager":
        ("probe_permission_manager",
         "modules.thinking.probes.probe_permission:_default_probe_permission_manager"),
    "modules.thinking.scheduled_tasks:get_task_manager":
        ("task_manager", "modules.thinking.scheduled_tasks:_default_task_manager"),
    "modules.thinking.session_graph:get_session_graph_store":
        ("session_graph_store",
         "modules.thinking.session_graph:_default_session_graph_store"),
    "modules.thinking.skills:get_skill_manager":
        ("skill_manager", "modules.thinking.skills:_default_skill_manager"),
    "utils.power:get_prevent_sleep":
        ("prevent_sleep", "utils.power:_default_prevent_sleep"),
}


def test_real_tree_migrated_seams_stay_migrated():
    """降级好的缝不许回到「绕过端口」那一档。

    三半边都要查：访问器自己必须是端口优先、查的还是当初那条端口、并且真有外部调用点
    （没有调用点就是死代码，迁移没落地）；内置入口必须再无外部硬 import 读者。
    """
    data = sc.build()
    for accessor, (port, builtin) in MIGRATED.items():
        seam = _seam(data, accessor)
        assert seam["kind"] == "port_first", accessor
        assert seam["behind_port"] == port, \
            f"{accessor} 现在查的是 {seam['behind_port']}，不是 {port}"
        assert seam["site_count"] > 0, f"{accessor} 没有调用点：迁移没落地"
        assert seam["reader_count"] == 0, \
            f"{accessor} 仍有硬 import 读者：{seam['readers']}"
        if builtin is not None:
            readers = _seam(data, builtin)["readers"]
            assert readers == [], f"{builtin} 仍被这些文件硬 import：{readers}"


# 判定过、**故意**留下的硬 import 读者：符号 -> 哪些文件、为什么。
JUDGED_BYPASS = {
    "modules.memory.event_store:EventStore": {
        "files": {"config/settings.py", "modules/desktop_pet/memory_bank.py"},
        "why": "切记忆库要把内置那份单例清掉、桌宠按人设各自 new 实例——这两处要的"
               "就是「这个类本身」，改成经端口取反而会把重置目标换成插件的对象",
    },
    "modules.memory.causal_graph:CausalGraph": {
        "files": {"config/settings.py", "modules/desktop_pet/memory_bank.py"},
        "why": "同上：因果图跟记忆库走，切库时按类名清单例",
    },
    "modules.memory.event_reducer:get_memory_model_client": {
        "files": {
            "modules/desktop_pet/memory_bank.py",
            "modules/thinking/api_stream.py",
            "modules/thinking/chat_light/continuous_thinker.py",
        },
        "why": "记忆提炼使用独立的 OpenRouter 客户端，与主聊天模型解耦；"
               "这三处都要拿「记忆专用」单例，不能走主模型端口",
    },
    "modules.thinking.core.workflow_checkpoint:get_checkpoint_manager": {
        "files": {"modules/thinking/core/model_runner.py"},
        "why": "思考循环挂起/唤醒时直接读写检查点单例，属于内核内部状态，"
               "目前不对外提供可接管端口",
    },
}


@pytest.mark.timeout(300)
def test_real_tree_bypasses_are_all_judged():
    """目录里不许出现没记录在案的「接管不到的读者」。

    「❌ 清零」是算出来的，不是口头约定：任何新增的硬 import 读者——不管是新文件读旧缝，
    还是新缝根本没端口——都必须在这里写明理由，否则就是漏切换。
    """
    live = {
        s["symbol"]: set(s["readers"])
        for s in sc.build()["seams"]
        if s["readers"]
    }
    assert set(live) == set(JUDGED_BYPASS), (
        f"新增/消失的绕过端口读者：{set(live) ^ set(JUDGED_BYPASS)}"
    )
    for symbol, readers in live.items():
        assert readers == JUDGED_BYPASS[symbol]["files"], (symbol, readers)
        assert JUDGED_BYPASS[symbol]["why"], symbol


@pytest.mark.timeout(300)
def test_real_tree_output_is_deterministic():
    """同一份代码两次生成必须逐字节一样，否则 --check 的绿没有意义。

    目录里有集合，集合的迭代顺序在 Python 里不保证；渲染必须先排序。
    """
    first = sc.render_markdown(sc.build())
    second = sc.render_markdown(sc.build())
    assert first == second
    assert sc.main(["--check"]) == 0, "docs/SEAMS.md 漂了：重新生成它"
