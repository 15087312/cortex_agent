"""前端请求路径 × 后端路由 契约测试

背景（§109）：桌宠设置页读桌宠总开关时调用了不存在的 `GET /config/{key}`，
后端 404 → 前端 `catch` 静默 → 开关永远显示默认值。所有前端单测都是
`routeFetch` 按 URL mock 的，未命中的 URL 直接返回 `{}` + 200，
所以「后端根本没有这个路由」这类错误在 647 个前端测试里全绿。

本测试不信任何 mock：直接扫前端源码里的请求字面量，和后端
`api.main.app.routes` 的真实路由表比对（方法 + 路径都要匹配）。

约定（新增请求时无需改本文件）：
- 组件/api 层统一用 `request('GET', '/stream/...')`
- 文件上传等无法走 request 的地方用 `fetch(BASE + '/data-process/...')`
- 桌宠透明窗（frontend/pet/index.html）直接 `fetch('/stream/pet/...')`
- 也可以直接写 `/api/...`（vite 代理 `rewrite: ^/api → ''`，见 frontend/vite.config.js），
  这里统一剥掉 `/api` 再比。

路径里拼接的变量（`encodeURIComponent(id)` 等）在抽取时折叠成 `*`，
因此本测试校验的是「路由骨架存在」，不校验变量取值。

**抽取本身必须被验证**（§110 教训 6）：早期版本有两个静默漏洞——
① `fetch(` 的表达式起点少算了一个引号，所有 `fetch(...)` 调用都被丢弃；
② 不认 `/api` 前缀，页面里写 `/api/...` 的调用全部看不见。
两者都表现为「检查通过」，等价于没检查。因此：
- 每条 unparsed 调用都必须显式解释（仅允许 `request('GET', path)` 这种泛化封装），
  否则 `test_no_silently_dropped_calls` 失败；
- 各条解析规则都有自测，不再依赖「本机恰好能抽出来」。
"""
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = PROJECT_ROOT / "frontend"
# 第三方打包产物（pixi.min.js 等）不参与扫描
_VENDOR_DIRS = {FRONTEND / "pet" / "lib"}

# 本文件要 import api.main（挂载全部路由，连带加载业务模块），比普通单测慢；
# 按 §27.6 线索③的做法放宽 timeout，避免在慢机器/CI 上被全局 60s 误杀。
pytestmark = pytest.mark.timeout(120)

_SKIP_PARTS = {"node_modules", "dist", "coverage", "test-results", "__pycache__"}
_SKIP_SUFFIX = (".spec.js", ".test.js")

# 只关心后端 API：静态资源/页面相对路径（models/mao/...）不算
_PATH_PREFIXES = (
    "/api", "/stream", "/config", "/management", "/tools", "/security", "/system",
    "/health", "/data-process", "/audio", "/output", "/difference",
)

# vite dev proxy 把 /api 前缀剥掉再转发（frontend/vite.config.js）
_PROXY_PREFIX = "/api"

_REQUEST_CALL = re.compile(r"request\(\s*'(GET|POST|PUT|DELETE)'\s*,\s*")
_FETCH_CALL = re.compile(r"fetch\(\s*(?:BASE\s*\+\s*)?'([^']+)'")
# 任何 fetch( 都要有结论：要么被上面的字面量规则抽取，要么显式解释（见下方两个表）
_FETCH_ANY = re.compile(r"fetch\(\s*")
# 泛化封装：路径由调用方参数传入（request() 的调用点由 _REQUEST_CALL 覆盖）
_GENERIC_FETCH = {"frontend/src/api.js": "BASE + path"}
# 非后端 API（前端本地发现的端口等）——写清楚原因，新增条目必须说明为什么不是 API
_NON_API_FETCH = {
    "frontend/src/ws/client.js": "window.location.origin + '/backend-port'",
}

# fetch 的默认方法；字面量后面出现 method: 'POST' 时按 POST 记
_METHOD_IN_OPTS = re.compile(r"method\s*:\s*'(GET|POST|PUT|DELETE)'")

# 泛化封装：路径由参数传入，静态不可知（只允许这一种形态）
_BARE_IDENT = re.compile(r"[A-Za-z_$][\w$]*")

# 查询串变量：`const q = new URLSearchParams(...)` / `const qs = '?' + ...`
# 这类变量拼在路径尾部是 query，不该被当成路径参数
_QUERY_VAR_DECL = re.compile(r"\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")
_DEFAULT_QUERY_VARS = frozenset({"q", "qs", "query", "params", "queryParams", "searchParams"})


def _frontend_files():
    for p in FRONTEND.rglob("*"):
        if p.suffix not in (".js", ".vue", ".html"):
            continue
        if _SKIP_PARTS & set(p.parts):
            continue
        if any(v in p.parents for v in _VENDOR_DIRS):
            continue
        if p.name.endswith(_SKIP_SUFFIX):
            continue
        yield p


def _expr_from(lines, i, col, limit=12):
    """从 (行, 列) 开始取一个可能跨行的字符串表达式

    只按「行尾是 +」续行，避免把相邻语句里的路径拼进来（最初版本就踩了这个坑）。
    """
    parts = [lines[i][col:]]
    j = i
    while parts[-1].rstrip().endswith("+") and j + 1 < len(lines) and len(parts) < limit:
        j += 1
        parts.append(lines[j])
    return "\n".join(parts)


_STRING_LIT = re.compile(r"'([^']*)'")
_CLOSERS = ")]};,+'\""


def _code(gap_text: str = "") -> str:
    """去掉分隔符/括号/加号后剩下的拼接内容（空串 = 这里没有变量拼接）"""
    return "".join(ch for ch in gap_text if ch not in _CLOSERS and not ch.isspace())


def _first_arg(expr: str) -> str:
    """取路径参数本身：截到第一个顶层逗号（`request('GET', <这里>, undefined)`）"""
    depth = 0
    quote = ""
    for i, ch in enumerate(expr):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return expr[:i]
    return expr


def _literal_path(expr, query_vars=frozenset()):
    """把表达式里的路径字面量拼成骨架：拼接进来的变量折叠成 `*`

    例：`'/stream/sessions/' + id + '/messages?limit=' + n`
        → `/stream/sessions/*/messages`
    拼接的只是查询串（下一个字面量以 `?` 开头，或尾部变量是查询串变量）时不产生 `*`：
    例：`'/tools/' + (s ? '?source=' + s : '')` → `/tools`
    三元分支（`+ (editing ? '/' + id : '')`）是可选的：按所在节点比较，不产生 `*`
    例：`'/management/skills' + (editing ? '/' + id : '')` → `/management/skills`
    `/api` 前缀按 vite 代理规则剥掉：`/api/stream/pet/chat` → `/stream/pet/chat`
    """
    expr = _first_arg(expr)
    path = ""
    last_end = 0
    pending_gap = ""
    m = None
    for m in _STRING_LIT.finditer(expr):
        lit = m.group(1)
        gap = _code(expr[last_end:m.start()])
        last_end = m.end()
        if lit.startswith("?"):
            # `+ id + '?depth='` 的 id 是路径参数；
            # `+ (s ? '?source=' …)` 的拼接只是 query（gap 里带三元的 `?`）
            if path and gap and "?" not in gap:
                path += "*"
            break
        if not lit.startswith("/") and not path:
            continue
        # gap 里是否有真正的拼接表达式（+ id / + encodeURIComponent(id)）
        # 三元里的分支是可选的，不能当成路径参数
        if path and gap and "?" not in gap and ":" not in gap:
            pending_gap = "*"
        path += pending_gap + lit
        pending_gap = ""
        if "?" in lit:
            break
    else:
        tail = _code(expr[last_end:]) if m else ""
        if path and tail and "?" not in tail and tail not in query_vars:
            path += "*"

    if path.startswith(_PROXY_PREFIX + "/"):
        path = path[len(_PROXY_PREFIX):]
    elif path == _PROXY_PREFIX:
        path = "/"
    path = path.split("?")[0]
    if path != "/":
        path = path.rstrip("/")
    if path == "/":
        return "/"            # 根路由（systemInfo 读 GET /）
    return path if path.startswith(_PATH_PREFIXES) else ""


def _query_vars(text: str):
    """本文件里存放查询串的变量名（拼在路径尾部时不当路径参数）"""
    names = set(_DEFAULT_QUERY_VARS)
    for line in text.splitlines():
        if "URLSearchParams" not in line and "= '?" not in line and '= "?' not in line:
            continue
        decl = _QUERY_VAR_DECL.match(line)
        if decl:
            names.add(decl.group(1))
    return names


def collect_frontend_calls():
    """→ {(method, path_skeleton)}；path 以 `*` 表示被拼接掉的一段"""
    calls, _ = _collect()
    return calls


def _collect():
    """→ (calls, unexplained)

    unexplained: 出现 `request(`/`fetch(` 但抽取不出路径、且不属于「路径由参数传入」
    的调用点。这类调用不能静默跳过——静默跳过正是 §109 那个 404 能藏住的原因。
    """
    calls = set()
    unexplained = []
    for f in _frontend_files():
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        query_vars = _query_vars(text)
        rel = str(f.relative_to(PROJECT_ROOT))
        for i, line in enumerate(lines):
            for m in _REQUEST_CALL.finditer(line):
                arg = _first_arg(_expr_from(lines, i, m.end())).strip()
                path = _literal_path(arg, query_vars)
                if path:
                    calls.add((m.group(1), path))
                elif not _BARE_IDENT.fullmatch(arg):
                    unexplained.append((rel, i + 1, line.strip()[:120]))
            for m in _FETCH_CALL.finditer(line):
                raw = m.group(1)
                if not raw.startswith(_PATH_PREFIXES):
                    continue
                # 引号也要算进表达式，否则 _STRING_LIT 匹配不到（早期版本在这里
                # 把每一条 fetch 调用都丢掉了，测试照样全绿）
                expr = _expr_from(lines, i, line.index(raw) - 1)
                method = "GET"
                mm = _METHOD_IN_OPTS.search("\n".join(lines[i:i + 6]))
                if mm:
                    method = mm.group(1)
                path = _literal_path(expr, query_vars)
                if path:
                    calls.add((method, path))
                else:
                    unexplained.append((rel, i + 1, line.strip()[:120]))
            # 非字面量的 fetch 也要有结论（反引号模板串 / fetch(url) 以前完全看不见）
            for m in _FETCH_ANY.finditer(line):
                rest = _expr_from(lines, i, m.end()).lstrip()
                if rest.startswith(("'", '"')) or rest.startswith("BASE + '"):
                    continue          # 字面量调用，已由 _FETCH_CALL 处理
                if rest.startswith(_GENERIC_FETCH.get(rel, "\0")):
                    continue          # 泛化封装，路径由参数传入
                if _NON_API_FETCH.get(rel, "\0") in rest:
                    continue          # 非后端 API（已备注原因）
                unexplained.append((rel, i + 1, line.strip()[:120]))
    return calls, unexplained


def _app_routes():
    from api.main import app

    out = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        if not path:
            continue
        methods = set(getattr(r, "methods", None) or []) or {"WS"}
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            out.add((m, re.sub(r"\{[^}]+\}", "*", path).rstrip("/")))
    return out


def _matches(skeleton: str, route_path: str) -> bool:
    """严格逐段比对：骨架的 `*` 必须对上路由的路径参数

    不能把 `*` 放宽成「任意一段」——`GET /config/*`（= 前端 `'/config/' + key`）
    会误匹配 `GET /config/api-key`，正是 §109 那个 bug 的形状。
    """
    s = [x for x in skeleton.split("/") if x]
    r = [x for x in route_path.split("/") if x]
    if len(s) != len(r):
        return False
    for a, b in zip(s, r):
        if a == "*":
            if b != "*":
                return False
        elif a != b:
            return False
    return True


def unknown_routes(calls):
    """返回后端不存在的 (method, path)；单独拆出来便于自测"""
    routes = _app_routes()
    bad = []
    for method, skeleton in sorted(calls):
        if not any(m == method and _matches(skeleton, path) for m, path in routes):
            bad.append((method, skeleton))
    return bad


def test_frontend_calls_have_backend_routes():
    calls = collect_frontend_calls()
    # 抽取本身要有结果，避免因为正则失效而「零调用 = 全绿」
    # 114 是修复 fetch / /api 前缀后本机实测条数；低于 100 说明抽取又退化了
    assert len(calls) >= 100, f"前端请求抽取异常，只抽到 {len(calls)} 条"
    bad = unknown_routes(calls)
    assert not bad, "以下前端请求在后端没有对应路由（会 404，且前端多半静默吞掉）：\n" + "\n".join(
        f"  {m} {p}" for m, p in bad
    )


def test_no_silently_dropped_calls():
    """抽取不出来的调用必须显式解释，不能静默丢弃

    唯一允许的形态是 `request('GET', path)`（api.js 里的泛化封装，路径由调用方给）。
    新增其它写法时要么改约定，要么在这里说明——静默丢弃等于没测。
    """
    _, unexplained = _collect()
    assert not unexplained, (
        "以下请求出现但无法解析出路径（会被静默跳过，等同于没检查）；"
        "请改成字面量路径，或按 api.js 的 `request(method, pathVar)` 封装：\n"
        + "\n".join(f"  {f}:{n}: {s}" for f, n, s in unexplained)
    )


def test_contract_checker_catches_the_historical_404():
    """自测：这套检查能抓出 §109 的真实 bug（GET /config/{key} 不存在）"""
    bad = unknown_routes({("GET", "/config/DESKTOP_PET_ENABLED")})
    assert bad == [("GET", "/config/DESKTOP_PET_ENABLED")]


def test_contract_checker_accepts_dynamic_segments():
    """含路径参数的合法路由不能被误判（避免为了绿而放宽检查）"""
    assert unknown_routes({("POST", "/stream/pet/personas/*/activate")}) == []
    assert unknown_routes({("GET", "/stream/sessions/*/messages")}) == []


def test_contract_checker_strips_vite_proxy_prefix():
    """页面里直接写 `/api/...` 的调用也要能被检查（vite 代理会剥掉 /api）"""
    assert _literal_path("'/api/management/skills'") == "/management/skills"
    assert _literal_path("'/api/stream/pet/chat', {") == "/stream/pet/chat"


def test_contract_checker_parses_fetch_arguments():
    """fetch 表达式必须从引号开始取，否则路径字面量匹配不到（早期漏检根因）"""
    lines = ["const r = await fetch('/api/management/skills', { headers: {} })"]
    m = _FETCH_CALL.search(lines[0])
    expr = _expr_from(lines, 0, lines[0].index(m.group(1)) - 1)
    assert _literal_path(expr) == "/management/skills"


def test_contract_checker_treats_ternary_suffix_as_optional():
    """`'/skills' + (editing ? '/' + id : '')` 是可选的子路径，不能抽成 `/skills*/*`"""
    assert _literal_path("'/management/skills' + (editing ? '/' + id : '')") == "/management/skills"
    assert _literal_path("'/tools/ai' + (edit ? '/' + id : '')") == "/tools/ai"


def test_contract_checker_flags_non_literal_fetch(monkeypatch, tmp_path):
    """反引号模板串 / 变量路径的 fetch 不能被静默跳过（早期只认单引号字面量）"""
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "x.js").write_text(
        "fetch(`/stream/pet/${id}`)\nfetch(url)\nfetch('/stream/pet/chat', {})\n",
        encoding="utf-8",
    )
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "FRONTEND", tmp_path / "frontend")
    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)

    calls, unexplained = _collect()
    assert calls == {("GET", "/stream/pet/chat")}   # 字面量仍能抽出来
    assert len(unexplained) == 2, unexplained          # 另两条必须上报


def test_contract_checker_treats_query_vars_as_query():
    """尾部拼的是查询串变量时不能当路径参数（否则 `/stats*` 会假报 404）"""
    assert _literal_path("'/management/api-requests/stats' + q", {"q"}) == (
        "/management/api-requests/stats"
    )


@pytest.mark.parametrize("path,method", [
    ("/stream/pet/voice/status", "GET"),
    ("/stream/pet/personas", "GET"),
    ("/stream/pet/personas", "POST"),
    ("/stream/pet/config", "PUT"),
])
def test_pet_endpoints_used_by_persona_and_voice_ui_exist(path, method):
    """人设页/语音状态用到的端点必须在路由表里（显式列名，失败信息更好读）"""
    assert unknown_routes({(method, path)}) == []
