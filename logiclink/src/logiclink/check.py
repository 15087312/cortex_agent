"""Orchestration: symbol table, E001-E012, W010 diagnostics (v2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import ast_terms as T
from .collect import collect
from .config import Config, load_config
from .evidence import calls_in, facts_of, has_evidence_call, in_range
from .parser import Decl, ParseError, parse_term


@dataclass
class Diagnostic:
    code: str  # E001.. / W010..
    level: str  # error | warning
    message: str
    path: str
    line: int = 1
    col: int = 1
    note: str = ""


@dataclass
class Block:
    decl: Decl
    term: T.Term | None  # explicit term (parsed), None when bare
    eff: T.Term | None  # effective term after eq/alias fallback (None on error)
    start: int = 0  # interval start line (1-based, 0 = unknown)
    end: int = 0  # interval end line (inclusive, 0 = unknown)
    attached_fn: str | None = None


def _parse_terms(decls: list[Decl], lexicon: dict) -> tuple[dict[int, T.Term], list[Diagnostic]]:
    terms: dict[int, T.Term] = {}
    diags: list[Diagnostic] = []
    for idx, d in enumerate(decls):
        if d.term_str is None:
            continue
        if d.kind in ("input", "output", "state_init", "state_trans", "use"):
            continue  # v2 port lists / state literals / module refs are not term expressions
        try:
            terms[idx] = parse_term(d.term_str, lexicon)
        except ParseError as e:
            diags.append(
                Diagnostic("E001", "error", f"parse error in {d.kind} `{d.name}`: {e}", d.path, d.line, d.col)
            )
    return terms, diags


def run(
    root: str | Path,
    cfg: Config | None = None,
    strict: bool = False,
    allow_arch: bool = False,
) -> tuple[list[Diagnostic], int]:
    root = Path(root).resolve()
    cfg = cfg or load_config(root)
    result = collect(root, cfg.scan_roots, cfg.exclude, cfg.extensions)
    diags: list[Diagnostic] = []

    for path, line, col, msg in result.failures:
        diags.append(Diagnostic("E001", "error", f"parse error: {msg}", path, line, col))

    terms, term_diags = _parse_terms(result.decls, cfg.lexicon)
    diags.extend(term_diags)

    # ---- symbol table ----
    atoms_declared: set[str] = set()
    aliases: dict[str, tuple[T.Term, Decl]] = {}
    eqs: dict[str, tuple[T.Term, Decl]] = {}
    blocks: list[Block] = []
    blocks_by_name: dict[str, Block] = {}
    exports: dict[str, Decl] = {}
    uses: list[Decl] = []

    def resolve(name: str) -> T.Term | None:
        if name in aliases:
            return aliases[name][0]
        if name in eqs:
            return eqs[name][0]
        return None

    for idx, d in enumerate(result.decls):
        if d.kind == "atom":
            atoms_declared.add(d.name)
        elif d.kind == "alias":
            if idx not in terms:
                continue
            if d.name in aliases and not T.equal(aliases[d.name][0], terms[idx]):
                diags.append(
                    Diagnostic("E003", "error", f"alias `{d.name}` redefined with a different term", d.path, d.line, d.col)
                )
            aliases.setdefault(d.name, (terms[idx], d))
        elif d.kind == "eq":
            if idx not in terms:
                continue
            if d.name in eqs:
                if not T.equal(eqs[d.name][0], terms[idx]):
                    prev = eqs[d.name][1]
                    diags.append(
                        Diagnostic(
                            "E003",
                            "error",
                            f"eq `{d.name}` redefined with a different term "
                            f"(previous at {prev.path}:{prev.line})",
                            d.path,
                            d.line,
                            d.col,
                        )
                    )
            else:
                eqs[d.name] = (terms[idx], d)
        elif d.kind == "export":
            if d.name in exports:
                prev = exports[d.name]
                diags.append(
                    Diagnostic(
                        "E005",
                        "error",
                        f"duplicate export `{d.name}` (previous at {prev.path}:{prev.line})",
                        d.path,
                        d.line,
                        d.col,
                    )
                )
            else:
                exports[d.name] = d
        elif d.kind == "use":
            uses.append(d)
        elif d.kind == "block":
            term = terms.get(idx)
            b = Block(decl=d, term=term, eff=None)
            if d.name in blocks_by_name:
                prev = blocks_by_name[d.name].decl
                diags.append(
                    Diagnostic(
                        "E005",
                        "error",
                        f"duplicate block `{d.name}` (previous at {prev.path}:{prev.line})",
                        d.path,
                        d.line,
                        d.col,
                    )
                )
            else:
                blocks_by_name[d.name] = b
            blocks.append(b)

    for d in uses:
        if d.op == ":=":
            # v2 assembly use: `use name := module.Symbol` resolves its Symbol.
            target = (d.term_str or d.name).split(".")[-1]
        else:
            target = d.name.split(".")[-1]
        known = (
            target in exports
            or target in eqs
            or target in aliases
            or target in blocks_by_name
            or target in atoms_declared
            or target in cfg.atoms
        )
        if not known:
            diags.append(
                Diagnostic("E005", "error", f"use `{d.name}` does not resolve to any known symbol", d.path, d.line, d.col)
            )

    # Effective terms for blocks: explicit -> eq -> alias -> bare atom.
    for b in blocks:
        name = b.decl.name.split(".")[-1] if "." in b.decl.name else b.decl.name
        if b.term is not None:
            b.eff = b.term
        elif b.decl.name in eqs:
            b.eff = eqs[b.decl.name][0]
        elif name in eqs and b.decl.name not in eqs:
            b.eff = eqs[name][0]
        elif b.decl.name in aliases:
            b.eff = aliases[b.decl.name][0]
        else:
            b.eff = T.Atom(b.decl.name, ())

    # E001: unregistered identifiers inside terms.
    known_names: set[str] = (
        set(cfg.atoms)
        | atoms_declared
        | set(aliases)
        | set(eqs)
        | set(blocks_by_name)
        | set(exports)
        | {"id", "Loop"}
        | {d.name.split(".")[-1] for d in uses}
    )
    for idx, d in enumerate(result.decls):
        if idx not in terms:
            continue
        for atom in sorted(T.atoms_of(terms[idx])):
            if atom not in known_names and atom not in cfg.lexicon:
                diags.append(
                    Diagnostic(
                        "E001",
                        "error",
                        f"unregistered identifier `{atom}` in {d.kind} `{d.name}` "
                        f"(register it via `atom`, [atoms.*] or [lexicon] in link.toml)",
                        d.path,
                        d.line,
                        d.col,
                    )
                )

    # E003: block explicit term vs eq for the same name.
    for b in blocks:
        if b.term is None:
            continue
        for key in (b.decl.name, b.decl.name.split(".")[-1]):
            if key in eqs and not T.equal(b.term, eqs[key][0], resolve, cfg.tool_wildcard):
                diags.append(
                    Diagnostic(
                        "E003",
                        "error",
                        f"block `{b.decl.name}` term != eq `{key}` "
                        f"(block: {T.fmt(T.normalize(b.term, resolve))} vs "
                        f"eq: {T.fmt(T.normalize(eqs[key][0], resolve))})",
                        b.decl.path,
                        b.decl.line,
                        b.decl.col,
                    )
                )
                break

    # ---- intervals (Python files) ----
    py_texts: dict[str, list[str]] = {}
    for path in result.files:
        if path.endswith(".py"):
            try:
                py_texts[path] = Path(path).read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
    facts_cache: dict[str, object] = {}

    def facts_for(path: str):
        if path not in facts_cache:
            facts_cache[path] = facts_of("\n".join(py_texts.get(path, [])))
        return facts_cache[path]

    _assign_intervals(blocks, py_texts)

    # E006: hook resolution.
    for b in blocks:
        d = b.decl
        if d.hook_file is None:
            continue
        target = (root / d.hook_file).resolve()
        if not target.is_file():
            diags.append(
                Diagnostic("E006", "error", f"block `{d.name}` hook file not found: {d.hook_file}", d.path, d.line, d.col)
            )
            continue
        if d.hook_fn is not None:
            facts = facts_of(target.read_text(encoding="utf-8"))
            if all(f.name != d.hook_fn for f in facts.funcs):
                diags.append(
                    Diagnostic(
                        "E006",
                        "error",
                        f"block `{d.name}` hook fn `{d.hook_fn}` not found in {d.hook_file}",
                        d.path,
                        d.line,
                        d.col,
                    )
                )
            else:
                span = next(f for f in facts.funcs if f.name == d.hook_fn)
                if b.start == 0:
                    b.start, b.end = span.lineno, span.end_lineno
                    b.attached_fn = span.name

    # E004: parent term vs children combination (comment tree only).
    eq_names = set(eqs) | {n.split(".")[-1] for n in eqs}
    diags.extend(_check_composition(blocks, resolve, cfg, eq_names))

    # E002 + W010: evidence.
    diags.extend(_check_evidence(blocks, resolve, cfg, facts_for, py_texts, strict))

    # E007-E012: atom constraints and architecture constraints.
    diags.extend(_check_atom_constraints(blocks, resolve, cfg, facts_for))
    diags.extend(_check_arch_constraints(blocks, resolve, cfg, facts_for))

    # v2: L1 assembly / L2 dataflow / L3 call graph / L4 state machine.
    from . import v2_datav, v2_flow, v2_graph, v2_state

    for (code, level, message, path, line, col, note) in v2_flow.check_flow(result.decls):
        diags.append(Diagnostic(code, level, message, path, line, col, note))
    for (code, level, message, path, line, col, note) in v2_datav.check_datav(result.decls, cfg):
        diags.append(Diagnostic(code, level, message, path, line, col, note))
    for (code, level, message, path, line, col, note) in v2_graph.check_graph(
        result.decls, cfg.arch.allow_cycles
    ):
        diags.append(Diagnostic(code, level, message, path, line, col, note))
    for (code, level, message, path, line, col, note) in v2_state.check_state(result.decls):
        diags.append(Diagnostic(code, level, message, path, line, col, note))

    # Architecture gate: eq/alias drift against the recorded baseline (E305).
    from . import arch

    for (code, level, message, path, line, col, note) in arch.check_arch(
        result.decls, cfg, root, allow_arch=allow_arch
    ):
        diags.append(Diagnostic(code, level, message, path, line, col, note))

    errors = [d for d in diags if d.level == "error"]
    diags.sort(key=lambda d: (d.path, d.line, d.code))
    return diags, (1 if errors else 0)


# ---------------------------------------------------------------- intervals

def _next_code_line(lines: list[str], after: int) -> tuple[int, str] | None:
    """Next non-blank, non-comment line after 1-based line ``after``."""
    for i in range(after, len(lines)):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            continue
        return i + 1, s
    return None


def _assign_intervals(blocks: list[Block], py_texts: dict[str, list[str]]) -> None:
    by_file: dict[str, list[Block]] = {}
    for b in blocks:
        if b.decl.path in py_texts:
            by_file.setdefault(b.decl.path, []).append(b)
    for path, blist in by_file.items():
        lines = py_texts[path]
        facts = facts_of("\n".join(lines))
        blist.sort(key=lambda b: b.decl.line)
        file_lines = blist
        for i, b in enumerate(file_lines):
            nxt = _next_code_line(lines, b.decl.line)
            attached = None
            if nxt is not None:
                _, code = nxt
                if (
                    code.startswith("def ")
                    or code.startswith("async def ")
                    or code.startswith("@")
                    or code.startswith("class ")
                ):
                    following = [f for f in facts.funcs if nxt[0] <= f.lineno <= nxt[0] + 5]
                    if following:
                        attached = min(following, key=lambda f: f.lineno)
            if attached is not None:
                b.start, b.end = attached.lineno, attached.end_lineno
                b.attached_fn = attached.name
                continue
            # Inline block: enclosing function or module scope.
            enclosing = [f for f in facts.funcs if f.lineno < b.decl.line <= f.end_lineno]
            outer_end = min([f.end_lineno for f in enclosing]) if enclosing else len(lines)
            if i + 1 < len(file_lines) and file_lines[i + 1].decl.line <= outer_end:
                end = file_lines[i + 1].decl.line - 1
            else:
                end = outer_end
            b.start, b.end = b.decl.line, max(end, b.decl.line)
            if enclosing:
                b.attached_fn = max(enclosing, key=lambda f: f.lineno).name


# ---------------------------------------------------------------- E004

def _combo(children: list[Block]) -> T.Term:
    terms = [c.eff for c in children if c.eff is not None]
    if not terms:
        return T.Id()
    out = terms[0]
    for t in terms[1:]:
        out = T.Seq(out, t)
    return out


def _check_composition(blocks: list[Block], resolve, cfg: Config, eq_names: set[str]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    # A block participates as a parent when it declares a term, explicitly or via eq.
    participants = [
        b
        for b in blocks
        if (b.term is not None or b.decl.name in eq_names) and b.start > 0
    ]
    for parent in participants:
        desc = [
            c
            for c in blocks
            if c is not parent and c.start > 0 and c.decl.path == parent.decl.path
            and parent.start <= c.decl.line <= parent.end
            and c.decl.line != parent.decl.line
        ]
        if not desc:
            continue
        # Nearest participating ancestor wins: drop descendants shadowed by a
        # participating block strictly inside the parent.
        inner_parents = [
            q
            for q in participants
            if q is not parent
            and q.decl.path == parent.decl.path
            and parent.start <= q.decl.line <= parent.end
        ]
        children: list[Block] = []
        for c in sorted(desc, key=lambda b: b.decl.line):
            shadow = False
            for q in inner_parents:
                if q.start <= c.decl.line <= q.end and c is not q:
                    shadow = True
                    break
            if not shadow:
                children.append(c)
        if not children or all(c is parent for c in children):
            continue
        parent_n = T.normalize(parent.eff, resolve)
        combo_n = T.normalize(_combo(children), resolve)
        if T.equal(parent.eff, _combo(children), resolve, cfg.tool_wildcard):
            continue
        p_atoms = T.atoms_of(parent_n)
        c_atoms = T.atoms_of(combo_n)
        missing = sorted(p_atoms - c_atoms)
        extra = sorted(c_atoms - p_atoms)
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing: {', '.join(missing)}")
            if extra:
                parts.append(f"extra: {', '.join(extra)}")
            diags.append(
                Diagnostic(
                    "E004",
                    "error",
                    f"block `{parent.decl.name}` term != children combination ({'; '.join(parts)})",
                    parent.decl.path,
                    parent.decl.line,
                    parent.decl.col,
                    note=f"parent: {T.fmt(parent_n)} vs children: {T.fmt(combo_n)}",
                )
            )
    return diags


# ---------------------------------------------------------------- E002 / W010

def _check_evidence(blocks, resolve, cfg: Config, facts_for, py_texts, strict: bool) -> list[Diagnostic]:
    from .evidence import CodeFacts  # local to avoid circulars in type hints

    diags: list[Diagnostic] = []
    for b in blocks:
        if b.eff is None or b.start == 0:
            continue
        d = b.decl
        facts: CodeFacts = facts_for(d.path)
        norm = T.normalize(b.eff, resolve)
        term_atoms = T.atoms_of(norm)
        # Loop evidence: every Loop node needs a loop statement in the interval.
        for _ in range(T.count_loops(norm)):
            if not [ln for ln in facts.loops if in_range(ln, b.start, b.end)]:
                diags.append(
                    Diagnostic(
                        "E002",
                        "error",
                        f"missing evidence for Loop in block `{d.name}` (no while/for in range)",
                        d.path,
                        d.line,
                        d.col,
                        note=f"expected one of: {', '.join(cfg.loop_needs)}",
                    )
                )
                break
        evidenced: set[str] = set()
        for atom in sorted(term_atoms):
            if atom == "id":
                continue
            rule = cfg.atoms.get(atom)
            if rule is None:
                diags.append(
                    Diagnostic(
                        "E002",
                        "error",
                        f"missing evidence for `{atom}` in block `{d.name}`: "
                        f"no evidence rule (declare [atoms.{atom}] in link.toml)",
                        d.path,
                        d.line,
                        d.col,
                    )
                )
                continue
            # v1 existence semantics: ANY satisfied condition counts as evidence
            # (e.g. Emit may be witnessed by a notify-call OR a return).
            hits: list[bool] = []
            notes: list[str] = []
            if rule.calls:
                from .evidence import evidence_hit

                call_rules = {n: r.calls for n, r in cfg.atoms.items() if r.calls}
                hit = evidence_hit(facts, b.start, b.end, atom, call_rules)
                hits.append(hit is not None)
                if hit is None:
                    notes.append(f"expected calls: {', '.join(rule.calls)}")
                else:
                    evidenced.add(atom)
            if rule.requires_branch:
                branch_hit = bool([ln for ln in facts.branches if in_range(ln, b.start, b.end)])
                hits.append(branch_hit)
                if not branch_hit:
                    notes.append("expected a branch (if/match)")
                else:
                    evidenced.add(atom)
            if rule.returns:
                ret_hit = bool([ln for ln in facts.returns if in_range(ln, b.start, b.end)])
                hits.append(ret_hit)
                if not ret_hit:
                    notes.append("expected a return statement")
                else:
                    evidenced.add(atom)
            ok = any(hits) if hits else True
            if not ok:
                diags.append(
                    Diagnostic(
                        "E002",
                        "error",
                        f"missing evidence for `{atom}` in block `{d.name}`",
                        d.path,
                        d.line,
                        d.col,
                        note="; ".join(notes),
                    )
                )
        # W010: registered atom calls present in the interval but absent from the term.
        # Each actual call is attributed to exactly one atom (best match), so
        # e.g. tools.invoke counts for Tool, not for Think's loose "invoke".
        from .evidence import attribute_call

        call_rules = {n: r.calls for n, r in cfg.atoms.items() if r.calls}
        present: set[str] = set()
        for name in calls_in(facts, b.start, b.end):
            hit = attribute_call(name, call_rules)
            if hit is not None:
                present.add(hit)
        for surplus in sorted(present - term_atoms - evidenced):
            # Only warn when the surplus atom is a real call-based atom.
            if surplus in term_atoms:
                continue
            diags.append(
                Diagnostic(
                    "W010",
                    "error" if strict else "warning",
                    f"block `{d.name}` has evidence for `{surplus}` but its term does not mention it",
                    d.path,
                    d.line,
                    d.col,
                )
            )
    return diags


def _match(actual: str, rule: str) -> bool:
    from .evidence import call_matches

    return call_matches(actual, rule)


# ---------------------------------------------------------------- E007-E012

def _check_atom_constraints(blocks, resolve, cfg: Config, facts_for) -> list[Diagnostic]:
    """检查原子规则的新增约束：forbidden_calls, requires_import, requires_context, min/max_calls"""
    from .evidence import (
        forbidden_calls_in, has_import, has_async_context, has_with_context,
        count_calls, calls_in,
    )
    
    diags: list[Diagnostic] = []
    for b in blocks:
        if b.eff is None or b.start == 0:
            continue
        d = b.decl
        facts = facts_for(d.path)
        norm = T.normalize(b.eff, resolve)
        term_atoms = T.atoms_of(norm)
        
        for atom in sorted(term_atoms):
            if atom == "id":
                continue
            rule = cfg.atoms.get(atom)
            if rule is None:
                continue
            
            # E009: 禁止调用检查
            if rule.forbidden_calls:
                forbidden = forbidden_calls_in(facts, b.start, b.end, rule.forbidden_calls)
                for call in forbidden:
                    diags.append(
                        Diagnostic(
                            "E009",
                            "error",
                            f"block `{d.name}` calls forbidden function `{call}` in atom `{atom}`",
                            d.path,
                            d.line,
                            d.col,
                            note=f"forbidden calls: {', '.join(rule.forbidden_calls)}",
                        )
                    )
            
            # E007: 导入检查
            if rule.requires_import:
                for imp in rule.requires_import:
                    if not has_import(facts, b.start, b.end, imp):
                        diags.append(
                            Diagnostic(
                                "E007",
                                "error",
                                f"block `{d.name}` missing required import `{imp}` for atom `{atom}`",
                                d.path,
                                d.line,
                                d.col,
                                note=f"required imports: {', '.join(rule.requires_import)}",
                            )
                        )
            
            # E008: 上下文检查
            if rule.requires_context:
                for ctx in rule.requires_context:
                    if ctx == "async" and not has_async_context(facts, b.start, b.end):
                        diags.append(
                            Diagnostic(
                                "E008",
                                "error",
                                f"block `{d.name}` missing async context for atom `{atom}`",
                                d.path,
                                d.line,
                                d.col,
                                note="required context: async",
                            )
                        )
                    elif ctx == "with" and not has_with_context(facts, b.start, b.end):
                        diags.append(
                            Diagnostic(
                                "E008",
                                "error",
                                f"block `{d.name}` missing with context for atom `{atom}`",
                                d.path,
                                d.line,
                                d.col,
                                note="required context: with",
                            )
                        )
            
            # 调用次数检查
            if rule.calls:
                call_count = count_calls(facts, b.start, b.end, rule.calls)
                if rule.min_calls > 0 and call_count < rule.min_calls:
                    diags.append(
                        Diagnostic(
                            "E002",
                            "error",
                            f"block `{d.name}` has {call_count} calls for atom `{atom}`, minimum is {rule.min_calls}",
                            d.path,
                            d.line,
                            d.col,
                            note=f"expected at least {rule.min_calls} calls: {', '.join(rule.calls)}",
                        )
                    )
                if rule.max_calls >= 0 and call_count > rule.max_calls:
                    diags.append(
                        Diagnostic(
                            "E002",
                            "error",
                            f"block `{d.name}` has {call_count} calls for atom `{atom}`, maximum is {rule.max_calls}",
                            d.path,
                            d.line,
                            d.col,
                            note=f"expected at most {rule.max_calls} calls: {', '.join(rule.calls)}",
                        )
                    )
    
    return diags


def _check_arch_constraints(blocks, resolve, cfg: Config, facts_for) -> list[Diagnostic]:
    """检查架构约束"""
    diags: list[Diagnostic] = []
    
    # E010: 复杂度检查
    if cfg.arch.max_complexity > 0:
        for b in blocks:
            if b.eff is None or b.start == 0:
                continue
            d = b.decl
            facts = facts_for(d.path)
            # 简单复杂度计算：调用数 + 分支数 + 循环数
            complexity = len(calls_in(facts, b.start, b.end))
            complexity += len([ln for ln in facts.branches if in_range(ln, b.start, b.end)])
            complexity += len([ln for ln in facts.loops if in_range(ln, b.start, b.end)])
            if complexity > cfg.arch.max_complexity:
                diags.append(
                    Diagnostic(
                        "E010",
                        "error",
                        f"block `{d.name}` complexity {complexity} exceeds maximum {cfg.arch.max_complexity}",
                        d.path,
                        d.line,
                        d.col,
                    )
                )
    
    return diags
