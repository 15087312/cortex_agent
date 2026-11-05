"""Architecture gate -- record and diff ``eq`` / ``alias`` fingerprints.

``[arch] forbid_eq_change = true`` used to mean "refuse to run": ``cli``
returned 1 whenever the flag was on, without comparing anything.  That made the
gate useless (a repo with the flag enabled could never pass) and meaningless
(nothing was actually checked).

The gate is now a baseline diff.  ``logiclink . --arch-update`` records the
canonical fingerprint of every ``eq`` / ``alias`` into a committed file::

    <root>/.logiclink/arch.json
    {"version": 1, "eq": {"MainFlow": "1a2b3c..."}, "alias": {}}

A later run recomputes the fingerprints and reports **E305** only for names
that were added, removed or changed.  Fingerprints hash the normalized AST
(``ast_terms.normalize``), never the raw source text, so whitespace, `>>` vs `·`
and comments do not trigger a false positive -- the same rule v1 already
follows for equality.

Diagnostics (returned as tuples):
    E305  the eq/alias set differs from the recorded baseline (or no baseline
          exists while the gate is enabled)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import ast_terms as T
from .config import Config
from .parser import Decl, parse_term

BASELINE_REL = Path(".logiclink") / "arch.json"
_VERSION = 1
_KINDS = ("eq", "alias")


def _canonical(term_str: str, lexicon: dict) -> str:
    """Normalized AST text for a term, or the raw text if it does not parse.

    Unparsable terms are still tracked (E001 reports them separately) so that
    editing broken syntax cannot silently slip past the gate.
    """
    try:
        term = parse_term(term_str, lexicon)
        return T.fmt(T.normalize(term, lambda _name: None))
    except Exception:
        return term_str


def _table(decls: list[Decl], lexicon: dict) -> tuple[dict[str, dict[str, str]], dict[str, Decl]]:
    """(name -> fingerprint per kind, "kind:name" -> declaring decl)."""
    table: dict[str, dict[str, str]] = {k: {} for k in _KINDS}
    where: dict[str, Decl] = {}
    for d in decls:
        if d.kind not in _KINDS or not d.term_str:
            continue
        bucket = table[d.kind]
        if d.name in bucket:
            continue  # first declaration wins, as in check.run
        bucket[d.name] = hashlib.sha256(_canonical(d.term_str, lexicon).encode("utf-8")).hexdigest()[:16]
        where[f"{d.kind}:{d.name}"] = d
    return table, where


def fingerprints(decls: list[Decl], lexicon: dict) -> dict:
    """The baseline payload for the current tree."""
    table, _where = _table(decls, lexicon)
    return {"version": _VERSION, **{kind: dict(sorted(table[kind].items())) for kind in _KINDS}}


def baseline_path(root: Path) -> Path:
    return Path(root).resolve() / BASELINE_REL


def load_baseline(root: Path) -> dict | None:
    """Baseline payload, or None when it is missing / unreadable."""
    try:
        data = json.loads(baseline_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_baseline(root: Path, payload: dict) -> tuple[Path, int]:
    """Write the baseline; returns (path, number of recorded names)."""
    path = baseline_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    recorded: dict[str, dict] = {
        kind: dict(sorted((payload.get(kind) or {}).items())) for kind in _KINDS
    }
    body: dict[str, object] = {"version": _VERSION, **recorded}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, sum(len(names) for names in recorded.values())


def diff(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """``(kind, name, change)`` for every added / removed / changed declaration."""
    changes: list[tuple[str, str, str]] = []
    for kind in _KINDS:
        raw_before = old.get(kind)
        raw_after = new.get(kind)
        before: dict = raw_before if isinstance(raw_before, dict) else {}
        after: dict = raw_after if isinstance(raw_after, dict) else {}
        for name in sorted(set(before) | set(after)):
            if name not in before:
                changes.append((kind, name, "added"))
            elif name not in after:
                changes.append((kind, name, "removed"))
            elif before[name] != after[name]:
                changes.append((kind, name, "changed"))
    return changes


def check_arch(
    decls: list[Decl], cfg: Config, root: Path, allow_arch: bool = False
) -> list[tuple]:
    """E305 tuples for baseline drift.  ``allow_arch`` downgrades them to warnings."""
    if not cfg.forbid_eq_change:
        return []
    current, where = _table(decls, cfg.lexicon)
    baseline = load_baseline(root)
    level = "warning" if allow_arch else "error"
    bpath = str(baseline_path(root))
    note = (
        "confirm the change, then record it with `logiclink . --arch-update`"
        if allow_arch
        else "eq/alias changes are architecture changes: review them, then `--arch-update`"
    )

    if baseline is None:
        return [
            (
                "E305",
                level,
                "architecture baseline missing: no eq/alias fingerprints recorded yet",
                bpath,
                1,
                1,
                "run `logiclink . --arch-update` to record the current eq/alias set",
            )
        ]

    diags: list[tuple] = []
    for kind, name, change in diff(baseline, {k: current[k] for k in _KINDS}):
        src = where.get(f"{kind}:{name}")
        path, line, col = (src.path, src.line, src.col) if src is not None else (bpath, 1, 1)
        before = (baseline.get(kind) or {}).get(name, "-")
        after = current[kind].get(name, "-")
        diags.append(
            (
                "E305",
                level,
                f"architecture change: {kind} `{name}` {change} "
                f"(recorded {before} ≠ current {after})",
                path,
                line,
                col,
                note,
            )
        )
    return diags
