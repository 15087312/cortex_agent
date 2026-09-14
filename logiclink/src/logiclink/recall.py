"""recall -- fetch named code blocks by their ``# @link:block <Name>``.

``logiclink --recall <Name>`` prints every code block labelled with that name
(file, line range, source), so architecture terms can be recalled from code
instead of grepped by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

from .evidence import facts_of

_BLOCK_RE = re.compile(r"#\s*@link:\s*(?:block\s+)?([A-Za-z_][A-Za-z0-9_.]*)")


def _block_slice(text: str, lineno: int, funcs) -> tuple[str, int, int]:
    """Return (code, start, end) for the block whose @link comment is at lineno."""
    lines = text.splitlines()
    i = lineno - 1
    if i < 0 or i >= len(lines):
        return "", lineno, lineno
    # Function-level block: comment is followed (possibly via decorators) by a def.
    j = i + 1
    while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("@")):
        j += 1
    if j < len(lines) and lines[j].lstrip().startswith(("def ", "async def")):
        span = next((f for f in funcs if f.lineno == j + 1), None)
        end = span.end_lineno if span else j + 1
        return "\n".join(lines[i:end]), lineno, end
    # Inline block: the comment line itself.
    return lines[i], lineno, lineno


def _iter_blocks(text: str, path: str):
    """Yield (name, path, line, code) for every @link:block comment."""
    facts = facts_of(text)
    funcs = facts.funcs
    for i, raw in enumerate(text.splitlines(), start=1):
        m = _BLOCK_RE.search(raw)
        if not m:
            continue
        name = m.group(1)
        code, start, end = _block_slice(text, i, funcs)
        yield name, path, start, end, code


def scan_blocks(files: list[str]) -> list[tuple[str, str, int, int, str]]:
    """Scan source files for named blocks. ``files`` are absolute paths."""
    out: list[tuple[str, str, int, int, str]] = []
    for f in files:
        if not f.endswith(".py"):
            continue
        try:
            text = Path(f).read_text(encoding="utf-8")
        except OSError:
            continue
        out.extend(_iter_blocks(text, f))
    return out


def render_recall(blocks: list[tuple[str, str, int, int, str]], name: str | None, cwd: Path) -> str:
    """Render recall results as text."""
    if name is None:
        counts: dict[str, int] = {}
        for b in blocks:
            counts[b[0]] = counts.get(b[0], 0) + 1
        if not counts:
            return "(no @link:block declarations found)\n"
        lines = [f"{len(counts)} named blocks:"]
        for n in sorted(counts):
            lines.append(f"  {n:28s} x{counts[n]}")
        return "\n".join(lines) + "\n"

    lines: list[str] = []
    hits = [b for b in blocks if b[0] == name]
    if not hits:
        return f"(no block named `{name}`)\n"
    for bname, path, start, end, code in hits:
        try:
            rel = str(Path(path).relative_to(cwd))
        except ValueError:
            rel = path
        lines.append(f"== {bname}  {rel}:{start}-{end}  ({end - start + 1} lines)")
        lines.append(code)
        lines.append("")
    return "\n".join(lines)
