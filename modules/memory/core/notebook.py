"""
AI 记事本 - 工作记忆持久化层

定位：AI大脑便签 + 短期工作区 + 长期草稿纸
- 信息处理系统可以往里写
- 注意力系统可以读重点
- 主管模型可以安排"记下来"
- 专家模型可以往里存结果
- 主模型可以总结进去
- 重启程序还在
"""
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from utils.logger import setup_logger


class AINotebook:
    """
    AI 工作记事本 - 可持久化的重点工作区
    
    与短期记忆的区别：
    - 短期记忆 = 实时思考流，重启清空
    - 记事本 = 重要内容持久化，重启还在
    """

    def __init__(self, data_dir: str = "data/notebook"):
        """
        初始化 AI 记事本
        
        Args:
            data_dir: 数据存储目录
        """
        self.data_dir = Path(data_dir)
        self.history_dir = self.data_dir / "history"
        self.current_file = self.data_dir / "current_notebook.md"
        self.index_file = self.data_dir / "index.json"
        
        self.logger = setup_logger("ai_notebook")
        
        self._ensure_dirs()
        self._load_index()
        
        if not self.current_file.exists():
            self._init_file()
        
        self.logger.info("AI 记事本初始化完成 (数据目录: %s)", self.data_dir)

    def _ensure_dirs(self):
        """确保目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(exist_ok=True)

    def _init_file(self):
        """初始化记事本文件"""
        with open(self.current_file, "w", encoding="utf-8") as f:
            f.write(f"# AI 记事本\n初始化时间：{time.ctime()}\n\n")

    def _load_index(self):
        """加载索引文件"""
        if self.index_file.exists():
            try:
                import json
                with open(self.index_file, "r", encoding="utf-8") as f:
                    self.index = json.load(f)
            except Exception as e:
                self.logger.warning("加载记事本索引失败，重置索引: %s", e)
                self.index = {"entries": [], "last_updated": None}
        else:
            self.index = {"entries": [], "last_updated": None}

    def _save_index(self):
        """保存索引文件"""
        import json
        self.index["last_updated"] = time.time()
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)

    def _add_to_index(self, entry_type: str, title: str = None):
        """添加到索引"""
        entry = {
            "type": entry_type,
            "title": title,
            "timestamp": time.time(),
            "ctime": time.ctime()
        }
        self.index["entries"].append(entry)
        self._save_index()

    # ==============================
    # 基础操作
    # ==============================

    def write_line(self, text: str) -> str:
        """
        追加一行到记事本
        
        Args:
            text: 要写入的文本
            
        Returns:
            写入的内容
        """
        timestamp = time.strftime("%H:%M:%S")
        line = f"- [{timestamp}] {text}\n"
        
        with open(self.current_file, "a", encoding="utf-8") as f:
            f.write(line)
        
        self.logger.debug("记事本追加: %s...", text[:50])
        return line.strip()

    def write_block(self, title: str, content: str, separator: str = "=") -> None:
        """
        分块记录内容
        
        Args:
            title: 区块标题
            content: 区块内容
            separator: 分隔符类型 ("=" 或 "-")
        """
        sep = separator * len(title)
        
        with open(self.current_file, "a", encoding="utf-8") as f:
            f.write(f"\n{sep}\n")
            f.write(f"{title}\n")
            f.write(f"{sep}\n")
            f.write(f"{content}\n\n")
        
        self._add_to_index("block", title)
        self.logger.debug("记事本写入区块: %s", title)

    def write_thought(self, thought: str) -> None:
        """
        记录思考结果
        
        Args:
            thought: 思考内容
        """
        self.write_block(f"思考 {time.strftime('%H:%M:%S')}", thought, "-")

    def write_task(self, task: str, status: str = "pending") -> None:
        """
        记录任务
        
        Args:
            task: 任务内容
            status: 任务状态 (pending/in_progress/completed)
        """
        status_icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(status, "📝")
        self.write_line(f"{status_icon} [{status.upper()}] {task}")
        self._add_to_index("task", task[:50])

    def write_result(self, label: str, result: Any) -> None:
        """
        记录计算/执行结果
        
        Args:
            label: 结果标签
            result: 结果内容
        """
        content = f"**{label}**: {result}"
        self.write_block(f"结果 {time.strftime('%H:%M:%S')}", content)

    def write_memory(self, memory_type: str, content: str) -> None:
        """
        记录重要记忆
        
        Args:
            memory_type: 记忆类型
            content: 记忆内容
        """
        self.write_block(f"记忆 [{memory_type}]", content)

    # ==============================
    # 读取操作
    # ==============================

    def read_all(self) -> str:
        """
        读取全部内容
        
        Returns:
            记事本全部内容
        """
        if not self.current_file.exists():
            return ""
        
        with open(self.current_file, "r", encoding="utf-8") as f:
            return f.read()

    def read_lines(self) -> List[str]:
        """
        按行读取
        
        Returns:
            所有非空行
        """
        content = self.read_all()
        return [line.strip() for line in content.split("\n") if line.strip()]

    def search(self, keyword: str, case_sensitive: bool = False) -> List[Dict[str, str]]:
        """
        搜索关键词
        
        Args:
            keyword: 关键词
            case_sensitive: 是否区分大小写
            
        Returns:
            匹配的行的列表
        """
        content = self.read_all()
        
        if not case_sensitive:
            content = content.lower()
            keyword = keyword.lower()
        
        lines = self.read_lines()
        results = []
        
        for i, line in enumerate(lines):
            check_line = line if case_sensitive else line.lower()
            if keyword in check_line:
                results.append({
                    "line_number": i + 1,
                    "content": line,
                    "match": keyword
                })
        
        self.logger.debug("搜索 '%s' 找到 %d 条结果", keyword, len(results))
        return results

    def get_recent(self, lines: int = 10) -> str:
        """
        获取最近 N 行
        
        Args:
            lines: 行数
            
        Returns:
            最近的内容
        """
        all_lines = self.read_lines()
        recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return "\n".join(recent)

    def get_blocks(self) -> List[Dict[str, str]]:
        """
        获取所有区块
        
        Returns:
            区块列表
        """
        content = self.read_all()
        blocks = []
        current_title = None
        current_content = []
        in_block = False
        
        for line in content.split("\n"):
            if line.startswith("## ") or line.startswith("==="):
                if current_title:
                    blocks.append({
                        "title": current_title,
                        "content": "\n".join(current_content).strip()
                    })
                current_title = line.replace("=", "").strip()
                current_content = []
                in_block = True
            elif in_block and line.strip():
                current_content.append(line)
        
        if current_title:
            blocks.append({
                "title": current_title,
                "content": "\n".join(current_content).strip()
            })
        
        return blocks

    # ==============================
    # 管理操作
    # ==============================

    def clear(self, auto_backup: bool = True) -> None:
        """
        清空记事本
        
        Args:
            auto_backup: 是否自动备份
        """
        if auto_backup:
            self.save_version()
        
        self._init_file()
        self.index = {"entries": [], "last_updated": time.time()}
        self._save_index()
        
        self.logger.info("记事本已清空（已备份）")

    def save_version(self, comment: str = None) -> str:
        """
        保存历史版本
        
        Args:
            comment: 版本备注
            
        Returns:
            版本文件路径
        """
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        version_name = f"notebook_{timestamp}.md"
        version_file = self.history_dir / version_name
        
        content = self.read_all()
        
        if comment:
            header = f"# 版本快照 | {time.ctime()}\n# 备注: {comment}\n\n"
            content = header + content
        
        with open(version_file, "w", encoding="utf-8") as f:
            f.write(content)
        
        self.logger.info("保存版本快照: %s", version_name)
        return str(version_file)

    def list_versions(self) -> List[Dict[str, Any]]:
        """
        列出所有历史版本
        
        Returns:
            版本列表
        """
        versions = []
        
        for f in sorted(self.history_dir.glob("notebook_*.md"), reverse=True):
            stat = f.stat()
            versions.append({
                "filename": f.name,
                "path": str(f),
                "size": stat.st_size,
                "created": stat.st_ctime,
                "ctime": time.ctime(stat.st_ctime)
            })
        
        return versions

    def restore_version(self, version_filename: str) -> bool:
        """
        恢复历史版本
        
        Args:
            version_filename: 版本文件名
            
        Returns:
            是否成功
        """
        version_file = self.history_dir / version_filename
        
        if not version_file.exists():
            self.logger.warning("版本文件不存在: %s", version_filename)
            return False
        
        current_content = self.read_all()
        self.save_version("自动备份-恢复前")
        
        with open(version_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        with open(self.current_file, "w", encoding="utf-8") as f:
            f.write(content)
        
        self.logger.info("恢复版本: %s", version_filename)
        return True

    # ==============================
    # 构建上下文
    # ==============================

    def build_prompt_context(self, max_lines: int = None) -> str:
        """
        构建给 AI 看的记事本上下文
        
        Args:
            max_lines: 最大行数
            
        Returns:
            格式化的上下文字符串
        """
        if max_lines:
            content = self.get_recent(max_lines)
        else:
            content = self.read_all()
        
        if not content.strip():
            return ""
        
        return f"""
{'='*50}
[AI 记事本]
{'='*50}
{content}
{'='*50}
"""

    def build_summary(self) -> str:
        """
        构建摘要信息
        
        Returns:
            摘要字符串
        """
        stats = self.get_statistics()
        return (
            f"记事本状态 | "
            f"总行数: {stats['total_lines']} | "
            f"区块: {stats['total_blocks']} | "
            f"版本: {stats['total_versions']}"
        )

    # ==============================
    # 状态与统计
    # ==============================

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        content = self.read_all()
        blocks = self.get_blocks()
        
        versions = list(self.history_dir.glob("notebook_*.md"))
        
        return {
            "total_lines": len([l for l in content.split("\n") if l.strip()]),
            "total_blocks": len(blocks),
            "total_versions": len(versions),
            "file_size": self.current_file.stat().st_size if self.current_file.exists() else 0,
            "last_updated": self.index.get("last_updated"),
            "last_updated_ctime": time.ctime(self.index.get("last_updated", 0))
        }

    def get_status(self) -> Dict[str, Any]:
        """
        获取完整状态
        
        Returns:
            状态字典
        """
        return {
            "exists": self.current_file.exists(),
            "statistics": self.get_statistics(),
            "recent_content": self.get_recent(5)
        }

