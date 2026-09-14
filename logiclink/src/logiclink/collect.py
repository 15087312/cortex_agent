"""Collector: read ``*.link`` files and ``# @link:`` lines in ``*.py`` (v1)."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from .parser import ParseError, parse_decl, Decl

_PY_LINK_RE = re.compile(r"#\s*@link:\s*(.+)$")


@dataclass
class CollectResult:
    decls: list[Decl]
    failures: list[tuple[str, int, int, str]]  # (path, line, col, message) -> E001
    files: list[str]  # scanned source files (absolute posix)


def _excluded(rel_posix: str, parts: list[str], patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch("/" + rel_posix, pat):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def collect(root: Path, scan_roots: list[str], exclude: list[str], extensions: list[str]) -> CollectResult:
    decls: list[Decl] = []
    failures: list[tuple[str, int, int, str]] = []
    files: list[str] = []
    exts = set(extensions)
    for sroot in scan_roots:
        base = (root / sroot).resolve()
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = sorted(p for p in base.rglob("*") if p.is_file())
        else:
            continue
        for path in candidates:
            if path.suffix not in exts:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.name
            if _excluded(rel, list(path.relative_to(root).parts) if path.is_relative_to(root) else [path.name], exclude):
                continue
            files.append(str(path))
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if path.suffix == ".link":
                _collect_link_file(text, str(path), decls, failures)
            elif path.suffix == ".py":
                _collect_py_file(text, str(path), decls, failures)
    # Deterministic order: by file then line.
    decls.sort(key=lambda d: (d.path, d.line))
    return CollectResult(decls, failures, sorted(files))


def _collect_link_file(text: str, path: str, decls: list[Decl], failures: list) -> None:
    last_export = ""
    current_state = ""
    for i, raw in enumerate(text.splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("@link:"):
            s = s[len("@link:") :].strip()
        try:
            d = parse_decl(s, path, i, raw.find(s) + 1 if s in raw else 1)
        except ParseError as e:
            failures.append((path, i, 1, str(e)))
            continue
        if d.kind == "export":
            last_export = d.name
        elif d.kind in ("input", "output"):
            # Bare `input: [...]` / `output: [...]` binds to the nearest export.
            if not d.name:
                if not last_export:
                    failures.append((path, i, 1, f"{d.kind} declaration without a preceding `export`"))
                    continue
                d.name = last_export
        elif d.kind == "state":
            current_state = d.name
        elif d.kind in ("state_init", "state_trans"):
            if not current_state:
                failures.append((path, i, 1, f"{d.kind} declaration outside a `state` block"))
                continue
            d.name = current_state
        decls.append(d)


def _collect_py_file(text: str, path: str, decls: list[Decl], failures: list) -> None:
    for i, raw in enumerate(text.splitlines(), start=1):
        m = _PY_LINK_RE.search(raw)
        if not m:
            continue
        body = m.group(1).strip()
        if not body:
            failures.append((path, i, m.start() + 1, "empty @link declaration"))
            continue
        try:
            decls.append(parse_decl(body, path, i, m.start(1) + 1))
        except ParseError as e:
            failures.append((path, i, m.start(1) + 1, str(e)))
