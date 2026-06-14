"""
命令执行工具 — shell 命令执行、Python 沙箱执行、进程管理

- exec_command: 通用 shell 执行（高权限）— 带危险命令检测 + 执行前快照
- run_command: 白名单管控的命令执行（仅允许安全命令）
- run_script: 任意语言脚本执行（真机模式，无沙箱）
- kill_process: 杀死进程
- rollback_snapshot: 回滚到命令执行前的快照
- list_command_snapshots: 列出可用快照
"""
import subprocess
import os
import signal
import time
import sys
import tempfile
import shlex
import json
import shutil
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("exec")

MAX_OUTPUT_LENGTH = 50000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600

# 快照存储目录
_SNAPSHOT_DIR = Path("data/command_snapshots")
_MAX_SNAPSHOTS = 50  # 最多保留快照数
_MAX_BACKUP_FILE_SIZE = 10 * 1024 * 1024  # 单文件备份上限 10MB

# 命令白名单 — run_command 只允许这些命令（安全命令，不含破坏性操作）
COMMAND_WHITELIST = {
    "python", "python3", "pip", "pip3",
    "git", "pytest", "ruff", "black", "mypy", "flake8",
    "node", "npm", "npx", "yarn",
    "cat", "head", "tail", "wc", "sort", "uniq",
    "ls", "find", "grep", "rg", "ag", "ack",
    "echo", "printf",
    "which", "where",
    "mkdir", "cp", "mv",
    "tar", "gzip", "gunzip", "zip", "unzip",
    "make", "cmake",
    "env", "pwd", "date", "whoami",
    "open",  # macOS 打开应用/文件
}

# exec_command 危险命令模式 — 检测到时在响应中添加警告
_DANGEROUS_PATTERNS = [
    "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf .",
    "mkfs.", "dd if=", "> /dev/sd",
    ":(){ :|:& };:",  # fork bomb
    "chmod 777 /", "chmod -R 777 /",
    "curl.*|.*sh", "wget.*|.*sh",  # pipe to shell
    "nc -l", "ncat -l",  # reverse shell
    "/etc/shadow", "/etc/passwd",
]

# 极端危险模式 — 硬阻断，绝不执行
import re as _re
_EXTREME_DANGER_PATTERNS_RAW = [
    r'rm\s+-[rRf]*[rR][rRf]*f\s+/\s*$',       # rm -rf / (exactly root)
    r'rm\s+-[rRf]*[rR][rRf]*f\s+/\*',          # rm -rf /*
    r'rm\s+-[rRf]*[rR][rRf]*f\s+~',            # rm -rf ~
    r'rm\s+-[rRf]*[rR][rRf]*f\s+\.\s*$',       # rm -rf .
    r':\(\)\{.*\|.*\&\}.*:',                    # fork bomb
    r'\bmkfs\.',                                 # mkfs
    r'\bdd\s+if=',                               # dd
    r'>\s*/dev/sd',                              # overwrite disk
    r'nc\s+-l',                                  # reverse shell listener
    r'ncat\s+-l',                                # reverse shell listener
]
_EXTREME_DANGER_RE = [_re.compile(p, _re.IGNORECASE) for p in _EXTREME_DANGER_PATTERNS_RAW]


def _detect_dangerous_command(command: str) -> List[str]:
    """检测命令中的危险模式，返回匹配的警告列表"""
    warnings = []
    cmd_lower = command.lower().strip()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.lower() in cmd_lower:
            warnings.append(f"检测到危险模式: '{pattern}'")
    # 检测链式命令（; && || |）中的危险操作
    if any(op in command for op in [";", "&&", "||", "|"]):
        parts = []
        for sep in [";", "&&", "||", "|"]:
            parts.extend(command.split(sep))
        for part in parts:
            part = part.strip()
            if part:
                base = os.path.basename(part.split()[0]) if part.split() else ""
                if base in ("rm", "mkfs", "dd", "nc", "ncat"):
                    warnings.append(f"链式命令中包含高危命令: '{base}'")
    return warnings


def _check_extreme_danger(command: str) -> Optional[str]:
    """检查极端危险命令，返回拒绝原因或 None"""
    for pattern in _EXTREME_DANGER_RE:
        if pattern.search(command):
            return f"极端危险命令被拦截: 匹配模式 '{pattern.pattern}'"
    return None


# ---------------------------------------------------------------------------
# 命令执行前快照 — 记录 git 状态 + 备份受影响文件，以便回滚
# ---------------------------------------------------------------------------

def _run_git_safe(args: List[str], cwd: Optional[str] = None) -> Tuple[bool, str]:
    """安全运行 git 命令，返回 (success, stdout)"""
    try:
        r = subprocess.run(
            ["git"] + args, capture_output=True, text=True,
            timeout=10, cwd=cwd,
        )
        return r.returncode == 0, (r.stdout or "").strip()
    except Exception:
        return False, ""


def _get_git_snapshot(workdir: Optional[str]) -> Dict[str, Any]:
    """获取当前 git 仓库状态快照"""
    cwd = workdir or os.getcwd()
    snapshot: Dict[str, Any] = {"is_git_repo": False}

    ok, head = _run_git_safe(["rev-parse", "HEAD"], cwd)
    if not ok:
        return snapshot

    snapshot["is_git_repo"] = True
    snapshot["head"] = head

    _, branch = _run_git_safe(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    snapshot["branch"] = branch

    _, dirty = _run_git_safe(["diff", "--name-only"], cwd)
    snapshot["dirty_files"] = dirty.split("\n") if dirty else []

    _, staged = _run_git_safe(["diff", "--cached", "--name-only"], cwd)
    snapshot["staged_files"] = staged.split("\n") if staged else []

    return snapshot


def _parse_target_files(command: str, workdir: Optional[str]) -> List[Path]:
    """从命令中解析出可能被影响的文件/目录路径（已存在的）"""
    targets: List[Path] = []
    cwd = Path(workdir) if workdir else Path.cwd()

    try:
        tokens = shlex.split(command)
    except ValueError:
        # shlex 解析失败时 fallback 到简单 split
        tokens = command.split()

    # rm / mv / chmod / chown / truncate / ln 等命令的非 flag 参数视为目标
    destructive_cmds = {"rm", "mv", "chmod", "chown", "truncate", "ln", "install"}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        base = os.path.basename(token)

        if base in destructive_cmds:
            # 跳过 flag（-rf, -r, -f, --recursive 等）
            j = i + 1
            while j < len(tokens) and tokens[j].startswith("-"):
                j += 1
            # 后续非 flag 参数是目标路径
            while j < len(tokens) and not tokens[j].startswith("-"):
                candidate = tokens[j]
                if candidate in (".", "..", "/", "~"):
                    j += 1
                    continue
                path = Path(candidate).expanduser()
                if not path.is_absolute():
                    path = cwd / path
                try:
                    # resolve 但不跟随 symlink（只规范化路径）
                    path = path.resolve()
                    if path.exists() and path not in targets:
                        targets.append(path)
                except (OSError, ValueError):
                    pass
                j += 1
        i += 1

    # 重定向覆盖: > /path/to/file
    for token in tokens:
        if token.startswith(">") and len(token) > 1:
            path = Path(token[1:]).expanduser()
            if not path.is_absolute():
                path = cwd / path
            try:
                path = path.resolve()
                if path.exists() and path not in targets:
                    targets.append(path)
            except (OSError, ValueError):
                pass

    return targets


def _create_snapshot(command: str, workdir: Optional[str]) -> Optional[Dict[str, Any]]:
    """在执行危险命令前创建快照（git 状态 + 文件备份）。

    Returns:
        快照元数据 dict，失败时返回 None
    """
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        cmd_hash = hashlib.md5(command.encode()).hexdigest()[:8]
        snapshot_id = f"{ts}_{cmd_hash}"
        snapshot_path = _SNAPSHOT_DIR / snapshot_id
        snapshot_path.mkdir(parents=True, exist_ok=True)

        # 1. Git 状态
        git_info = _get_git_snapshot(workdir)

        # 2. 解析受影响文件并备份
        targets = _parse_target_files(command, workdir)
        backed_up: List[Dict[str, str]] = []
        skipped: List[str] = []

        for target in targets:
            try:
                if target.is_file():
                    if target.stat().st_size > _MAX_BACKUP_FILE_SIZE:
                        skipped.append(f"{target} (文件过大: {target.stat().st_size} bytes)")
                        continue
                    rel = target.name
                    # 用路径 hash 避免同名文件冲突
                    path_hash = hashlib.md5(str(target).encode()).hexdigest()[:6]
                    backup_name = f"{rel}_{path_hash}"
                    backup_dest = snapshot_path / "files" / backup_name
                    backup_dest.parent.mkdir(exist_ok=True)
                    shutil.copy2(str(target), str(backup_dest))
                    backed_up.append({
                        "original": str(target),
                        "backup": str(backup_dest),
                        "size": target.stat().st_size,
                    })
                elif target.is_dir():
                    # 目录只记录存在性，不递归备份（避免巨大开销）
                    entry_count = sum(1 for _ in target.iterdir()) if target.exists() else 0
                    backed_up.append({
                        "original": str(target),
                        "backup": "",
                        "type": "directory",
                        "entry_count": entry_count,
                    })
            except (OSError, PermissionError) as e:
                skipped.append(f"{target} ({e})")

        # 3. 写入快照元数据
        meta = {
            "snapshot_id": snapshot_id,
            "timestamp": ts,
            "command": command,
            "workdir": workdir or os.getcwd(),
            "git": git_info,
            "backed_up_files": backed_up,
            "skipped": skipped,
        }
        meta_path = snapshot_path / "snapshot.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(
            f"[安全快照] {snapshot_id}: git={'✓' if git_info.get('is_git_repo') else '✗'}, "
            f"备份 {len(backed_up)} 个目标, 跳过 {len(skipped)} 个"
        )
        return meta

    except Exception as e:
        logger.error(f"[安全快照] 创建失败 (非致命): {e}")
        return None


def _prune_old_snapshots():
    """清理超出上限的旧快照（FIFO）"""
    try:
        if not _SNAPSHOT_DIR.exists():
            return
        dirs = sorted(_SNAPSHOT_DIR.iterdir(), key=lambda d: d.name)
        if len(dirs) > _MAX_SNAPSHOTS:
            for old in dirs[: len(dirs) - _MAX_SNAPSHOTS]:
                shutil.rmtree(old, ignore_errors=True)
                logger.debug(f"[安全快照] 清理旧快照: {old.name}")
    except Exception as e:
        logger.debug(f"[安全快照] 清理失败 (非致命): {e}")


@ToolRegistry.register(
    "exec_command",
    description=(
        "【高权限】在系统终端中执行任意 shell 命令。支持超时和工作目录。"
        "可用于运行脚本、编译代码、执行测试等。注意：此工具无命令限制，但会检测危险模式。"
    ),
    params={
        "command": "要执行的 shell 命令",
        "timeout": "可选，超时秒数（默认60，最大600）",
        "workdir": "可选，工作目录路径",
    },
    risk_level="HIGH",
    category="admin",
    tags=["mutation", "learning"],
    core=True,
)
def exec_command(command: str, timeout: Optional[int] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    """执行任意 shell 命令（高权限）— 带危险模式检测 + 执行前快照

    安全防护层级（调用链从外到内）：
    1. ToolSecurityGate.check() — 外层安全门控，HIGH 风险工具需 LLM 审查或用户确认
       （LLM 不可用时直接拒绝，见 tool_security_gate.py _check_high_risk）
    2. _visible_tool_whitelist() — expert tier 无法看到此工具（HIGH 风险自动过滤）
    3. ModelPermissions.can_use_tool_category("admin") — 非 large 角色权限拒绝
    4. _check_extreme_danger() — 本函数内部，极端危险模式硬阻断（rm -rf /、fork bomb 等）
    5. _detect_dangerous_command() + _create_snapshot() — 危险模式警告 + 执行前快照兜底
    """
    if not command or not command.strip():
        return {"error": "命令不能为空", "exit_code": -1}

    # 极端危险命令 — 硬阻断，绝不执行
    extreme_block = _check_extreme_danger(command)
    if extreme_block:
        logger.error(f"[安全拦截] {extreme_block}, command={command[:100]}")
        return {"error": extreme_block, "exit_code": -1, "blocked": True}

    try:
        timeout = int(timeout) if timeout is not None else DEFAULT_TIMEOUT
    except (ValueError, TypeError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    # 危险命令检测
    danger_warnings = _detect_dangerous_command(command)

    # 危险命令 → 执行前创建快照（git 状态 + 文件备份）
    snapshot_meta = None
    if danger_warnings:
        snapshot_meta = _create_snapshot(command, workdir)
        # 异步清理旧快照（不阻塞执行）
        try:
            import threading
            threading.Thread(target=_prune_old_snapshots, daemon=True).start()
        except Exception:
            pass

    try:
        start = time.time()
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=workdir,
        )
        elapsed = time.time() - start
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + f"\n... (截断, {len(stdout)} 字符)"
        if len(stderr) > MAX_OUTPUT_LENGTH:
            stderr = stderr[:MAX_OUTPUT_LENGTH] + f"\n... (截断, {len(stderr)} 字符)"
        resp = {"stdout": stdout, "stderr": stderr, "exit_code": result.returncode, "elapsed_seconds": round(elapsed, 2)}
        if danger_warnings:
            resp["security_warnings"] = danger_warnings
            logger.warning(f"exec_command 危险命令检测: {danger_warnings}, command={command[:100]}")
        if snapshot_meta:
            resp["snapshot"] = {
                "snapshot_id": snapshot_meta["snapshot_id"],
                "git_head": snapshot_meta["git"].get("head", ""),
                "git_branch": snapshot_meta["git"].get("branch", ""),
                "backed_up_count": len(snapshot_meta.get("backed_up_files", [])),
                "rollback_hint": f"如需回滚，调用 rollback_snapshot(snapshot_id='{snapshot_meta['snapshot_id']}')",
            }
        return resp
    except subprocess.TimeoutExpired:
        return {"error": f"超时（{timeout}秒）", "exit_code": -1}
    except Exception as e:
        return {"error": f"执行失败: {e}", "exit_code": -1}


@ToolRegistry.register(
    "run_command",
    description=(
        "【白名单管控】执行受白名单限制的 shell 命令。"
        "只允许执行 python、pip、git、pytest、ruff、black 等安全命令。"
        "需要执行其他命令请使用 exec_command。"
    ),
    params={
        "command": "要执行的命令（必须是白名单内的命令）",
        "timeout": "可选，超时秒数（默认60）",
        "workdir": "可选，工作目录路径",
    },
    risk_level="MEDIUM",
    category="admin",
    tags=["mutation"],
)
def run_command(command: str, timeout: Optional[int] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    """执行命令 — 白名单模式：仅允许 COMMAND_WHITELIST 中的命令"""
    if not command or not command.strip():
        return {"error": "命令不能为空", "exit_code": -1}

    # 白名单检查：提取命令名（第一个 token）
    try:
        cmd_parts = shlex.split(command)
    except ValueError:
        return {"error": f"命令解析失败: {command}", "exit_code": -1}

    cmd_name = os.path.basename(cmd_parts[0]) if cmd_parts else ""
    if cmd_name not in COMMAND_WHITELIST:
        return {
            "error": f"命令「{cmd_name}」不在白名单中。允许的命令: {', '.join(sorted(COMMAND_WHITELIST))}。如需执行其他命令请使用 exec_command。",
            "exit_code": -1,
        }

    return exec_command(command, timeout, workdir)


@ToolRegistry.register(
    "run_script",
    description=(
        "执行任意语言脚本（真机模式）。支持 python/bash/node/ruby/perl/sh 等。"
        "脚本在临时目录中直接执行，无沙箱限制。"
        "返回 stdout/stderr/exit_code。"
    ),
    params={
        "code": "要执行的脚本代码",
        "language": "脚本语言: python(默认), bash, node, ruby, perl, sh",
        "timeout": "可选，超时秒数（默认30）",
    },
    risk_level="HIGH",
    category="admin",
    tags=["mutation"],
    core=True,
)
def run_script(code: str, language: str = "python", timeout: Optional[int] = 30) -> Dict[str, Any]:
    """执行任意语言脚本 — 真机模式，无沙箱"""
    if not code or not code.strip():
        return {"error": "脚本代码不能为空"}

    # 极端危险命令 — 硬阻断，防御性检查（security_gate 层也有检查）
    extreme_block = _check_extreme_danger(code)
    if extreme_block:
        logger.error(f"[安全拦截] run_script: {extreme_block}")
        return {"error": f"安全拦截: {extreme_block}", "exit_code": -1}

    try:
        timeout = int(timeout) if timeout is not None else 30
    except (ValueError, TypeError):
        timeout = 30
    timeout = max(5, min(timeout, 300))

    # 语言 → 解释器映射（跨平台）
    _is_win = sys.platform == "win32"
    interpreters = {
        "python": [sys.executable],
        "python3": [sys.executable],
        "bash": ["bash"] if _is_win else ["/bin/bash"],
        "sh": ["cmd", "/c"] if _is_win else ["/bin/sh"],
        "node": ["node"],
        "ruby": ["ruby"],
        "perl": ["perl"],
    }
    lang = language.lower().strip()
    interp = interpreters.get(lang)
    if not interp:
        return {"error": f"不支持的语言: {language}。支持: {', '.join(interpreters.keys())}"}

    # 脚本文件扩展名
    ext_map = {
        "python": ".py", "python3": ".py", "bash": ".sh",
        "sh": ".sh", "node": ".js", "ruby": ".rb", "perl": ".pl",
    }
    ext = ext_map.get(lang, ".txt")

    # 写入临时脚本文件并直接执行
    script_dir = tempfile.mkdtemp(prefix="runscript_")
    script_path = os.path.join(script_dir, f"_script{ext}")
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
        if sys.platform != "win32":
            os.chmod(script_path, 0o755)

        start = time.time()
        cmd = interp + [script_path]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout + 2, cwd=script_dir,
        )
        elapsed = time.time() - start

        return {
            "stdout": (result.stdout or "")[:MAX_OUTPUT_LENGTH],
            "stderr": (result.stderr or "")[:MAX_OUTPUT_LENGTH],
            "exit_code": result.returncode,
            "elapsed_seconds": round(elapsed, 2),
        }
    except subprocess.TimeoutExpired:
        return {"error": f"超时（{timeout}秒）", "exit_code": -1}
    except Exception as e:
        return {"error": f"执行失败: {e}", "exit_code": -1}
    finally:
        try:
            shutil.rmtree(script_dir, ignore_errors=True)
        except Exception:
            pass








@ToolRegistry.register(
    "kill_process",
    description=(
        "杀死指定 PID 的进程。只能杀死当前用户启动的进程。"
        "需要主管审批。返回杀死结果。"
    ),
    params={
        "pid": "要杀死的进程 ID",
        "force": "可选，是否强制杀死（SIGKILL），默认 SIGTERM",
    },
    risk_level="HIGH",
    category="admin",
    tags=["mutation"],
)
def kill_process(pid: int, force: bool = False) -> Dict[str, Any]:
    """杀死进程（跨平台）"""
    try:
        pid = int(pid)
        if sys.platform == "win32":
            # Windows: taskkill /F 强制，taskkill 正常
            cmd = ["taskkill", "/F", "/PID", str(pid)] if force else ["taskkill", "/PID", str(pid)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return {
                "success": result.returncode == 0,
                "pid": pid,
                "signal": "taskkill/F" if force else "taskkill",
                "output": result.stdout.strip() or result.stderr.strip(),
            }
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, sig)
            return {
                "success": True,
                "pid": pid,
                "signal": "SIGKILL" if force else "SIGTERM",
            }
    except ProcessLookupError:
        return {"error": f"进程 {pid} 不存在"}
    except PermissionError:
        return {"error": f"无权限杀死进程 {pid}（只能杀死当前用户进程）"}
    except Exception as e:
        return {"error": f"杀死进程失败: {e}"}


@ToolRegistry.register(
    "rollback_snapshot",
    description=(
        "回滚到命令执行前的安全快照。将快照中备份的文件恢复到原始位置。"
        "可用于撤销危险命令（rm、mv 等）造成的文件破坏。"
        "注意：只能恢复文件内容，无法撤销进程杀死、网络请求等副作用。"
    ),
    params={
        "snapshot_id": "快照 ID（来自 exec_command 返回的 snapshot.snapshot_id）",
        "dry_run": "可选，仅预览将恢复的文件，不实际恢复（默认 False）",
    },
    risk_level="MEDIUM",
    category="admin",
)
def rollback_snapshot(snapshot_id: str, dry_run: bool = False) -> Dict[str, Any]:
    """回滚到指定快照 — 恢复备份的文件"""
    if not snapshot_id or not snapshot_id.strip():
        return {"error": "snapshot_id 不能为空", "success": False}

    if not _re.match(r'^[a-zA-Z0-9_-]+$', snapshot_id):
        return {"error": "无效的 snapshot_id 格式", "success": False}

    snapshot_path = _SNAPSHOT_DIR / snapshot_id
    meta_path = snapshot_path / "snapshot.json"

    if not meta_path.exists():
        available = []
        if _SNAPSHOT_DIR.exists():
            available = [d.name for d in sorted(_SNAPSHOT_DIR.iterdir())[-10:]]
        return {
            "error": f"快照 '{snapshot_id}' 不存在",
            "available_snapshots": available,
            "success": False,
        }

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"快照元数据损坏: {e}", "success": False}

    backed_up = meta.get("backed_up_files", [])
    if not backed_up:
        return {
            "snapshot_id": snapshot_id,
            "command": meta.get("command", ""),
            "message": "快照中没有备份文件（命令目标可能不存在或为目录）",
            "git": meta.get("git", {}),
            "success": True,
        }

    # Git 回滚方案
    git_info = meta.get("git", {})
    git_rollback = None
    if git_info.get("is_git_repo"):
        current_ok, current_head = _run_git_safe(["rev-parse", "HEAD"], meta.get("workdir"))
        snapshot_head = git_info.get("head", "")
        if current_ok and snapshot_head and current_head != snapshot_head:
            git_rollback = {
                "snapshot_head": snapshot_head,
                "current_head": current_head,
                "command": f"git reset --hard {snapshot_head}",
                "note": "如需 git 回滚，请手动执行上述命令（影响所有文件）",
            }

    # 文件级回滚
    restored: List[str] = []
    failed: List[str] = []
    skipped: List[str] = []

    for entry in backed_up:
        original = entry.get("original", "")
        backup = entry.get("backup", "")
        entry_type = entry.get("type", "file")

        if entry_type == "directory":
            skipped.append(f"{original} (目录，仅记录存在性)")
            continue

        if not backup or not Path(backup).exists():
            skipped.append(f"{original} (无备份或备份文件缺失)")
            continue

        if dry_run:
            restored.append(f"[预览] {original} <- {backup}")
            continue

        try:
            # 确保目标父目录存在
            Path(original).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, original)
            restored.append(original)
        except Exception as e:
            failed.append(f"{original} ({e})")

    result: Dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "command": meta.get("command", ""),
        "timestamp": meta.get("timestamp", ""),
        "restored": restored,
        "skipped": skipped,
        "failed": failed,
        "success": len(failed) == 0,
        "dry_run": dry_run,
    }
    if git_rollback:
        result["git_rollback"] = git_rollback

    action = "预览" if dry_run else "回滚"
    logger.info(f"[安全快照] {action} {snapshot_id}: 恢复 {len(restored)} 个, 失败 {len(failed)} 个")
    return result


@ToolRegistry.register(
    "list_command_snapshots",
    description=(
        "列出可用的命令执行快照。可查看每个快照的命令、时间、备份文件等信息。"
    ),
    params={
        "limit": "可选，返回最近 N 个快照（默认 10）",
    },
    risk_level="LOW",
    category="query",
)
def list_command_snapshots(limit: int = 10) -> Dict[str, Any]:
    """列出可用快照"""
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 10
    limit = max(1, min(limit, 50))

    if not _SNAPSHOT_DIR.exists():
        return {"snapshots": [], "total": 0}

    dirs = sorted(_SNAPSHOT_DIR.iterdir(), key=lambda d: d.name, reverse=True)[:limit]
    snapshots: List[Dict[str, Any]] = []

    for d in dirs:
        meta_path = d / "snapshot.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            snapshots.append({
                "snapshot_id": meta.get("snapshot_id", d.name),
                "timestamp": meta.get("timestamp", ""),
                "command": meta.get("command", "")[:120],
                "git_head": meta.get("git", {}).get("head", "")[:12],
                "git_branch": meta.get("git", {}).get("branch", ""),
                "backed_up_count": len(meta.get("backed_up_files", [])),
            })
        except Exception:
            snapshots.append({"snapshot_id": d.name, "error": "元数据损坏"})

    return {"snapshots": snapshots, "total": len(snapshots)}
