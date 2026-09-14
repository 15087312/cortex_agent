"""Python ``ast`` evidence for atoms inside a block interval (v1).

Only existence is checked (no CFG / ordering proofs):
- calls-based atoms: a ``Call`` whose dotted name matches a configured call
  (exact or last-segment match, case-insensitive fallback).
- ``Loop``: a ``while`` / ``for`` statement (per ``[ops.Loop] needs``).
- ``Guard`` (``requires_branch``): an ``if`` / ``match`` statement.
- ``Emit`` (``returns``): a ``return`` statement.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class FuncSpan:
    name: str
    lineno: int
    end_lineno: int
    is_async: bool = False


@dataclass
class CodeFacts:
    calls: list[tuple[int, str]] = field(default_factory=list)  # (lineno, dotted name)
    loops: list[int] = field(default_factory=list)
    branches: list[int] = field(default_factory=list)
    returns: list[int] = field(default_factory=list)
    funcs: list[FuncSpan] = field(default_factory=list)
    imports: list[tuple[int, str]] = field(default_factory=list)  # (lineno, module name)
    async_contexts: list[int] = field(default_factory=list)  # async 函数/with 语句的行号
    with_contexts: list[int] = field(default_factory=list)  # with 语句的行号


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self):
        self.facts = CodeFacts()

    def visit_Call(self, node: ast.Call):
        name = _dotted(node.func)
        if name:
            self.facts.calls.append((node.lineno, name))
        self.generic_visit(node)

    def visit_While(self, node: ast.While):
        self.facts.loops.append(node.lineno)
        self.generic_visit(node)

    def visit_For(self, node: ast.For):
        self.facts.loops.append(node.lineno)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor):
        self.facts.loops.append(node.lineno)
        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        self.facts.branches.append(node.lineno)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match):
        self.facts.branches.append(node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        self.facts.returns.append(node.lineno)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.facts.funcs.append(
            FuncSpan(node.name, node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno)
        )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.facts.funcs.append(
            FuncSpan(
                node.name,
                node.lineno,
                getattr(node, "end_lineno", node.lineno) or node.lineno,
                True,
            )
        )
        self.facts.async_contexts.append(node.lineno)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.facts.imports.append((node.lineno, alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            # 相对导入（from .x / from ..y import）记录为相对名，避免误配为顶层包
            name = ("." * node.level) + node.module if node.level > 0 else node.module
            self.facts.imports.append((node.lineno, name))
        self.generic_visit(node)

    def visit_With(self, node: ast.With):
        self.facts.with_contexts.append(node.lineno)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith):
        self.facts.with_contexts.append(node.lineno)
        self.facts.async_contexts.append(node.lineno)
        self.generic_visit(node)


def facts_of(text: str) -> CodeFacts:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return CodeFacts()
    v = _Visitor()
    v.visit(tree)
    return v.facts


def in_range(line: int, start: int, end: int) -> bool:
    return start <= line <= end


def calls_in(facts: CodeFacts, start: int, end: int) -> list[str]:
    return [name for ln, name in facts.calls if in_range(ln, start, end)]


def call_matches(actual: str, rule: str, exact: bool = False) -> bool:
    """Check if actual call matches rule.
    
    Args:
        actual: The actual call name (e.g., "llm.invoke")
        rule: The rule to match against (e.g., "llm.invoke" or "invoke")
        exact: If True, require exact match (no suffix matching)
    """
    if actual == rule:
        return True
    if exact:
        return False
    # 后缀匹配（兼容旧行为）
    if actual.split(".")[-1] == rule.split(".")[-1]:
        return True
    a, r = actual.lower(), rule.lower()
    if a == r or a.split(".")[-1] == r.split(".")[-1]:
        return True
    return actual.endswith("." + rule) or rule.endswith("." + actual)


def imports_in(facts: CodeFacts, start: int, end: int) -> list[str]:
    """Get all imports in the given range."""
    return [name for ln, name in facts.imports if in_range(ln, start, end)]


def has_import(facts: CodeFacts, start: int, end: int, module: str) -> bool:
    """Check if a specific module is imported in the given range."""
    for imp in imports_in(facts, start, end):
        if imp == module or imp.startswith(module + "."):
            return True
    return False


def has_async_context(facts: CodeFacts, start: int, end: int) -> bool:
    """Check if there's an async context in the given range."""
    return any(in_range(ln, start, end) for ln in facts.async_contexts)


def has_with_context(facts: CodeFacts, start: int, end: int) -> bool:
    """Check if there's a with context in the given range."""
    return any(in_range(ln, start, end) for ln in facts.with_contexts)


def count_calls(facts: CodeFacts, start: int, end: int, calls: list[str], exact: bool = False) -> int:
    """Count how many times specific calls are made in the given range."""
    count = 0
    for ln, name in facts.calls:
        if not in_range(ln, start, end):
            continue
        if any(call_matches(name, rule, exact) for rule in calls):
            count += 1
    return count


def forbidden_calls_in(facts: CodeFacts, start: int, end: int, forbidden: list[str]) -> list[str]:
    """Find forbidden calls in the given range."""
    found = []
    for ln, name in facts.calls:
        if not in_range(ln, start, end):
            continue
        if any(call_matches(name, rule) for rule in forbidden):
            if name not in found:
                found.append(name)
    return found


def has_evidence_call(facts: CodeFacts, start: int, end: int, calls: list[str]) -> str | None:
    """Return the matched actual call name, or None (loose matching for E002)."""
    return evidence_hit(facts, start, end, "", {"": calls})


def evidence_hit(
    facts: CodeFacts, start: int, end: int, atom: str, rules: dict[str, list[str]]
) -> str | None:
    """Loose per-atom existence, minus calls claimed by another atom.

    An actual call exactly matched (best attribution) by a *different* atom
    cannot serve as evidence here, so e.g. ``tools.invoke`` never masks a
    missing ``Think``. Unclaimed or tied calls still count (spec: existence).
    """
    own = rules.get(atom, [])
    for ln, name in facts.calls:
        if not in_range(ln, start, end):
            continue
        if not any(call_matches(name, rc) for rc in own):
            continue
        claimed = attribute_call(name, rules)
        if claimed is not None and claimed != atom:
            continue
        return name
    return None


def attribute_call(actual: str, rules: dict[str, list[str]]) -> str | None:
    """Attribute one actual call to the best-matching atom (for W010).

    Exact match beats dotted-suffix match; longer rules win ties.
    Returns None when nothing matches or the best score is tied.
    """
    best: str | None = None
    best_key: tuple | None = None
    tied = False
    for aname, calls in rules.items():
        for rc in calls:
            if actual == rc:
                key = (2, len(rc))
            elif actual.endswith("." + rc):
                key = (1, len(rc))
            else:
                continue
            cand = (key, aname)
            if best_key is None or cand[0] > best_key:
                best, best_key, tied = aname, cand[0], False
            elif cand[0] == best_key and aname != best:
                tied = True
    return None if tied else best
