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
from pathlib import Path
from typing import List, Set, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SecurityConfig:
    """安全配置"""
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
        if self.forbidden_read_dirs is None:
            self.forbidden_read_dirs = {
                "/etc/shadow",
                "/etc/passwd",
                "/root/.ssh",
                "/home/*/.ssh",
            }
        if self.forbidden_write_dirs is None:
            self.forbidden_write_dirs = {
                "/etc",
                "/sys",
                "/proc",
                "/dev",
                "/boot",
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

    # ── 文件安全检查 ──

    def is_sensitive_file(self, path: str) -> bool:
        """检查文件是否包含敏感信息

        敏感文件包括:
        - .env, .env.local, .env.*.local
        - .ssh, .netrc, config.json (用于 SSH)
        - 包含 secret/credential/password/token/key 等关键字的文件
        """
        try:
            p = Path(path).resolve()
        except Exception as e:
            logger.warning(f"路径解析失败: {e}")
            return False

        name_lower = p.name.lower()
        path_lower = str(p).lower()

        # 检查文件后缀
        for ext in self.config.sensitive_file_extensions:
            if name_lower.endswith(ext):
                return True

        # 检查文件名中的敏感关键字
        for pattern in self.config.sensitive_file_patterns:
            if pattern in name_lower:
                return True

        # 检查路径中的 .ssh 目录
        if ".ssh" in p.parts or ".netrc" in p.parts:
            return True

        return False

    def is_forbidden_write_path(self, path: str) -> bool:
        """检查路径是否禁止写入

        禁止写入:
        - 系统目录: /etc, /sys, /proc, /dev, /boot
        - git 元数据: .git 目录及内容
        """
        try:
            p = Path(path).resolve()
        except Exception as e:
            logger.warning(f"写入路径解析失败: {e}")
            return False

        path_str = str(p)

        # 检查禁止写入目录
        for forbidden_dir in self.config.forbidden_write_dirs:
            if path_str.startswith(forbidden_dir):
                return True

        # 禁止修改 .git 目录
        if ".git" in p.parts:
            return True

        return False

    def is_forbidden_read_path(self, path: str) -> bool:
        """检查路径是否禁止读取

        禁止读取:
        - 系统敏感文件: /etc/shadow, /etc/passwd
        - SSH 私钥: ~/.ssh/*, /root/.ssh/*
        """
        try:
            p = Path(path).resolve()
        except Exception as e:
            logger.warning(f"读取路径解析失败: {e}")
            return False

        path_str = str(p)

        # 检查禁止读取目录
        for forbidden_dir in self.config.forbidden_read_dirs:
            # 支持简单的通配符 (* 在末尾)
            if forbidden_dir.endswith("/*"):
                prefix = forbidden_dir[:-2]
                if path_str.startswith(prefix):
                    return True
            elif path_str.startswith(forbidden_dir):
                return True

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
