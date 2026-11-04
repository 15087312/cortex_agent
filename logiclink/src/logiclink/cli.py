"""CLI entry: ``logiclink``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ast_terms as T
from .check import run
from .config import load_config


def _render_text(diags, root: Path) -> str:
    lines: list[str] = []
    for d in diags:
        try:
            rel = str(Path(d.path).relative_to(root))
        except ValueError:
            rel = d.path
        lines.append(f"{d.level}[{d.code}]: {d.message}")
        lines.append(f"  --> {rel}:{d.line}:{d.col}")
        src = _source_line(d.path, d.line)
        if src is not None:
            lines.append("   |")
            lines.append(f"{d.line} | {src}")
            lines.append(f"   | {' ' * (d.col - 1)}^^^^^")
        if d.note:
            lines.append(f"note: {d.note}")
    return "\n".join(lines)


def _source_line(path: str, line: int) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()[line - 1].strip()
    except (OSError, IndexError):
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="logiclink", description="Link language deterministic checker (v1)")
    p.add_argument("path", nargs="?", default=".", help="project root (contains link.toml)")
    p.add_argument("--strict", action="store_true", help="W010 warnings become errors")
    p.add_argument("--strict-warnings", action="store_true", help="any warning exits non-zero")
    p.add_argument("--json", action="store_true", help="machine-readable diagnostics")
    p.add_argument("--explain", metavar="NAME", help="print the expanded term for NAME and exit")
    p.add_argument("--allow-arch", action="store_true", help="allow eq/alias drift against the baseline for this run")
    p.add_argument("--arch-update", action="store_true", help="record the current eq/alias set as the baseline (E305)")
    p.add_argument("--graph", action="store_true", help="render the declared vs real call graph (mermaid)")
    p.add_argument("--drift-only", action="store_true", help="with --graph, only show declared/real drift")
    p.add_argument("--html", metavar="OUT", help="write the full-repo module dependency panorama (interactive HTML/SVG)")
    p.add_argument("--html-flow", metavar="OUT", help="write the wired flow view (interactive HTML/SVG)")
    p.add_argument("--html-detail", metavar="OUT", help="write the three-level drill-down view (inter-module / intra-module / logic nodes)")
    p.add_argument("--recall", metavar="NAME", nargs="?", const="", help="recall named @link:block code blocks (no arg: list all names)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.path).resolve()
    cfg = load_config(root)

    if args.explain:
        from .collect import collect
        from .parser import parse_term

        result = collect(root, cfg.scan_roots, cfg.exclude, cfg.extensions)
        table: dict[str, str] = {}
        for d in result.decls:
            if d.kind in ("eq", "alias") and d.term_str and d.name not in table:
                table[d.name] = d.term_str
        for d in result.decls:
            if d.kind == "block" and d.term_str and d.name not in table:
                table[d.name] = d.term_str
        if args.explain not in table:
            print(f"unknown symbol: {args.explain}", file=sys.stderr)
            return 1
        term = parse_term(table[args.explain], cfg.lexicon)

        def resolve(n: str):
            return parse_term(table[n], cfg.lexicon) if n in table else None

        print(T.fmt(T.normalize(term, resolve)))
        return 0

    if args.recall is not None:
        from .collect import collect
        from .recall import render_recall, scan_blocks

        result = collect(root, cfg.scan_roots, cfg.exclude, cfg.extensions)
        blocks = scan_blocks(result.files)
        print(render_recall(blocks, args.recall or None, root))
        return 0

    if args.arch_update:
        from . import arch
        from .collect import collect

        result = collect(root, cfg.scan_roots, cfg.exclude, cfg.extensions)
        path, count = arch.save_baseline(root, arch.fingerprints(result.decls, cfg.lexicon))
        print(f"logiclink: recorded {count} eq/alias fingerprints -> {path}")
        return 0

    if args.graph or args.html or args.html_flow or args.html_detail:
        from .collect import collect

        result = collect(root, cfg.scan_roots, cfg.exclude, cfg.extensions)
        if args.html:
            from .viz_all import render_full_html

            out = render_full_html(result.decls, cfg, Path(args.html), root)
            print(f"logiclink: wrote full-repo panorama -> {out}")
            return 0
        if args.html_flow:
            from .viz import render_html

            out = render_html(result.decls, Path(args.html_flow))
            print(f"logiclink: wrote wired flow view -> {out}")
            return 0
        if args.html_detail:
            from .viz_detail import render_detail_html

            out = render_detail_html(result.decls, cfg, Path(args.html_detail), root)
            print(f"logiclink: wrote three-level detail view -> {out}")
            return 0
        from .v2_graph import render_graph

        print(render_graph(result.decls, drift_only=args.drift_only, allow_cycles=cfg.arch.allow_cycles))
        return 0

    diags, code = run(root, cfg, strict=args.strict, allow_arch=args.allow_arch)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "code": d.code,
                        "level": d.level,
                        "message": d.message,
                        "path": d.path,
                        "line": d.line,
                        "col": d.col,
                        "note": d.note,
                    }
                    for d in diags
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if diags:
            print(_render_text(diags, root))
        else:
            print("logiclink: ok")
    if args.strict_warnings and any(d.level == "warning" for d in diags):
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
