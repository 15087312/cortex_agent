"""``link.toml`` loading (the repo constitution, exactly one at the root)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AtomRule:
    calls: list[str] = field(default_factory=list)
    returns: bool = False
    requires_branch: bool = False
    # 新增约束
    forbidden_calls: list[str] = field(default_factory=list)  # 禁止调用
    requires_import: list[str] = field(default_factory=list)  # 必须导入
    requires_context: list[str] = field(default_factory=list)  # 必须在特定上下文中（async/with）
    min_calls: int = 0  # 最少调用次数
    max_calls: int = -1  # 最多调用次数（-1 表示无限制）


@dataclass
class ArchConstraints:
    forbid_eq_change: bool = False
    forbid_new_atoms: bool = False
    forbid_new_blocks: bool = False
    require_evidence: bool = True
    max_complexity: int = -1  # 最大复杂度（-1 表示无限制）
    require_doc: bool = False
    # Dependency cycles that are knowingly accepted: each entry lists module names.
    allow_cycles: list[frozenset[str]] = field(default_factory=list)
    # 全局约束
    no_direct_db_access: list[str] = field(default_factory=list)  # 这些模块不能直接访问数据库
    must_use_event_bus: list[str] = field(default_factory=list)  # 这些模块必须通过事件总线通信
    require_security_check: list[str] = field(default_factory=list)  # 这些模块必须经过安全检查


@dataclass
class Config:
    root: Path  # directory containing link.toml (or scan root when absent)
    scan_roots: list[str] = field(default_factory=lambda: ["."])
    exclude: list[str] = field(
        default_factory=lambda: [".venv", "**/node_modules/**", "**/test_*.py", "**/*_test.py"]
    )
    extensions: list[str] = field(default_factory=lambda: [".py", ".link"])
    atoms: dict[str, AtomRule] = field(default_factory=dict)
    loop_needs: list[str] = field(default_factory=lambda: ["while", "for"])
    lexicon: dict[str, str] = field(default_factory=dict)
    arch: ArchConstraints = field(default_factory=ArchConstraints)
    tool_wildcard: bool = False
    # v2 L2 dataflow: which atoms produce variables vs consume them.
    dataflow: dict[str, list[str]] = field(
        default_factory=lambda: {
            "producers": ["Retrieve", "Think", "Observe", "Tool", "Parse", "Emit"],
            "consumers": ["Write", "Guard"],
        }
    )
    # 兼容旧配置
    @property
    def forbid_eq_change(self) -> bool:
        return self.arch.forbid_eq_change


BUILTIN_ATOMS: dict[str, AtomRule] = {
    "Think": AtomRule(calls=["think", "llm.invoke", "complete", "chat", "invoke"]),
    "Tool": AtomRule(calls=["call_tool", "tools.invoke", "invoke_tool"]),
    "Observe": AtomRule(calls=["observe", "add_observation"]),
    "Emit": AtomRule(returns=True),
    "Guard": AtomRule(requires_branch=True),
    "Parse": AtomRule(calls=["parse"]),
    "Retrieve": AtomRule(calls=["retrieve"]),
    "Write": AtomRule(calls=["write"]),
    "Human": AtomRule(calls=["human"]),
}


def default_config(root: Path) -> Config:
    return Config(root=root, atoms={k: AtomRule(list(v.calls), v.returns, v.requires_branch) for k, v in BUILTIN_ATOMS.items()})


def load_config(start: Path) -> Config:
    """Find ``link.toml`` at ``start`` (file or dir) or ``start/link.toml``; else defaults."""
    start = start.resolve()
    toml_path = start / "link.toml" if start.is_dir() else start
    if toml_path.is_dir():
        toml_path = toml_path / "link.toml"
    root = toml_path.parent if toml_path.is_file() else (start if start.is_dir() else start.parent)
    cfg = default_config(root)
    if not toml_path.is_file():
        return cfg
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    scan = data.get("scan", {})
    if isinstance(scan.get("roots"), list):
        cfg.scan_roots = [str(x) for x in scan["roots"]]
    if isinstance(scan.get("exclude"), list):
        cfg.exclude = [str(x) for x in scan["exclude"]]
    if isinstance(scan.get("extensions"), list):
        cfg.extensions = [str(x) for x in scan["extensions"]]
    # 加载架构约束
    arch = data.get("arch", {})
    cfg.arch.forbid_eq_change = bool(arch.get("forbid_eq_change", False))
    cfg.arch.forbid_new_atoms = bool(arch.get("forbid_new_atoms", False))
    cfg.arch.forbid_new_blocks = bool(arch.get("forbid_new_blocks", False))
    cfg.arch.require_evidence = bool(arch.get("require_evidence", True))
    cfg.arch.max_complexity = int(arch.get("max_complexity", -1))
    cfg.arch.require_doc = bool(arch.get("require_doc", False))
    allowed = arch.get("allow_cycles", [])
    if isinstance(allowed, list):
        cfg.arch.allow_cycles = [
            frozenset(str(x) for x in entry) for entry in allowed if isinstance(entry, list)
        ]
    # 加载全局约束
    constraints = arch.get("constraints", {})
    if isinstance(constraints.get("no_direct_db_access"), list):
        cfg.arch.no_direct_db_access = [str(x) for x in constraints["no_direct_db_access"]]
    if isinstance(constraints.get("must_use_event_bus"), list):
        cfg.arch.must_use_event_bus = [str(x) for x in constraints["must_use_event_bus"]]
    if isinstance(constraints.get("require_security_check"), list):
        cfg.arch.require_security_check = [str(x) for x in constraints["require_security_check"]]
    cfg.tool_wildcard = bool(data.get("tool_wildcard", False))
    # v2 dataflow roles (optional; defaults above apply when absent).
    df = data.get("dataflow", {})
    if isinstance(df.get("producers"), list):
        cfg.dataflow["producers"] = [str(x) for x in df["producers"]]
    if isinstance(df.get("consumers"), list):
        cfg.dataflow["consumers"] = [str(x) for x in df["consumers"]]
    ops = data.get("ops", {})
    if isinstance(ops.get("Loop", {}).get("needs"), list):
        cfg.loop_needs = [str(x) for x in ops["Loop"]["needs"]]
    lex = data.get("lexicon", {})
    if isinstance(lex, dict):
        cfg.lexicon = {str(k): str(v) for k, v in lex.items()}
    atoms_tbl = data.get("atoms", {})
    if isinstance(atoms_tbl, dict):
        for name, rule in atoms_tbl.items():
            if not isinstance(rule, dict):
                continue
            base = cfg.atoms.get(str(name), AtomRule())
            calls = rule.get("calls", None)
            if calls is not None:
                base = AtomRule(
                    calls=[str(c) for c in calls],
                    returns=bool(rule.get("returns", base.returns)),
                    requires_branch=bool(rule.get("requires_branch", base.requires_branch)),
                    forbidden_calls=[str(c) for c in rule.get("forbidden_calls", base.forbidden_calls)],
                    requires_import=[str(i) for i in rule.get("requires_import", base.requires_import)],
                    requires_context=[str(c) for c in rule.get("requires_context", base.requires_context)],
                    min_calls=int(rule.get("min_calls", base.min_calls)),
                    max_calls=int(rule.get("max_calls", base.max_calls)),
                )
            else:
                base = AtomRule(
                    calls=list(base.calls),
                    returns=bool(rule.get("returns", base.returns)),
                    requires_branch=bool(rule.get("requires_branch", base.requires_branch)),
                    forbidden_calls=[str(c) for c in rule.get("forbidden_calls", base.forbidden_calls)],
                    requires_import=[str(i) for i in rule.get("requires_import", base.requires_import)],
                    requires_context=[str(c) for c in rule.get("requires_context", base.requires_context)],
                    min_calls=int(rule.get("min_calls", base.min_calls)),
                    max_calls=int(rule.get("max_calls", base.max_calls)),
                )
            cfg.atoms[str(name)] = base
    return cfg
