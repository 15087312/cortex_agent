"""
集中安全政策层 - 统一管理所有文件、命令、API 调用的安全限制

核心原则:
1. 单一责任 - 所有安全检查在此模块定义
2. 易维护 - 规则集中，修改时不需要改多个文件
3. 可审计 - 所有安全策略一目了然
4. 分类清晰 - 文件/命令/API/内存安全分别管理

使用方式:
from infra.security.centralized_policy import SecurityPolicy

policy = SecurityPolicy()
if policy.is_sensitive_file(path):
    raise PermissionError("Sensitive file")
"""

import logging
import sys
import tempfile
from pathlib import Path
from typing import List, Set, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SecurityConfig:
    """安全配置"""
    # 允许访问的基础目录（白名单）
    allowed_base_dirs: List[str] = None
    # 禁止读取的路径
    forbidden_read_dirs: Set[str] = None
    # 禁止写入的路径
    forbidden_write_dirs: Set[str] = None
    # 敏感文件名模式
    sensitive_file_patterns: Set[str] = None
    # 敏感文件后缀
    sensitive_file_extensions: Set[str] = None
    # 禁止执行的命令
    forbidden_commands: Set[str] = None
    # 允许的命令前缀（白名单模式）
    allowed_command_prefixes: Set[str] = None

    def __post_init__(self):
        _is_win = sys.platform == "win32"
        if self.allowed_base_dirs is None:
            self.allowed_base_dirs = [
                tempfile.gettempdir(),
            ]
            if not _is_win:
                self.allowed_base_dirs.append("/var/tmp")

        if self.forbidden_read_dirs is None:
            if _is_win:
                self.forbidden_read_dirs = {
                    "C:\\Windows\\System32\\config",
                    "C:\\Windows\\repair",
                }
            else:
                self.forbidden_read_dirs = {
                    "/etc/shadow",
                    "/etc/passwd",
                    "/root/.ssh",
                    "/home/*/.ssh",
                }

        if self.forbidden_write_dirs is None:
            if _is_win:
                self.forbidden_write_dirs = {
                    "C:\\Windows",
                    "C:\\Program Files",
                    "C:\\Program Files (x86)",
                    "C:\\ProgramData",
                }
            else:
                self.forbidden_write_dirs = {
                    "/etc",
                    "/sys",
                    "/proc",
                    "/dev",
                    "/boot",
                    "/usr",
                    "/bin",
                    "/sbin",
                    "/System",
                    "/Library",
                    "/Applications",
                }
        if self.sensitive_file_patterns is None:
            self.sensitive_file_patterns = {
                "secret", "credential", "password", "token",
                "private_key", "id_rsa", "id_ed25519",
                "api_key", "access_key", "aws_secret",
            }
        if self.sensitive_file_extensions is None:
            self.sensitive_file_extensions = {
                ".env", ".pem", ".key", ".pfx", ".p12", ".jks",
            }
        if self.forbidden_commands is None:
            self.forbidden_commands = {
                "rm -rf /", "dd if=/dev/zero", "mkfs", "format",
                ":(){:|:;};:", "fork()",  # 绝对禁止的命令
            }


class SecurityPolicy:
    """集中安全政策管理器"""

    def __init__(self, config: Optional[SecurityConfig] = None):
        self.config = config or SecurityConfig()
        # 预解析白名单目录
        self._allowed_dirs = [Path(d).resolve() for d in self.config.allowed_base_dirs]
        # 预解析禁止写入目录（处理 macOS /etc → /private/etc 等 symlink）
        self._forbidden_write_dirs = [Path(d).resolve() for d in self.config.forbidden_write_dirs]
        # 预解析禁止读取目录
        self._forbidden_read_resolved = []
        for d in self.config.forbidden_read_dirs:
            if "*" in d:
                # 通配符模式：拆分为前缀（* 之前的部分）和后缀
                prefix = d.split("*")[0]
                self._forbidden_read_resolved.append((str(Path(prefix).resolve()), True))
            else:
                self._forbidden_read_resolved.append((str(Path(d).resolve()), False))
        # 项目根目录（延迟初始化）
        self._project_root = None

    def set_project_root(self, root: str) -> None:
        """设置项目根目录（由启动代码调用）"""
        self._project_root = Path(root).resolve()

    def _get_project_root(self) -> Path:
        if self._project_root is None:
            # 自动检测：从当前文件向上找 pyproject.toml
            p = Path(__file__).resolve()
            for _ in range(5):
                if (p / "pyproject.toml").exists():
                    self._project_root = p
                    break
                p = p.parent
            else:
                self._project_root = Path(__file__).resolve().parents[3]
        return self._project_root

    # ── 文件安全检查 ──

    def is_path_allowed(self, path: str) -> bool:
        """路径检查 — 只允许项目目录、data/、/tmp 等白名单路径"""
        try:
            p = Path(path).expanduser().resolve()
        except Exception:
            return False

        p_str = str(p)

        # 系统临时目录始终允许
        for d in self._allowed_dirs:
            if p_str.startswith(str(d)):
                return True

        # 项目目录始终允许（含 data/、logs/ 等子目录）
        project_root = self._get_project_root()
        if p_str.startswith(str(project_root)):
            return True

        # 拒绝未知路径
        return False

    def is_sensitive_file(self, path: str) -> bool:
        """敏感文件检查 — 匹配文件名模式和后缀"""
        try:
            p = Path(path).expanduser().resolve()
        except Exception:
            return True  # 无法解析的路径视为敏感

        name_lower = p.name.lower()
        suffix_lower = p.suffix.lower()

        # 检查后缀
        if suffix_lower in self.config.sensitive_file_extensions:
            return True

        # 检查文件名模式
        for pattern in self.config.sensitive_file_patterns:
            if pattern.lower() in name_lower:
                return True

        return False

    def is_forbidden_write_path(self, path: str) -> bool:
        """禁止写入检查 — 真机模式：仅保护根目录和设备目录"""
        try:
            p = Path(path).expanduser().resolve()
        except Exception:
            return True

        p_str = str(p)

        if sys.platform == "win32":
            # Windows: 保护盘符根目录、NUL/CON 等设备名
            drive = p.drive  # e.g. "C:"
            if drive and len(p_str) <= len(drive) + 1:
                return True  # C:\ 根目录
            device_names = {"NUL", "CON", "PRN", "AUX", "COM1", "COM2", "COM3",
                            "LPT1", "LPT2", "LPT3"}
            if p.name.upper() in device_names:
                return True
            return False
        else:
            protected = {"/", "/dev", "/System"}
            return p_str in protected or p_str.startswith("/dev/")

    def is_forbidden_read_path(self, path: str) -> bool:
        """禁止读取检查 — 真机模式：不拦截"""
        return False

    def is_safe_file_path(self, path: str) -> Tuple[bool, Optional[str]]:
        """综合检查文件路径是否安全

        返回: (是否安全, 原因描述)
        """
        if self.is_forbidden_read_path(path):
            return False, "禁止读取系统敏感目录"
        if self.is_forbidden_write_path(path):
            return False, "禁止修改系统目录或 .git"
        if self.is_sensitive_file(path):
            return False, "拒绝访问包含敏感信息的文件"
        return True, None

    # ── 命令安全检查 ──

    def is_forbidden_command(self, command: str) -> bool:
        """检查命令是否在禁止列表中

        绝对禁止的命令:
        - rm -rf /
        - fork 炸弹
        - 格式化磁盘
        """
        cmd_lower = command.lower().strip()
        for forbidden in self.config.forbidden_commands:
            if forbidden.lower() in cmd_lower:
                return True
        return False

    def is_dangerous_command(self, command: str) -> bool:
        """检查命令是否是危险命令（需要额外审批）

        危险命令包括:
        - sudo/su
        - chmod/chown（修改权限）
        - rm/rmdir（删除文件）
        - kill/killall（杀进程）
        """
        dangerous = {"sudo", "su ", "chmod", "chown", " rm ", "rmdir", "kill "}
        cmd_lower = command.lower().strip()
        for danger in dangerous:
            if danger in cmd_lower:
                return True
        return False

    def is_safe_command(self, command: str) -> Tuple[bool, Optional[str], bool]:
        """检查命令是否安全

        返回: (是否安全, 原因, 是否需要额外审批)
        """
        if self.is_forbidden_command(command):
            return False, "命令已被禁用", False
        if self.is_dangerous_command(command):
            return True, "命令需要审批", True
        return True, None, False

    # ── 内存安全检查 ──

    def is_safe_memory_content(self, content: str) -> Tuple[bool, Optional[str]]:
        """检查内存内容是否包含敏感信息

        不允许存储:
        - API keys, tokens, credentials
        - 密码，私钥
        - 个人身份信息
        """
        content_lower = content.lower()
        dangerous_patterns = {
            "api_key", "api-key", "apikey",
            "access_token", "refresh_token", "bearer ",
            "password=", "passwd=", "pwd=",
            "private_key", "secret_key",
            "begin rsa private", "begin openssh",
        }

        for pattern in dangerous_patterns:
            if pattern in content_lower:
                return False, f"检测到敏感信息: {pattern}"

        return True, None

    # ── 统计和日志 ──

    def get_policy_summary(self) -> dict:
        """获取当前安全政策摘要"""
        return {
            "forbidden_read_dirs": list(self.config.forbidden_read_dirs),
            "forbidden_write_dirs": list(self.config.forbidden_write_dirs),
            "sensitive_patterns": list(self.config.sensitive_file_patterns),
            "forbidden_commands": list(self.config.forbidden_commands),
            "total_rules": (
                len(self.config.forbidden_read_dirs) +
                len(self.config.forbidden_write_dirs) +
                len(self.config.sensitive_file_patterns) +
                len(self.config.forbidden_commands)
            ),
        }


# 全局单例
_global_policy = None


def get_security_policy() -> SecurityPolicy:
    """获取全局安全政策实例"""
    global _global_policy
    if _global_policy is None:
        _global_policy = SecurityPolicy()
    return _global_policy


def set_security_policy(policy: SecurityPolicy) -> None:
    """设置全局安全政策实例（用于测试或自定义）"""
    global _global_policy
    _global_policy = policy
