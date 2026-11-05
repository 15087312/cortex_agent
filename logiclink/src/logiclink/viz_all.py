"""v2 viz-all -- full repository module-dependency panorama.

Unlike the flow view (wired modules only), this renderer shows **every**
module in ``link.toml [scan] roots`` plus the **real** import edges between
them, so the picture matches the project instead of the declarations:

  * modules whose imports were wired into ``app.link`` get a blue badge;
  * edges between wired modules are coloured by L3 status
    (green = declared & real, red solid = E301, orange dashed = E303,
     red dashed = E302);
  * every other edge is the real dependency (grey) -- not yet governed;
  * bidirectional real deps are drawn as two offset arcs;
  * hover a node to highlight its neighbours; click for per-edge evidence
    (file:line of the actual import).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .evidence import facts_of
from .v2_graph import _resolve_edges
from .viz import _CSS

_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "tests"}

_CSS_EXTRA = """
.edge.plain { stroke:#9AA7B4; stroke-width:1.8; opacity:0.75; }
.edge.plain.hot { stroke-width:3; opacity:1; }
.node.wired rect { stroke:#2F6FB2; stroke-width:2.5; }
.node.wired .wbadge { fill:#2F6FB2; }
.wbadge text { font-size:9.5px; fill:#fff; text-anchor:middle; }
.n-long { font-size:10.5px; fill:var(--sub); }
.wrap { overflow:auto; }
svg { min-width:1120px; }
"""


def _module_map(root: Path, scan_roots: list[str]) -> dict[str, dict]:
    mods: dict[str, dict] = {}
    for r in scan_roots:
        base = (root / r).resolve()
        if base.is_dir():
            mods[r.replace("/", ".")] = {"long": r.replace("/", "."), "short": Path(r).name, "root": r, "dir": base}
    return mods


def _real_edges(mods: dict[str, dict]) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """(longA, longB) -> [(file, line)] real imports. Long-name matching wins;
    bare short-name matching only for modules with a unique short name."""
    short_counts = Counter(m["short"] for m in mods.values())
    edges: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for long, m in mods.items():
        for py in sorted(m["dir"].rglob("*.py")):
            if any(p in _SKIP_DIRS for p in py.parts):
                continue
            try:
                facts = facts_of(py.read_text(encoding="utf-8"))
            except OSError:
                continue
            for ln, imp in facts.imports:
                hit = False
                for long2, m2 in mods.items():
                    if long2 == long:
                        continue
                    if imp == long2 or imp.startswith(long2 + "."):
                        edges.setdefault((long, long2), []).append((str(py), ln))
                        hit = True
                if hit:
                    continue
                for long2, m2 in mods.items():
                    if long2 == long or short_counts[m2["short"]] != 1:
                        continue
                    s = m2["short"]
                    if imp == s or imp.startswith(s + "."):
                        edges.setdefault((long, long2), []).append((str(py), ln))
    return edges


def _layer_of(short: str, ind: dict, outd: dict) -> int:
    if outd.get(short, 0) >= 5:
        return 0
    if ind.get(short, 0) >= 3 and outd.get(short, 0) <= 1:
        return 2
    return 1


def _layout(mods: dict, edges: dict) -> tuple[list[dict], int, int]:
    ind: dict[str, int] = {}
    outd: dict[str, int] = {}
    for (a, b) in edges:
        outd[a] = outd.get(a, 0) + 1
        ind[b] = ind.get(b, 0) + 1
    layers: dict[int, list[str]] = {0: [], 1: [], 2: []}
    for long, m in mods.items():
        layers[_layer_of(m["short"], ind, outd)].append(long)
    for k in layers:
        layers[k].sort(key=lambda name: mods[name]["short"])

    NW, NH = 200, 172
    GAP = 44
    W = max(1200, max((len(layers[k]) * (NW + GAP) - GAP + 140 for k in layers if layers[k]), default=1200))
    H = 90 + 3 * NH + 2 * 132
    laid = []
    for k in (0, 1, 2):
        row = layers[k]
        if not row:
            continue
        x0 = (W - (len(row) * (NW + GAP) - GAP)) / 2
        y = 90 + k * (NH + 132)
        for i, long in enumerate(row):
            m = mods[long]
            laid.append({
                "id": m["short"], "long": long, "root": m["root"], "wired": False,
                "x": x0 + i * (NW + GAP), "y": y, "w": NW, "h": NH, "layer": k,
            })
    return laid, W, H


def render_full_html(decls, cfg, out_path: str | Path, root: Path | None = None) -> Path:
    root = Path(root) if root is not None else Path.cwd()
    mods = _module_map(root, cfg.scan_roots)
    if not mods:
        raise SystemExit("viz-all: no module directories in [scan] roots")
    real = _real_edges(mods)
    laid, W, H = _layout(mods, real)

    use_target = {d.name: (d.term_str or "") for d in decls if d.kind == "use" and d.op == ":="}
    wired_short = {t.split(".")[0] for t in use_target.values() if "." in t}
    short_to_long: dict[str, str] = {}
    for long, m in mods.items():
        short_to_long.setdefault(m["short"], long)
    for n in laid:
        n["wired"] = n["id"] in wired_short

    declared_short: set[tuple[str, str]] = set()
    if use_target:
        declared, _real, _ds, _re = _resolve_edges(decls, use_target)
        for (a, b) in declared:
            la, lb = short_to_long.get(a), short_to_long.get(b)
            if la and lb and la in mods and lb in mods:
                declared_short.add((mods[la]["short"], mods[lb]["short"]))

    # edge kinds (in short-name space)
    def kind_of(a: str, b: str) -> str:
        wa, wb = a in wired_short, b in wired_short
        if (a, b) in declared_short:
            return "ok"
        if (b, a) in declared_short:
            return "warn"
        if wa and wb:
            return "e302"
        return "plain"

    edges_out = []
    seen: set[tuple[str, str]] = set()
    for (la, lb) in sorted(real):
        a, b = mods[la]["short"], mods[lb]["short"]
        edges_out.append({"from": a, "to": b, "kind": kind_of(a, b), "ev": real[(la, lb)][:3]})
        seen.add((a, b))
    for (a, b) in sorted(declared_short):
        if (a, b) in seen:
            continue
        edges_out.append({"from": a, "to": b, "kind": "bad", "ev": []})
        seen.add((a, b))

    # geometry
    pos = {n["id"]: (n["x"], n["y"]) for n in laid}
    ge = []
    for e in edges_out:
        ax, ay = pos[e["from"]]
        bx, by = pos[e["to"]]
        if ax == bx and ay == by:
            continue
        reverse = (e["to"], e["from"]) in seen
        shift = 10 if reverse else 0
        same_row = abs(ay - by) < 20
        if same_row:
            # from right edge to left edge (or reverse), with an offset arc for reverse pairs
            x1 = ax + (200 if ax < bx else 0)
            y1 = ay + 172 * 0.5 + (shift if reverse else 0)
            x2 = bx + (0 if ax < bx else 200)
            y2 = by + 172 * 0.5 + (shift if reverse else 0)
            mx = (x1 + x2) / 2
            bow = -38 if reverse else 0
            d = f"M {x1} {y1} C {mx} {y1 + bow}, {mx} {y2 + bow}, {x2} {y2}"
        else:
            x1, y1 = ax + 200 / 2 + (shift if reverse else 0), ay + 172
            x2, y2 = bx + 200 / 2 + (shift if reverse else 0), by
            d = f"M {x1} {y1} L {x2} {y2}"
        ge.append({**e, "d": d})

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

    def node_svg(n: dict, idx: int) -> str:
        wired = ' wired' if n["wired"] else ''
        rows = [
            f'<g class="node{wired}" id="node-{idx}" data-id="{n["id"]}">',
            f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" rx="12"/>',
            f'<text class="n-title" x="{n["x"] + n["w"] / 2}" y="{n["y"] + 30}" text-anchor="middle">{esc(n["id"])}</text>',
            f'<text class="n-long" x="{n["x"] + n["w"] / 2}" y="{n["y"] + 48}" text-anchor="middle">{esc(n["long"])}</text>',
            f'<text class="n-sub" x="{n["x"] + 13}" y="{n["y"] + 78}">root: {esc(n["root"])}</text>',
            f'<text class="n-sub" x="{n["x"] + 13}" y="{n["y"] + 96}">layer: {"编排" if n["layer"] == 0 else "支撑" if n["layer"] == 1 else "基础"}</text>',
        ]
        if n["wired"]:
            rows.append(
                f'<rect class="wbadge" x="{n["x"] + n["w"] / 2 - 26}" y="{n["y"] + 8}" width="52" height="15" rx="7.5"/>'
                f'<text x="{n["x"] + n["w"] / 2}" y="{n["y"] + 19}">wired</text>'
            )
        rows.append("</g>")
        return "".join(rows)

    marker = ('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" '
              'markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">'
              '<path d="M 0 1 L 10 5 L 0 9 z" fill="currentColor" opacity="0.8"/></marker></defs>')

    stats = {"ok": 0, "bad": 0, "warn": 0, "e302": 0, "plain": 0}
    for e in ge:
        stats[e["kind"]] += 1
    nodes_json = json.dumps([{k: n[k] for k in ("id", "long", "root", "wired", "x", "y", "w", "h", "layer")} for n in laid], ensure_ascii=False)
    edges_json = json.dumps([{k: e[k] for k in ("from", "to", "kind", "ev", "d")} for e in ge], ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>logiclink 全仓库模块依赖全景</title>
<style>{_CSS}{_CSS_EXTRA}</style>
</head>
<body>
<h1>logiclink 全仓库模块依赖全景</h1>
<div class="sub">数据：{len(laid)} 个模块（link.toml [scan] roots） · {len(ge)} 条真实/声明边（import 级证据，双向分别计数）</div>
<div class="legend">
  <span><i class="ok"></i>已声明且真实（{stats["ok"]}）</span>
  <span><i class="bad"></i>声明无真实 E301（{stats["bad"]}）</span>
  <span><i class="warn"></i>方向相反 E303（{stats["warn"]}）</span>
  <span><i class="dashed" style="border-color:#D64550"></i>wired 间未声明 E302（{stats["e302"]}）</span>
  <span><i style="border-color:#9AA7B4"></i>真实依赖·未治理（{stats["plain"]}）</span>
  <span style="color:#6B7280">· 蓝徽标 = 已接入 app.link · 悬停看邻边 · 点击看 import 证据</span>
</div>
<div class="wrap">
  <div id="tip"></div>
  <div id="panel"></div>
  <svg id="g" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
    {marker}
    <g id="zoom">
      {''.join(f'<path class="edge {e["kind"]}" d="{e["d"]}" data-from="{e["from"]}" data-to="{e["to"]}" marker-end="url(#arr)"/>' for e in ge)}
      {''.join(node_svg(n, i) for i, n in enumerate(laid))}
    </g>
  </svg>
</div>
<script>
(function() {{
  var svg = document.getElementById('g');
  var tip = document.getElementById('tip');
  var panel = document.getElementById('panel');
  var nodes = {nodes_json};
  var edges = {edges_json};
  function edgesOf(id) {{ return edges.filter(function(e) {{ return e.from === id || e.to === id; }}); }}
  function setHot(id) {{
    var ns = edgesOf(id).map(function(e) {{ return [e.from, e.to]; }}).reduce(function(a, b) {{ return a.concat(b); }}, []);
    svg.querySelectorAll('.edge').forEach(function(p) {{
      var hit = p.getAttribute('data-from') === id || p.getAttribute('data-to') === id;
      p.classList.toggle('hot', hit); p.classList.toggle('dim', !hit);
    }});
    svg.querySelectorAll('.node').forEach(function(g) {{ g.classList.toggle('dim', g.getAttribute('data-id') !== id); }});
  }}
  function clearHot() {{
    svg.querySelectorAll('.edge').forEach(function(p) {{ p.classList.remove('hot', 'dim'); }});
    svg.querySelectorAll('.node').forEach(function(g) {{ g.classList.remove('dim'); }});
  }}
  function badge(kind) {{
    var m = {{ ok: ['ok', '已声明✓'], bad: ['bad', 'E301'], warn: ['warn', 'E303'], e302: ['bad', 'E302'], plain: ['', '真实'] }};
    var b = m[kind] || ['', ''];
    return '<span class="badge ' + b[0] + '">' + b[1] + '</span>';
  }}
  nodes.forEach(function(n) {{
    var g = document.getElementById('node-' + nodes.indexOf(n));
    g.addEventListener('mouseenter', function() {{ setHot(n.id); showTip(n); }});
    g.addEventListener('mouseleave', function() {{ tip.style.display = 'none'; clearHot(); }});
    g.addEventListener('click', function() {{ openPanel(n); }});
  }});
  function showTip(n) {{
    var inc = edgesOf(n.id).filter(function(e) {{ return e.to === n.id; }}).length;
    var out = edgesOf(n.id).filter(function(e) {{ return e.from === n.id; }}).length;
    tip.innerHTML = '<b>' + n.id + '</b> &nbsp;' + n.long +
      (n.wired ? ' &nbsp;<span style="color:#8FD0FF">● wired</span>' : '') +
      '<br>依赖 ' + out + ' 个模块 · 被 ' + inc + ' 个模块依赖';
    tip.style.display = 'block';
    var r = svg.getBoundingClientRect();
    tip.style.left = (n.x + n.w) + 'px';
    tip.style.top = (n.y) + 'px';
  }}
  function evList(e) {{
    if (!e.ev.length) return '（无 import 证据——纯声明边）';
    return e.ev.slice(0, 3).map(function(v) {{
      var p = v[0]; var parts = p.split('/'); var f = parts.slice(-2).join('/');
      return '&nbsp;↳ ' + f + ':' + v[1];
    }}).join('<br>');
  }}
  function openPanel(n) {{
    var inc = edgesOf(n.id).filter(function(e) {{ return e.to === n.id; }});
    var out = edgesOf(n.id).filter(function(e) {{ return e.from === n.id; }});
    var h = '<span class="close" id="closePanel">✕</span>';
    h += '<h2>' + n.id + (n.wired ? ' <span class="badge ok">wired</span>' : '') + '</h2>';
    h += '<div class="row"><span class="k">模块</span> <code>' + n.long + '</code></div>';
    h += '<div class="row"><span class="k">root</span> <code>' + n.root + '</code></div>';
    if (out.length) h += '<div class="row"><span class="k">依赖（出边 ' + out.length + '）</span><br>' + out.map(function(e) {{ return '&nbsp;↳ ' + e.to + badge(e.kind) + '<br>' + evList(e); }}).join('<br>') + '</div>';
    if (inc.length) h += '<div class="row"><span class="k">被依赖（入边 ' + inc.length + '）</span><br>' + inc.map(function(e) {{ return '&nbsp;↳ ' + e.from + badge(e.kind) + '<br>' + evList(e); }}).join('<br>') + '</div>';
    panel.innerHTML = h;
    var cp = document.getElementById('closePanel');
    if (cp) cp.addEventListener('click', function() {{ panel.style.display = 'none'; }});
    panel.style.display = 'block';
  }}
  var z = 1, tx = 0, ty = 0, drag = null;
  function apply() {{ var g = svg.querySelector('#zoom'); g.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + z + ')'); }}
  svg.addEventListener('wheel', function(ev) {{
    ev.preventDefault();
    var f = ev.deltaY < 0 ? 1.12 : 0.89;
    z = Math.min(3, Math.max(0.35, z * f));
    apply();
  }}, {{ passive: false }});
  svg.addEventListener('mousedown', function(ev) {{ drag = {{ x: ev.clientX, y: ev.clientY }}; }});
  window.addEventListener('mousemove', function(ev) {{
    if (!drag) return;
    tx += ev.clientX - drag.x; ty += ev.clientY - drag.y;
    drag = {{ x: ev.clientX, y: ev.clientY }}; apply();
  }});
  window.addEventListener('mouseup', function() {{ drag = null; }});
}})();
</script>
</body>
</html>
"""
    out = Path(out_path)
    out.write_text(html, encoding="utf-8")
    return out
