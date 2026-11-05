"""v2 viz-detail -- three-level drill-down architecture map.

Levels (tabs in one self-contained HTML):
  L1  inter-module   : 15 modules, curved routed edges, each edge labelled with
                       the concrete @link:block that initiates the dependency.
  L2  intra-module   : pick a module -> its eq groups unfolded into atom nodes,
                       each atom mapped to its real @link:block code block.
  L3  logic-node map : all 250 @link:block nodes grouped by their eq (e.g.
                       ThinkLoop = Retrieve·Think·Guard·(Write|Emit) is one
                       collapsible group); groups send to other groups based on
                       real cross-module calls.

All three levels share pan/zoom, hover highlight, and click-for-detail.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import ast_terms as T
from .evidence import CodeFacts, facts_of
from .parser import parse_term
from .viz import _CSS
from .viz_all import _module_map, _real_edges

_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "tests"}

_CSS_EXTRA = """
.tabs { display:flex; gap:4px; margin-bottom:10px; }
.tab { padding:7px 16px; border:1px solid var(--border); border-radius:8px 8px 0 0;
       background:#ECEBE5; cursor:pointer; font-size:13px; color:var(--sub); }
.tab.active { background:#fff; color:var(--text); font-weight:600; border-bottom-color:#fff; }
.view { display:none; }
.view.active { display:block; }
.edge.plain { stroke:#9AA7B4; stroke-width:1.6; opacity:0.7; }
.edge.plain.hot { stroke-width:2.8; opacity:1; }
.edge.group { stroke:#6B8FB5; stroke-width:2; }
.edge.group.hot { stroke-width:3.5; }
.node.wired rect { stroke:#2F6FB2; stroke-width:2.5; }
.node.eqgroup rect { fill:#F0F4F8; stroke:#5A7FA8; stroke-width:1.8; }
.node.eqgroup.collapsed rect { fill:#E4ECF4; }
.node.atom rect { fill:#fff; stroke:#B8C5D4; stroke-width:1.2; }
.node.atom.hascode rect { stroke:#4A7AB5; stroke-width:1.8; }
.n-eq { font-size:11px; fill:#3B5A7A; font-weight:600; }
.n-atom { font-size:10.5px; fill:#2A3A4A; }
.n-atom.nocode { fill:#9AA7B4; }
.elabel { font-size:10px; fill:#5A6A7A; }
.elabel.hot { fill:var(--text); font-weight:600; }
.modsel { margin-bottom:8px; }
.modsel select { padding:5px 10px; border-radius:6px; border:1px solid var(--border); font-size:13px; }
.wrap { overflow:auto; }
svg { min-width:1100px; }
"""


# ---------------------------------------------------------------- data

def _block_module(path: str, mods: dict[str, dict], root: Path) -> str:
    p = Path(path).resolve()
    for long, m in mods.items():
        try:
            p.relative_to(m["dir"])
            return long
        except ValueError:
            continue
    return "?"


def _block_span(text: str, lineno: int, funcs) -> tuple[int, int]:
    lines = text.splitlines()
    i = lineno - 1
    j = i + 1
    while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("@")):
        j += 1
    if j < len(lines) and lines[j].lstrip().startswith(("def ", "async def")):
        span = next((f for f in funcs if f.lineno == j + 1), None)
        return lineno, span.end_lineno if span else j + 1
    return lineno, lineno


def _eq_of_block(
    name: str, atom: str | None, module: str, eq_index: dict[str, dict[str, list[str]]]
) -> str | None:
    """Block name like App_Think -> eq 'App'; fallback: module eq containing atom.

    The fallback used to be a no-op loop (``if atom in eq_index[module]: pass``)
    that always returned None, so a block whose name does not mirror its eq never
    joined a group.  Match the atom against each eq's parsed atom list instead.
    """
    module_eqs = eq_index.get(module, {})
    if "_" in name:
        prefix = name.split("_")[0]
        if prefix in module_eqs:
            return prefix
    if name in module_eqs:
        return name
    if atom:
        for eqn, atoms in module_eqs.items():
            if atom in atoms:
                return eqn
    return None


def collect_detail(decls, cfg, root: Path) -> dict:
    mods = _module_map(root, cfg.scan_roots)
    real = _real_edges(mods)

    # module public symbol index: module -> set of public func/class names
    mod_symbols: dict[str, set[str]] = defaultdict(set)
    for long, m in mods.items():
        d = m["dir"]
        if not d.is_dir():
            continue
        for py in d.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in py.parts):
                continue
            try:
                f = facts_of(py.read_text(encoding="utf-8"))
            except OSError:
                continue
            for fn in f.funcs:
                if not fn.name.startswith("_"):
                    mod_symbols[long].add(fn.name)
            for cls in getattr(f, "classes", []):
                if not cls.name.startswith("_"):
                    mod_symbols[long].add(cls.name)

    # eq index: module -> {eq_name: [atoms]}
    eq_index: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for d in decls:
        if d.kind != "eq" or not d.term_str:
            continue
        try:
            atoms = [a.name for a in T.linear_atoms(parse_term(d.term_str))]
        except Exception:
            continue
        mod = _block_module(d.path, mods, root)
        eq_index[mod][d.name] = atoms

    # blocks
    blocks: list[dict] = []
    file_cache: dict[str, tuple[str, CodeFacts]] = {}
    for d in decls:
        if d.kind != "block":
            continue
        mod = _block_module(d.path, mods, root)
        if d.path not in file_cache:
            try:
                text = Path(d.path).read_text(encoding="utf-8")
                file_cache[d.path] = (text, facts_of(text))
            except OSError:
                file_cache[d.path] = ("", facts_of(""))
        text, facts = file_cache[d.path]
        start, end = _block_span(text, d.line, facts.funcs)
        calls = [name for ln, name in facts.calls if start <= ln <= end]
        imports = [name for ln, name in facts.imports if start <= ln <= end]
        atom = d.term_str
        eqn = _eq_of_block(d.name, atom, mod, eq_index)
        blocks.append({
            "name": d.name, "atom": atom, "module": mod, "path": d.path,
            "line": start, "end": end, "calls": calls[:12], "imports": imports[:8],
            "eq": eqn,
        })

    # eq groups with their blocks
    groups: list[dict] = []
    block_by_eq: dict[str, list[dict]] = defaultdict(list)
    for b in blocks:
        if b["eq"]:
            block_by_eq[b["eq"]].append(b)
    for mod, eqs in eq_index.items():
        for eqn, atoms in eqs.items():
            grp_blocks = block_by_eq.get(eqn, [])
            groups.append({
                "name": eqn, "module": mod, "atoms": atoms,
                "blocks": [b["name"] for b in grp_blocks],
                "wired": eqn in {"MainFlow", "ApiThink", "ApiMemory", "ApiOutput", "ApiPerceive",
                                 "ThinkLoop", "PerceiveLoop", "MemRetrieve", "Distrib", "App"},
            })

    # inter-module edges: each edge carries concrete call pairs (block -> target symbol)
    mod_edges: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    seen_call: set[tuple[str, str, str, str]] = set()
    for b in blocks:
        for imp in b["imports"]:
            target = None
            for long, m in mods.items():
                if long == b["module"]:
                    continue
                if imp == long or imp.startswith(long + ".") or imp == m["short"] or imp.startswith(m["short"] + "."):
                    target = long
                    break
            if not target:
                continue
            target_syms = mod_symbols.get(target, set())
            matched = []
            for c in b["calls"]:
                bare = c.rsplit(".", 1)[-1] if "." in c else c
                if bare in target_syms and bare not in matched:
                    matched.append(bare)
            atom = b.get("atom") or ""
            if matched:
                for sym in matched[:3]:
                    key = (b["module"], target, b["name"], sym)
                    if key not in seen_call:
                        seen_call.add(key)
                        mod_edges[(b["module"], target)].append((b["name"], sym, atom))
            else:
                key = (b["module"], target, b["name"], "")
                if key not in seen_call:
                    seen_call.add(key)
                    mod_edges[(b["module"], target)].append((b["name"], "", atom))

    # group-level edges (group A's blocks import group B's module)
    group_edges: dict[tuple[str, str], int] = defaultdict(int)
    for b in blocks:
        if not b["eq"]:
            continue
        for imp in b["imports"]:
            for long, m in mods.items():
                if long == b["module"]:
                    continue
                if imp == long or imp.startswith(long + "."):
                    for g in groups:
                        if g["module"] == long and g["name"] != b["eq"]:
                            group_edges[(b["eq"], g["name"])] += 1
                    break

    return {
        "mods": mods, "real": real, "blocks": blocks, "groups": groups,
        "mod_edges": dict(mod_edges), "group_edges": dict(group_edges),
        "eq_index": {m: dict(eq_index[m]) for m in eq_index},
    }


# ---------------------------------------------------------------- L1 layout

def _l1_layout(mods, real, mod_edges) -> tuple[list[dict], list[dict], int, int]:
    ind: dict[str, int] = defaultdict(int)
    outd: dict[str, int] = defaultdict(int)
    for (a, b) in real:
        outd[a] += 1
        ind[b] += 1
    layers: dict[int, list[str]] = {0: [], 1: [], 2: []}
    for long, m in mods.items():
        if outd.get(long, 0) >= 5:
            layers[0].append(long)
        elif ind.get(long, 0) >= 3 and outd.get(long, 0) <= 1:
            layers[2].append(long)
        else:
            layers[1].append(long)
    for k in layers:
        layers[k].sort(key=lambda name: mods[name]["short"])

    NW, NH = 195, 168
    GAP = 42
    W = max(1200, max((len(layers[k]) * (NW + GAP) - GAP + 120 for k in layers if layers[k]), default=1200))
    H = 80 + 3 * NH + 2 * 120
    laid = []
    for k in (0, 1, 2):
        row = layers[k]
        if not row:
            continue
        x0 = (W - (len(row) * (NW + GAP) - GAP)) / 2
        y = 80 + k * (NH + 120)
        for i, long in enumerate(row):
            m = mods[long]
            laid.append({"id": m["short"], "long": long, "x": x0 + i * (NW + GAP), "y": y,
                         "w": NW, "h": NH, "layer": k, "root": m["root"]})
    pos = {n["long"]: (n["x"], n["y"]) for n in laid}
    edges = []
    seen = set()
    for (la, lb), call_pairs in sorted(mod_edges.items()):
        if la not in pos or lb not in pos:
            continue
        seen.add((la, lb))
        labels = []
        for bn, sym, atom in call_pairs[:2]:
            tag = f"[{atom}]" if atom else ""
            labels.append(f"{bn}{tag}→{sym}" if sym else f"{bn}{tag}")
        label = ", ".join(labels)
        if len(call_pairs) > 2:
            label += f" +{len(call_pairs)-2}"
        edges.append({"from": mods[la]["short"], "to": mods[lb]["short"], "from_long": la, "to_long": lb,
                      "kind": "plain", "label": label, "count": len(call_pairs),
                      "calls": [{"block": bn, "symbol": sym, "atom": atom} for bn, sym, atom in call_pairs]})
    for (la, lb) in sorted(real):
        if (la, lb) in seen or la not in pos or lb not in pos:
            continue
        edges.append({"from": mods[la]["short"], "to": mods[lb]["short"], "from_long": la, "to_long": lb,
                      "kind": "plain", "label": "", "count": 0})
    return laid, edges, W, H


def _l1_geometry(edges, pos, NW, NH):
    out = []
    for e in edges:
        ax, ay = pos[e["from_long"]]
        bx, by = pos[e["to_long"]]
        if ax == bx and ay == by:
            continue
        reverse = (e["to_long"], e["from_long"]) in {(x["from_long"], x["to_long"]) for x in edges}
        shift = 9 if reverse else 0
        same_row = abs(ay - by) < 20
        if same_row:
            x1 = ax + (NW if ax < bx else 0)
            y1 = ay + NH * 0.5 + (shift if reverse else 0)
            x2 = bx + (0 if ax < bx else NW)
            y2 = by + NH * 0.5 + (shift if reverse else 0)
            mx = (x1 + x2) / 2
            bow = -34 if reverse else 0
            d = f"M {x1} {y1} C {mx} {y1 + bow}, {mx} {y2 + bow}, {x2} {y2}"
            lx, ly = mx, (y1 + y2) / 2 + bow - 8
        else:
            x1 = ax + NW / 2 + (shift if reverse else 0)
            y1 = ay + NH
            x2 = bx + NW / 2 + (shift if reverse else 0)
            y2 = by
            mx = (x1 + x2) / 2
            d = f"M {x1} {y1} C {x1} {y1 + 50}, {x2} {y2 - 50}, {x2} {y2}"
            lx, ly = mx, (y1 + y2) / 2
        out.append({**e, "d": d, "lx": lx, "ly": ly})
    return out


# ---------------------------------------------------------------- L2 layout (intra-module)

def _l2_layout(module: str, detail: dict) -> tuple[list[dict], list[dict], int, int]:
    eqs = detail["eq_index"].get(module, {})
    blocks = [b for b in detail["blocks"] if b["module"] == module]
    block_by_eq = defaultdict(list)
    for b in blocks:
        if b["eq"]:
            block_by_eq[b["eq"]].append(b)

    NW, NH = 170, 130
    GAP = 36
    groups = []
    for eqn, atoms in eqs.items():
        groups.append({"name": eqn, "atoms": atoms, "blocks": [b["name"] for b in block_by_eq.get(eqn, [])]})
    groups.sort(key=lambda g: -len(g["atoms"]))
    cols = min(4, max(1, len(groups)))
    W = cols * (NW + GAP) - GAP + 100
    rows = (len(groups) + cols - 1) // cols
    H = rows * (NH + 80) + 100
    laid = []
    for i, g in enumerate(groups):
        r, c = divmod(i, cols)
        x = 50 + c * (NW + GAP)
        y = 60 + r * (NH + 80)
        laid.append({"id": g["name"], "x": x, "y": y, "w": NW, "h": NH,
                     "atoms": g["atoms"], "blocks": g["blocks"], "eq": g["name"]})
    return laid, [], W, H


# ---------------------------------------------------------------- L3 layout (logic-node groups)

def _l3_layout(detail: dict) -> tuple[list[dict], list[dict], int, int]:
    groups = detail["groups"]
    group_edges = detail["group_edges"]
    mods = detail["mods"]
    # group by module column
    by_mod = defaultdict(list)
    for g in groups:
        if g["module"] in mods:
            by_mod[g["module"]].append(g)
    mod_order = sorted(by_mod.keys(), key=lambda m: mods[m]["short"])

    GW, GH = 180, 110
    GAPX, GAPY = 28, 30
    col_w = GW + GAPX
    W = len(mod_order) * col_w + 80
    max_rows = max(len(by_mod[m]) for m in mod_order)
    H = max_rows * (GH + GAPY) + 100

    laid = []
    for ci, mod in enumerate(mod_order):
        x = 40 + ci * col_w
        for ri, g in enumerate(sorted(by_mod[mod], key=lambda g: g["name"])):
            y = 50 + ri * (GH + GAPY)
            laid.append({"id": g["name"], "module": mod, "x": x, "y": y, "w": GW, "h": GH,
                         "atoms": g["atoms"], "blocks": g["blocks"], "wired": g["wired"],
                         "collapsed": True})
    pos = {g["id"]: (g["x"], g["y"]) for g in laid}
    edges = []
    for (ga, gb), cnt in sorted(group_edges.items()):
        if ga in pos and gb in pos and ga != gb:
            edges.append({"from": ga, "to": gb, "kind": "group", "count": cnt})
    return laid, edges, W, H


def _l3_geometry(edges, pos, GW, GH):
    out = []
    for e in edges:
        ax, ay = pos[e["from"]]
        bx, by = pos[e["to"]]
        if ax == bx:
            continue
        x1 = ax + (GW if ax < bx else 0)
        y1 = ay + GH / 2
        x2 = bx + (0 if ax < bx else GW)
        y2 = by + GH / 2
        mx = (x1 + x2) / 2
        d = f"M {x1} {y1} C {mx} {y1}, {mx} {y2}, {x2} {y2}"
        out.append({**e, "d": d})
    return out


# ---------------------------------------------------------------- render

def render_detail_html(decls, cfg, out_path, root: Path) -> Path:
    detail = collect_detail(decls, cfg, root)
    mods = detail["mods"]

    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

    marker = ('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" '
              'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
              '<path d="M 0 1 L 10 5 L 0 9 z" fill="currentColor" opacity="0.8"/></marker></defs>')

    # ---- L1 ----
    l1_nodes, l1_edges_raw, W1, H1 = _l1_layout(mods, detail["real"], detail["mod_edges"])
    l1_pos = {n["long"]: (n["x"], n["y"]) for n in l1_nodes}
    l1_edges = _l1_geometry(l1_edges_raw, l1_pos, 195, 168)

    def l1_node(n):
        return (f'<g class="node" id="l1n-{n["id"]}" data-id="{n["id"]}">'
                f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" rx="12"/>'
                f'<text class="n-title" x="{n["x"]+n["w"]/2}" y="{n["y"]+28}" text-anchor="middle">{esc(n["id"])}</text>'
                f'<text class="n-long" x="{n["x"]+n["w"]/2}" y="{n["y"]+46}" text-anchor="middle">{esc(n["long"])}</text>'
                f'<text class="n-sub" x="{n["x"]+12}" y="{n["y"]+72}">root: {esc(n["root"])}</text>'
                f'<text class="n-sub" x="{n["x"]+12}" y="{n["y"]+90}">{"编排" if n["layer"]==0 else "支撑" if n["layer"]==1 else "基础"}层</text>'
                f'</g>')

    l1_svg = (f'<svg viewBox="0 0 {W1} {H1}" xmlns="http://www.w3.org/2000/svg">{marker}'
              f'<g id="l1z">'
              + "".join(f'<path class="edge {e["kind"]}" d="{e["d"]}" data-from="{e["from"]}" data-to="{e["to"]}" marker-end="url(#arr)"/>'
                        + (f'<text class="elabel" x="{e["lx"]}" y="{e["ly"]}" text-anchor="middle">{esc(e["label"])}</text>' if e["label"] else "")
                        for e in l1_edges)
              + "".join(l1_node(n) for n in l1_nodes)
              + '</g></svg>')

    # ---- L2 (template, populated by JS per module) ----
    l2_modules = sorted([m for m in detail["eq_index"].keys() if m in mods], key=lambda m: mods[m]["short"])
    l2_svg = f'<svg id="l2svg" viewBox="0 0 800 400" xmlns="http://www.w3.org/2000/svg">{marker}<g id="l2z"></g></svg>'

    # ---- L3 ----
    l3_nodes, l3_edges_raw, W3, H3 = _l3_layout(detail)
    l3_pos = {g["id"]: (g["x"], g["y"]) for g in l3_nodes}
    l3_edges = _l3_geometry(l3_edges_raw, l3_pos, 180, 110)

    def l3_node(g):
        atoms = " · ".join(g["atoms"][:5]) + ("…" if len(g["atoms"]) > 5 else "")
        nb = len(g["blocks"])
        cls = "eqgroup collapsed" + (" wired" if g["wired"] else "")
        return (f'<g class="node {cls}" id="l3n-{g["id"]}" data-id="{g["id"]}">'
                f'<rect x="{g["x"]}" y="{g["y"]}" width="{g["w"]}" height="{g["h"]}" rx="10"/>'
                f'<text class="n-eq" x="{g["x"]+g["w"]/2}" y="{g["y"]+26}" text-anchor="middle">{esc(g["id"])}</text>'
                f'<text class="n-atom" x="{g["x"]+g["w"]/2}" y="{g["y"]+48}" text-anchor="middle">{esc(atoms)}</text>'
                f'<text class="n-sub" x="{g["x"]+g["w"]/2}" y="{g["y"]+70}" text-anchor="middle">{nb} code blocks · {esc(mods[g["module"]]["short"])}</text>'
                f'</g>')

    l3_svg = (f'<svg viewBox="0 0 {W3} {H3}" xmlns="http://www.w3.org/2000/svg">{marker}'
              f'<g id="l3z">'
              + "".join(f'<path class="edge {e["kind"]}" d="{e["d"]}" data-from="{e["from"]}" data-to="{e["to"]}" marker-end="url(#arr)"/>' for e in l3_edges)
              + "".join(l3_node(g) for g in l3_nodes)
              + '</g></svg>')

    nodes_json = json.dumps(detail["blocks"], ensure_ascii=False)
    groups_json = json.dumps(detail["groups"], ensure_ascii=False)
    eq_index_json = json.dumps(detail["eq_index"], ensure_ascii=False)
    mod_list_json = json.dumps([{"long": m, "short": mods[m]["short"]} for m in l2_modules], ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>logiclink 三层架构图</title>
<style>{_CSS}{_CSS_EXTRA}</style></head>
<body>
<h1>logiclink 三层架构图</h1>
<div class="sub">模块间（真实依赖+发起代码块） · 模块内（eq 展开到原子） · 逻辑节点（250 个 @link:block 按 eq 分组）</div>
<div class="tabs">
  <div class="tab active" data-view="l1">L1 模块间</div>
  <div class="tab" data-view="l2">L2 模块内</div>
  <div class="tab" data-view="l3">L3 逻辑节点</div>
</div>
<div id="tip"></div><div id="panel"></div>
<div class="view active" id="view-l1"><div class="wrap">{l1_svg}</div></div>
<div class="view" id="view-l2">
  <div class="modsel"><select id="l2mod"></select></div>
  <div class="wrap">{l2_svg}</div>
</div>
<div class="view" id="view-l3"><div class="wrap">{l3_svg}</div></div>
<script>
(function() {{
  var BLOCKS = {nodes_json};
  var GROUPS = {groups_json};
  var EQ_INDEX = {eq_index_json};
  var MOD_LIST = {mod_list_json};
  var tip = document.getElementById('tip');
  var panel = document.getElementById('panel');

  // tabs
  document.querySelectorAll('.tab').forEach(function(t) {{
    t.addEventListener('click', function() {{
      document.querySelectorAll('.tab').forEach(function(x){{x.classList.remove('active');}});
      document.querySelectorAll('.view').forEach(function(x){{x.classList.remove('active');}});
      t.classList.add('active');
      document.getElementById('view-' + t.dataset.view).classList.add('active');
    }});
  }});

  // common: pan/zoom per svg
  document.querySelectorAll('svg').forEach(function(svg) {{
    var z=1, tx=0, ty=0, drag=null;
    var g = svg.querySelector('g[id$="z"]');
    function apply(){{ if(g) g.setAttribute('transform','translate('+tx+','+ty+') scale('+z+')'); }}
    svg.addEventListener('wheel', function(ev){{ ev.preventDefault(); z=Math.min(3,Math.max(0.3,z*(ev.deltaY<0?1.12:0.89))); apply(); }}, {{passive:false}});
    svg.addEventListener('mousedown', function(ev){{ drag={{x:ev.clientX,y:ev.clientY}}; }});
    window.addEventListener('mousemove', function(ev){{ if(!drag)return; tx+=ev.clientX-drag.x; ty+=ev.clientY-drag.y; drag={{x:ev.clientX,y:ev.clientY}}; apply(); }});
    window.addEventListener('mouseup', function(){{ drag=null; }});
  }});

  // L1 hover/click
  document.querySelectorAll('#view-l1 .node').forEach(function(g) {{
    var id = g.dataset.id;
    g.addEventListener('mouseenter', function() {{
      document.querySelectorAll('#view-l1 .edge').forEach(function(p){{
        var hit=p.dataset.from===id||p.dataset.to===id; p.classList.toggle('hot',hit); p.classList.toggle('dim',!hit);
      }});
      document.querySelectorAll('#view-l1 .node').forEach(function(x){{ x.classList.toggle('dim', x.dataset.id!==id); }});
    }});
    g.addEventListener('mouseleave', function() {{
      document.querySelectorAll('#view-l1 .edge').forEach(function(p){{p.classList.remove('hot','dim');}});
      document.querySelectorAll('#view-l1 .node').forEach(function(x){{x.classList.remove('dim');}});
      tip.style.display='none';
    }});
    g.addEventListener('click', function() {{
      var inc=[], out=[];
      document.querySelectorAll('#view-l1 .edge').forEach(function(p){{
        var lbl = p.nextElementSibling && p.nextElementSibling.classList.contains('elabel') ? p.nextElementSibling.textContent : '';
        if(p.dataset.to===id) inc.push(p.dataset.from+' → '+id + (lbl ? '  ['+lbl+']' : ''));
        if(p.dataset.from===id) out.push(id+' → '+p.dataset.to + (lbl ? '  ['+lbl+']' : ''));
      }});
      panel.innerHTML='<span class="close" id="cp">✕</span><h2>'+id+'</h2>'
        +'<div class="row"><span class="k">出边（调用其他模块）</span><br>'+out.map(function(s){{return '&nbsp;↳ '+s;}}).join('<br>')+'</div>'
        +'<div class="row"><span class="k">入边（被其他模块调用）</span><br>'+inc.map(function(s){{return '&nbsp;↳ '+s;}}).join('<br>')+'</div>';
      panel.style.display='block';
      document.getElementById('cp').onclick=function(){{panel.style.display='none';}};
    }});
  }});

  // L2 module selector + render
  var sel = document.getElementById('l2mod');
  MOD_LIST.forEach(function(m){{ var o=document.createElement('option'); o.value=m.long; o.textContent=m.short; sel.appendChild(o); }});
  function renderL2(modLong) {{
    var eqs = EQ_INDEX[modLong] || {{}};
    var blocks = BLOCKS.filter(function(b){{return b.module===modLong;}});
    var g = document.getElementById('l2z'); g.innerHTML='';
    var names = Object.keys(eqs);
    var NW=170, NH=130, GAP=36, cols=Math.min(4,Math.max(1,names.length));
    var W=Math.max(900, cols*(NW+GAP)-GAP+100), rows=Math.ceil(names.length/cols), H=Math.max(400, rows*(NH+80)+100);
    document.getElementById('l2svg').setAttribute('viewBox','0 0 '+W+' '+H);
    names.forEach(function(eqn,i){{
      var atoms=eqs[eqn];
      var r=Math.floor(i/cols), c=i%cols;
      var x=50+c*(NW+GAP), y=60+r*(NH+80);
      var grp=document.createElementNS('http://www.w3.org/2000/svg','g');
      grp.setAttribute('class','node eqgroup');
      grp.innerHTML='<rect x="'+x+'" y="'+y+'" width="'+NW+'" height="'+NH+'" rx="10"/>'
        +'<text class="n-eq" x="'+(x+NW/2)+'" y="'+(y+26)+'" text-anchor="middle">'+eqn+'</text>'
        +'<text class="n-atom" x="'+(x+NW/2)+'" y="'+(y+48)+'" text-anchor="middle">'+atoms.slice(0,5).join(' · ')+(atoms.length>5?'…':'')+'</text>'
        +'<text class="n-sub" x="'+(x+12)+'" y="'+(y+72)+'">blocks: '+blocks.filter(function(b){{return b.eq===eqn;}}).length+'</text>';
      g.appendChild(grp);
    }});
  }}
  sel.addEventListener('change', function(){{ renderL2(sel.value); }});
  if(MOD_LIST.length) renderL2(MOD_LIST[0].long);

  // L3 group click -> expand/collapse atom nodes
  document.querySelectorAll('#view-l3 .node').forEach(function(g) {{
    g.addEventListener('click', function() {{
      var id=g.dataset.id;
      var grp=GROUPS.find(function(x){{return x.name===id;}});
      if(!grp) return;
      panel.innerHTML='<span class="close" id="cp">✕</span><h2>'+id+' <span style="font-weight:400;font-size:12px;color:#6B7280">'+grp.module+'</span></h2>'
        +'<div class="row"><span class="k">eq</span> <code>'+grp.atoms.join(' · ')+'</code></div>'
        +'<div class="row"><span class="k">代码块 ('+grp.blocks.length+')</span><br>'
        +grp.blocks.map(function(bn){{
            var b=BLOCKS.find(function(x){{return x.name===bn;}});
            return '&nbsp;↳ <b>'+bn+'</b>'+(b&&b.atom?' ['+b.atom+']':'')+(b?' '+b.path.split('/').slice(-2).join('/')+':'+b.line:'')+'<br>';
          }}).join('')+'</div>';
      panel.style.display='block';
      document.getElementById('cp').onclick=function(){{panel.style.display='none';}};
    }});
    g.addEventListener('mouseenter', function() {{
      var id=g.dataset.id;
      document.querySelectorAll('#view-l3 .edge').forEach(function(p){{
        var hit=p.dataset.from===id||p.dataset.to===id; p.classList.toggle('hot',hit); p.classList.toggle('dim',!hit);
      }});
      document.querySelectorAll('#view-l3 .node').forEach(function(x){{ x.classList.toggle('dim', x.dataset.id!==id); }});
    }});
    g.addEventListener('mouseleave', function() {{
      document.querySelectorAll('#view-l3 .edge').forEach(function(p){{p.classList.remove('hot','dim');}});
      document.querySelectorAll('#view-l3 .node').forEach(function(x){{x.classList.remove('dim');}});
    }});
  }});
}})();
</script>
</body></html>
"""
    out = Path(out_path)
    out.write_text(html, encoding="utf-8")
    return out
