"""
文件编辑工具 — 精确查找并替换文件内容

对应 Claude Code 的 FileEditTool。基于字符串匹配的查找替换，不会重新创建整个文件。
"""
from typing import Dict, Any

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("file_edit")


@ToolRegistry.register(
    "file_edit",
    description=(
        "精确编辑文件内容：查找一个唯一字符串并用新内容替换它。"
        "不会重新创建整个文件——只替换匹配的部分。"
        "适用于修改函数体、变量值、配置项等局部修改。"
        "如需创建新文件或大幅修改，请使用 write_file。"
    ),
    params={
        "path": "要编辑的文件路径（绝对路径或相对于项目根目录）",
        "old_string": "要被替换的旧文本（必须是文件中唯一的精确匹配）",
        "new_string": "替换后的新文本内容",
    },
    risk_level="MEDIUM",
    category="admin",
    tags=["mutation"],
    core=True,
)
def file_edit(path: str, old_string: str, new_string: str) -> Dict[str, Any]:
    """精确编辑文件内容"""
    if not path:
        return {"error": "文件路径不能为空"}
    if not old_string:
        return {"error": "old_string 不能为空"}

    import os

    # 展开 ~ 和相对路径
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        # 相对于项目根目录
        try:
            from pathlib import Path
            project_root = Path(__file__).resolve().parents[3]
            path = str(project_root / path)
        except Exception as e:
            logger.warning(f"路径解析失败: {e}")
            return {"error": f"路径不是绝对路径且无法解析: {path}"}

    # 路径安全检查
    from pathlib import Path
    from infra.tool_manager.tools.file_manager import _is_path_allowed
    if not _is_path_allowed(Path(path)):
        return {"error": f"路径不在允许范围内: {path}"}

    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}
    if not os.path.isfile(path):
        return {"error": f"路径不是文件: {path}"}

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # 先试试精确匹配
        if old_string in content:
            new_content = content.replace(old_string, new_string, 1)
            changes = 1
        else:
            # 尝试行尾不敏感的 match
            import re
            normalized_old = old_string.replace("\r\n", "\n").replace("\r", "\n")
            normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
            if normalized_old in normalized_content:
                new_normalized = normalized_content.replace(normalized_old, new_string.replace("\r\n", "\n").replace("\r", "\n"), 1)
                # 保持原文件的行尾风格
                if "\r\n" in content:
                    new_content = new_normalized.replace("\n", "\r\n")
                else:
                    new_content = new_normalized
                changes = 1
            else:
                return {"error": f"未在文件中找到匹配的文本。old_string 必须与文件中的内容完全匹配（注意空格、缩进和换行）。"}

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "success": True,
            "path": path,
            "changes": changes,
            "message": "文件已编辑",
        }

    except PermissionError:
        return {"error": f"无权限写入文件: {path}"}
    except Exception as e:
        logger.error(f"文件编辑失败: {e}")
        return {"error": f"文件编辑失败: {e}"}
