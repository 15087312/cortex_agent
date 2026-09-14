"""v2 viz -- self-contained HTML/SVG renderer for the full logic chain.

Produces a single-file HTML (inline SVG + inline JS, zero external deps) that
shows, for every wired module in the root ``app.link``:

  * modules as nodes -- use name, ``module.Symbol``, port atoms (input/output),
    and the module's ``eq``;
  * declared chains (``eq A · B · C``) as solid edges carrying the chain name;
  * real-vs-declared status per edge:
        green solid   declared AND real import exists
        red solid     declared but no real import (E301)
        orange dashed declared direction opposite to real (E303)
        red dashed    real import but never declared (E302)
  * hover: highlight a node and every edge touching it;
  * click: side panel with ports / eq / source file + incident edges.

Deterministic layout (no layout library): modules with zero in-degree are
placed on the top row (orchestration layer), the rest on the main row in
``use`` declaration order.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import ast_terms as T
from .parser import Decl, parse_term
from .v2_graph import _module_dirs, _resolve_edges

# ---------------------------------------------------------------- data model

_CSS = """
:root { --text:#1A1B1C; --sub:#6B7280; --bg:#F4F3EE; --card:#FFFFFF;
        --border:#E4E3DD; --accent:#A3D5E8; --ok:#2E9E5B; --bad:#D64550;
        --warn:#C77B14; --node:#FFFFFF; }
* { box-sizing: border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--text);
       font: 13.5px/1.5 'PingFang SC','Roboto','Segoe UI',Arial,sans-serif; }
h1 { font-size:18px; margin:0 0 4px; }
.sub { color:var(--sub); font-size:12px; margin-bottom:12px; }
.legend { display:flex; flex-wrap:wrap; gap:14px; padding:10px 12px;
          background:var(--card); border:1px solid var(--border);
          border-radius:12px; margin-bottom:12px; font-size:12px; }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.legend i { display:inline-block; width:26px; height:0; border-top:2.5px solid #000; }
.legend i.dashed { border-top-style: dashed; }
.legend i.ok { border-color:var(--ok); }
.legend i.bad { border-color:var(--bad); }
.legend i.warn { border-color:var(--warn); border-top-style:dashed; }
.wrap { position:relative; background:var(--card); border:1px solid var(--border);
        border-radius:12px; padding:8px; }
svg { display:block; width:100%; height:auto; touch-action:none; }
.node rect { fill:var(--node); stroke:#9FB4C9; stroke-width:1.5; }
.node.zoom rect { stroke-width:3; }
.node text { font-family:inherit; }
.n-title { font-size:15px; font-weight:600; fill:var(--text); }
.n-sub   { font-size:11px; fill:var(--sub); }
.n-port  { font-size:11px; fill:#3B4A5A; }
.n-eq    { font-size:10.5px; fill:var(--sub); }
.edge { stroke-width:2.5; fill:none; }
.edge.ok   { stroke:var(--ok); }
.edge.bad  { stroke:var(--bad); }
.edge.warn { stroke:var(--warn); stroke-dasharray:6 4; }
.edge.e302 { stroke:var(--bad); stroke-dasharray:6 4; }
.edge.hot  { stroke-width:4.5; }
.edge.dim  { opacity:0.12; }
.node.dim { opacity:0.25; }
.elabel { font-size:11px; fill:var(--sub); }
.elabel.hot { fill:var(--text); font-weight:600; }
#tip { position:absolute; display:none; max-width:300px; background:#1A1B1C;
       color:#fff; font-size:12px; padding:8px 10px; border-radius:8px;
       pointer-events:none; z-index:5; }
#panel { position:absolute; top:8px; right:8px; width:300px; max-height:88%;
         overflow:auto; background:#fff; border:1px solid var(--border);
         border-radius:12px; padding:12px 14px; font-size:12.5px;
         box-shadow:0 4px 14px rgba(0,0,0,0.08); display:none; z-index:4; }
#panel h2 { font-size:14px; margin:0 0 6px; }
#panel .row { margin:5px 0; }
#panel .k { color:var(--sub); font-size:11px; }
#panel code { background:#F1F3F5; border-radius:4px; padding:1px 5px;
              font-size:11.5px; word-break:break-all; }
#panel .close { float:right; cursor:pointer; color:var(--sub); font-size:14px; }
.badge { display:inline-block; font-size:10.5px; border-radius:10px;
         padding:1px 8px; margin-left:6px; }
.badge.ok   { background:rgba(46,158,91,0.14); color:#1F7A45; }
.badge.bad  { background:rgba(214,69,80,0.14); color:#B02A37; }
.badge.warn { background:rgba(199,123,20,0.16); color:#8A5A0F; }
.edge.bad  .elabel, .edge.e302 .elabel { fill:var(--bad); }
.edge.warn .elabel { fill:var(--warn); }
"""


def _rel(p: str) -> str:
    try:
        return os.path.relpath(p)
    except ValueError:
        return p


def _collect(decls: list[Decl]) -> dict | None:
    """Extract everything the renderer needs from collected decls."""
    use_target = {d.name: (d.term_str or "") for d in decls if d.kind == "use" and d.op == ":="}
    if not use_target:
        return None
    module_of = {n: (t.split(".")[0] if "." in t else n) for n, t in use_target.items()}
    symbol_of = {n: t.split(".")[-1] for n, t in use_target.items()}
    use_of_module: dict[str, str] = {}
    for n, m in module_of.items():
        use_of_module.setdefault(m, n)
    dirs = _module_dirs(decls, use_target)
    declared, real, dsource, revidence = _resolve_edges(decls, use_target)
    # _resolve_edges works in module-name space; map back to use-name space.
    def to_use(a: str) -> str:
        return use_of_module.get(a, a)

    declared = {(to_use(a), to_use(b)) for (a, b) in declared}
    real_use: set[tuple[str, str]] = set()
    real_ev: dict[tuple[str, str], tuple[str, int]] = {}
    for (a, b) in real:
        ua, ub = to_use(a), to_use(b)
        real_use.add((ua, ub))
        real_ev.setdefault((ua, ub), revidence.get((a, b), ("", 0)))
    real = real_use

    ports: dict[str, dict[str, list[str]]] = {}
    for d in decls:
        if d.kind in ("input", "output") and d.term_str:
            sym = d.name.split(".")[0]
            atoms = sorted({a.strip() for a in d.term_str.strip("[]").split(",") if a.strip()})
            ports.setdefault(sym, {}).setdefault(d.kind, []).extend(a for a in atoms if a not in ports.get(sym, {}).get(d.kind, []))

    eq_of: dict[str, str] = {}
    eq_file: dict[str, str] = {}
    for d in decls:
        if d.kind == "eq" and d.term_str:
            eq_of.setdefault(d.name, d.term_str)
            eq_file.setdefault(d.name, _rel(d.path))
            if d.name in symbol_of.values():
                eq_of[d.name] = d.term_str

    nodes = {}
    for name, target in use_target.items():
        mod = module_of[name]
        sym = symbol_of[name]
        p = ports.get(sym, {})
        nodes[name] = {
            "id": name,
            "module": mod,
            "symbol": sym,
            "in": p.get("input", []),
            "out": p.get("output", []),
            "eq": eq_of.get(sym, ""),
            "file": eq_file.get(sym, _rel(dirs.get(mod, Path(".")) / "link")),
        }

    chains = []
    for d in decls:
        if d.kind != "eq" or not d.term_str:
            continue
        try:
            atoms = [a.name for a in T.linear_atoms(parse_term(d.term_str))]
        except Exception:
            continue
        if any(a in use_target for a in atoms):
            chains.append({"name": d.name, "names": atoms, "file": _rel(d.path)})

    # edges with per-chain labels
    chain_of: dict[tuple[str, str], list[str]] = {}
    for c in chains:
        for i in range(len(c["names"]) - 1):
            a, b = c["names"][i], c["names"][i + 1]
            if a in use_target and b in use_target:
                chain_of.setdefault((a, b), []).append(c["name"])

    edges = []
    seen: set[tuple[str, str]] = set()
    for (a, b) in sorted(declared):
        real_ok = (a, b) in real
        reverse = (b, a) in real and not real_ok
        kind = "ok" if real_ok else ("warn" if reverse else "bad")
        edges.append({"from": a, "to": b, "kind": kind, "label": "/".join(chain_of.get((a, b), ["?"]))})
        seen.add((a, b))
    for (a, b) in sorted(real):
        if (a, b) in seen or (b, a) in seen:
            continue
        fpath, fline = real_ev.get((a, b), ("", 0))
        edges.append({"from": a, "to": b, "kind": "e302", "label": f"E302 {Path(fpath).name}:{fline}" if fpath else "E302"})

    return {"nodes": nodes, "edges": edges, "chains": chains, "dirs": {m: _rel(str(p)) for m, p in dirs.items()}}


# ---------------------------------------------------------------- layout

_NW, _NH = 230, 190
_TW, _TH = 230, 120
_GAP = 76


def _layout(data: dict) -> tuple[list[dict], list[dict], int, int]:
    nodes, edges = data["nodes"], data["edges"]
    in_deg = {n: 0 for n in nodes}
    for e in edges:
        if e["kind"] != "e302":
            in_deg[e["to"]] = in_deg.get(e["to"], 0) + 1
    top = sorted([n for n in nodes if in_deg[n] == 0], key=lambda n: nodes[n]["module"])
    main = sorted([n for n in nodes if n not in top], key=lambda n: list(nodes).index(n))

    W = max(1100, len(main) * (_NW + _GAP) - _GAP + 120)
    H = 620
    pos: dict[str, tuple[float, float]] = {}
    if top:
        x = W / 2 - _TW / 2
        pos[top[0]] = (x, 56)
    for i, n in enumerate(main):
        pos[n] = (60 + i * (_NW + _GAP), 300)

    laid: list[dict] = []
    for n, (x, y) in pos.items():
        nd = nodes[n]
        laid.append({"id": n, "x": x, "y": y, "w": _NW, "h": _NH,
                     "top": n in top, **nd})
    ledges: list[dict] = []
    for e in edges:
        a, b = pos[e["from"]], pos[e["to"]]
        if e["kind"] == "e302":
            ledges.append({**e, "x1": a[0] + _NW, "y1": a[1], "x2": b[0], "y2": b[1], "top": False})
        elif e["from"] in top or e["to"] in top:
            ledges.append({**e, "x1": a[0] + _TW / 2, "y1": a[1] + _TH,
                           "x2": b[0] + _NW / 2, "y2": b[1], "top": True})
        else:
            ledges.append({**e, "x1": a[0] + _NW, "y1": a[1] + _NH * 0.62,
                           "x2": b[0], "y2": b[1] + _NH * 0.62, "top": False})
    return laid, ledges, W, H


# ---------------------------------------------------------------- html

def render_html(decls: list[Decl], out_path: str | Path) -> Path:
    data = _collect(decls)
    if data is None:
        raise SystemExit("viz: no `use name := module.Symbol` declarations found")
    laid, ledges, W, H = _layout(data)
    out = Path(out_path)

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                 .replace('"', "&quot;"))

    def node_svg(n: dict) -> str:
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        pins = " · ".join(n["in"]) if n["in"] else "—"
        pouts = " · ".join(n["out"]) if n["out"] else "—"
        eq = n["eq"]
        if len(eq) > 34:
            eq = eq[:33] + "…"
        rows = [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/>',
            f'<text class="n-title" x="{x + w / 2}" y="{y + 30}" text-anchor="middle">{esc(n["id"])}</text>',
            f'<text class="n-sub" x="{x + w / 2}" y="{y + 49}" text-anchor="middle">{esc(n["module"])}.{esc(n["symbol"])}</text>',
            f'<text class="n-port" x="{x + 14}" y="{y + 78}">in : {esc(pins)}</text>',
            f'<text class="n-port" x="{x + 14}" y="{y + 96}">out: {esc(pouts)}</text>',
            f'<text class="n-eq" x="{x + 14}" y="{y + 122}">eq: {esc(eq)}</text>',
        ]
        return "".join(rows)

    def edge_svg(e: dict) -> str:
        kind = e["kind"]
        cls = "edge " + kind
        lab = esc(e["label"])
        if e["top"]:
            d = f'M {e["x1"]} {e["y1"]} L {e["x2"]} {e["y2"]}'
            lx, ly = (e["x1"] + e["x2"]) / 2, (e["y1"] + e["y2"]) / 2 - 8
        else:
            mx = (e["x1"] + e["x2"]) / 2
            bow = -46 if kind == "e302" else 0
            c1x, c1y = e["x1"] + 40, e["y1"] + bow
            c2x, c2y = e["x2"] - 40, e["y2"] + bow
            d = f'M {e["x1"]} {e["y1"]} C {c1x} {c1y}, {c2x} {c2y}, {e["x2"]} {e["y2"]}'
            lx, ly = mx, (e["y1"] + e["y2"]) / 2 + bow - 10
        return (f'<path class="{cls}" d="{d}" data-from="{e["from"]}" data-to="{e["to"]}" '
                f'data-kind="{kind}" marker-end="url(#arr)"/>'
                f'<text class="elabel" x="{lx}" y="{ly}" text-anchor="middle" '
                f'data-edge="{e["from"]}→{e["to"]}">{lab}</text>')

    marker = ('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" '
              'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
              '<path d="M 0 1 L 10 5 L 0 9 z" fill="currentColor" opacity="0.85"/></marker></defs>')

    svg_nodes = "".join(node_svg(n) for n in laid)
    svg_edges = "".join(edge_svg(e) for e in ledges)
    stats = {
        "ok": sum(1 for e in ledges if e["kind"] == "ok"),
        "bad": sum(1 for e in ledges if e["kind"] == "bad"),
        "warn": sum(1 for e in ledges if e["kind"] == "warn"),
        "e302": sum(1 for e in ledges if e["kind"] == "e302"),
    }
    nodes_json = json.dumps([{k: v for k, v in n.items() if k in ("id", "module", "symbol", "in", "out", "eq", "file", "x", "y", "w", "h")} for n in laid], ensure_ascii=False)
    edges_json = json.dumps([{k: v for k, v in e.items() if k in ("from", "to", "kind", "label")} for e in ledges], ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>logiclink 全流程逻辑链路</title>
<style>{_CSS}</style>
</head>
<body>
<h1>logiclink 全流程逻辑链路</h1>
<div class="sub">数据：{len(laid)} 个接线模块（use 声明） · {len(ledges)} 条边（声明 + 真实 import 比对） · 生成自根 app.link 与各模块 .link</div>
<div class="legend">
  <span><i class="ok"></i>声明且有真实调用（{stats["ok"]}）</span>
  <span><i class="bad"></i>声明无真实调用 E301（{stats["bad"]}）</span>
  <span><i class="warn"></i>方向相反 E303（{stats["warn"]}）</span>
  <span><i class="dashed" style="border-color:#D64550"></i>真实未声明 E302（{stats["e302"]}）</span>
  <span style="color:#6B7280">· 悬停节点高亮关联边 · 点击节点看详情</span>
</div>
<div class="wrap">
  <div id="tip"></div>
  <div id="panel"></div>
  <svg id="g" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
    {marker}
    <g id="zoom">
      {svg_edges}
      {svg_nodes}
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
  var byId = {{}}; nodes.forEach(function(n) {{ byId[n.id] = n; }});
  var hot = {{}};
  function edgesOf(id) {{ return edges.filter(function(e) {{ return e.from === id || e.to === id; }}); }}
  function svgEls(sel) {{ return Array.prototype.slice.call(svg.querySelectorAll(sel)); }}
  function setHot(id) {{
    var ns = edgesOf(id).map(function(e) {{ return [e.from, e.to]; }}).reduce(function(a, b) {{ return a.concat(b); }}, []);
    svgEls('.edge').forEach(function(p) {{
      var hit = p.getAttribute('data-from') === id || p.getAttribute('data-to') === id;
      p.classList.toggle('hot', hit); p.classList.toggle('dim', !hit);
    }});
    svgEls('.elabel').forEach(function(t) {{
      var hit = t.getAttribute('data-edge').indexOf(id) === 0 || t.getAttribute('data-edge').endsWith('→' + id);
      t.classList.toggle('hot', hit);
    }});
    svgEls('.node').forEach(function(g) {{
      g.classList.toggle('dim', g.id !== 'node-' + id);
    }});
  }}
  function clearHot() {{
    svgEls('.edge').forEach(function(p) {{ p.classList.remove('hot', 'dim'); }});
    svgEls('.elabel').forEach(function(t) {{ t.classList.remove('hot'); }});
    svgEls('.node').forEach(function(g) {{ g.classList.remove('dim'); }});
  }}
  nodes.forEach(function(n) {{
    var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('class', 'node'); g.setAttribute('id', 'node-' + n.id);
    g.setAttribute('transform', 'translate(0,0)');
    g.addEventListener('mouseenter', function() {{ setHot(n.id); showTip(n, event); }});
    g.addEventListener('mousemove', function(ev) {{ showTip(n, ev); }});
    g.addEventListener('mouseleave', function() {{ tip.style.display = 'none'; clearHot(); }});
    g.addEventListener('click', function() {{ openPanel(n); }});
    var ns = '';
    ns += '<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h + '" rx="14"/>';
    ns += '<text class="n-title" x="' + (n.x + n.w / 2) + '" y="' + (n.y + 30) + '" text-anchor="middle">' + n.id + '</text>';
    ns += '<text class="n-sub" x="' + (n.x + n.w / 2) + '" y="' + (n.y + 49) + '" text-anchor="middle">' + n.module + '.' + n.symbol + '</text>';
    ns += '<text class="n-port" x="' + (n.x + 14) + '" y="' + (n.y + 78) + '">in : ' + (n['in'].join(' · ') || '—') + '</text>';
    ns += '<text class="n-port" x="' + (n.x + 14) + '" y="' + (n.y + 96) + '">out: ' + (n.out.join(' · ') || '—') + '</text>';
    ns += '<text class="n-eq" x="' + (n.x + 14) + '" y="' + (n.y + 122) + '">eq: ' + (n.eq.length > 34 ? n.eq.slice(0, 33) + '…' : n.eq) + '</text>';
    g.innerHTML = ns;
    svg.querySelector('#zoom').appendChild(g);
  }});
  function showTip(n, ev) {{
    tip.innerHTML = '<b>' + n.id + '</b> &nbsp;' + n.module + '.' + n.symbol +
      '<br>in : ' + (n['in'].join(' · ') || '—') +
      '<br>out: ' + (n.out.join(' · ') || '—') +
      '<br><span style="opacity:.7">' + (n.eq || '') + '</span>';
    tip.style.display = 'block';
    var r = svg.getBoundingClientRect();
    tip.style.left = (ev.clientX - r.left + 14) + 'px';
    tip.style.top = (ev.clientY - r.top + 14) + 'px';
  }}
  function openPanel(n) {{
    var inc = edgesOf(n.id).filter(function(e) {{ return e.to === n.id; }});
    var out = edgesOf(n.id).filter(function(e) {{ return e.from === n.id; }});
    function badge(e) {{
      var m = {{ ok: ['ok', '真实✓'], bad: ['bad', 'E301'], warn: ['warn', 'E303'], e302: ['bad', 'E302'] }};
      var b = m[e.kind] || ['', ''];
      return '<span class="badge ' + b[0] + '">' + b[1] + '</span>';
    }}
    var h = '<span class="close" id="closePanel">✕</span>';
    h += '<h2>' + n.id + ' <span style="font-weight:400;font-size:12px;color:#6B7280">' + n.module + '.' + n.symbol + '</span></h2>';
    h += '<div class="row"><span class="k">input</span> <code>' + (n['in'].join(' · ') || '—') + '</code></div>';
    h += '<div class="row"><span class="k">output</span> <code>' + (n.out.join(' · ') || '—') + '</code></div>';
    h += '<div class="row"><span class="k">eq</span> <code>' + (n.eq || '—') + '</code></div>';
    h += '<div class="row"><span class="k">声明</span> <code>' + n.file + '</code></div>';
    if (out.length) h += '<div class="row"><span class="k">出边</span><br>' + out.map(function(e) {{ return '&nbsp;↳ ' + e.from + ' → ' + e.to + badge(e); }}).join('<br>') + '</div>';
    if (inc.length) h += '<div class="row"><span class="k">入边</span><br>' + inc.map(function(e) {{ return '&nbsp;↳ ' + e.from + ' → ' + e.to + badge(e); }}).join('<br>') + '</div>';
    panel.innerHTML = h;
    var cp = document.getElementById('closePanel');
    if (cp) cp.addEventListener('click', function() {{ panel.style.display = 'none'; }});
    panel.style.display = 'block';
  }}
  // pan & zoom
  var z = 1, tx = 0, ty = 0, drag = null;
  function apply() {{ var g = svg.querySelector('#zoom'); g.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + z + ')'); }}
  svg.addEventListener('wheel', function(ev) {{
    ev.preventDefault();
    var f = ev.deltaY < 0 ? 1.12 : 0.89;
    z = Math.min(3, Math.max(0.4, z * f));
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
    out.write_text(html, encoding="utf-8")
    return out
