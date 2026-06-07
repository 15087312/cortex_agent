"""命令注册表 — 参考 Open-ClaudeCode 的 commands.ts"""

from typing import Any, Dict, List, Optional


class Command:
    """单个命令定义"""

    def __init__(
        self,
        name: str,
        aliases: List[str],
        description: str,
        action: str,  # 用于标识命令类别的标签
        handler=None,
        is_visible: bool = True,
    ):
        self.name = name
        self.aliases = aliases
        self.description = description
        self.action = action
        self.handler = handler
        self.is_visible = is_visible


# ── 默认命令 ──

_default_commands: List[Command] = []


def register(name: str, aliases: List[str], description: str, action: str = "action",
             handler=None, is_visible: bool = True):
    """注册一个命令"""
    cmd = Command(name, aliases, description, action, handler, is_visible)
    _default_commands.append(cmd)
    return cmd


def get_all() -> List[Command]:
    return list(_default_commands)


# ── 内置命令注册 ──

register("/help", ["/h", "/?"], "显示帮助信息", "help")
register("/exit", ["/q", "/quit"], "退出 CLI", "exit")
register("/clear", ["/c"], "清空对话框和工具追踪", "clear")
register("/status", ["/s"], "查看系统运行状态", "status")
register("/memory", ["/mem"], "查看记忆系统状态", "memory")
register("/session", ["/sess"], "查看会话列表及对话框内容（/session <主管名> 查看副会话详情）", "session")
register("/tools", ["/t"], "切换工具调用面板", "tools")
register("/debug", ["/d"], "切换调试面板", "debug")
register("/thinking", ["/th"], "切换思考过程显示", "thinking")
register("/export", ["/e"], "导出工具调用为 JSON 文件", "export")
register("/search", ["/find"], "搜索长期记忆 (用法: /search <query>)", "search")
register("/context", ["/ctx"], "加载并显示当前上下文", "context")
register("/stop", ["/pause"], "停止当前思考处理", "stop")
register("/mode", ["/m"], "切换陪伴模式 (用法: /mode on/off)", "mode")
register("/config", ["/cfg"], "查看或修改配置 (用法: /config 或 /config KEY VALUE)", "config")


def find_command(text: str) -> Optional[Command]:
    """根据输入文本查找匹配的命令，支持 / 和 ! 前缀，支持命令后跟参数"""
    text = text.strip()
    if not (text.startswith("/") or text.startswith("!")):
        return None
    # 将 ! 前缀转为 / 前缀
    if text.startswith("!"):
        text = "/" + text[1:]
    # 提取命令部分（可能带参数，如 /session 代码主管）
    text_lower = text.lower()
    parts = text_lower.split(maxsplit=1)
    cmd_part = parts[0]
    for cmd in _default_commands:
        if cmd_part == cmd.name or cmd_part in cmd.aliases:
            return cmd
    return None


def is_command(text: str) -> bool:
    """判断输入是否为命令"""
    text = text.strip()
    return text.startswith("/") or text.startswith("!")
