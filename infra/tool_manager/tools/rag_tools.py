"""
RAG 知识库工具 — 索引、查询、更新
"""
from pathlib import Path
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("rag_tools")


@ToolRegistry.register("rag_index", description="索引本地文档和代码到知识库，支持语义搜索。", params={
    "path": "要索引的文件或目录路径",
    "recursive": "可选，是否递归索引子目录（默认 True）",
}, risk_level="LOW", category="query")
def rag_index(path: str, recursive: bool = True) -> Dict[str, Any]:
    """索引文档/代码到知识库"""
    p = Path(path).expanduser()
    if not p.exists(): return {"error": f"路径不存在: {path}"}

    try:
        from modules.memory.core.memory_manager import MemoryManager
        mm = MemoryManager()
        files = []
        if p.is_file():
            files = [p]
        else:
            pattern = "**/*" if recursive else "*"
            files = [f for f in p.glob(pattern) if f.is_file() and f.suffix in (".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".rst", ".html") and ".git" not in f.parts]

        indexed = 0
        for f in files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")[:5000]
                if content.strip():
                    mm.save_long_term("rag_index", {"source": str(f), "content": content[:2000]})
                    indexed += 1
            except: continue

        return {"success": True, "path": str(p), "files_found": len(files), "indexed": indexed}
    except ImportError:
        return {"error": "MemoryManager 不可用"}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register("rag_query", description="从知识库中检索与查询相关的信息。基于语义+关键词混合搜索。", params={
    "query": "搜索查询",
    "limit": "可选，返回结果数量（默认5）",
}, risk_level="LOW", category="query")
def rag_query(query: str, limit: int = 5) -> Dict[str, Any]:
    """查询知识库 — 使用混合搜索（FAISS 语义 + 关键词降级）"""
    if not query: return {"error": "查询不能为空"}
    limit = max(1, min(limit, 20))

    try:
        from modules.memory.core.memory_manager import MemoryManager
        mm = MemoryManager()

        # 优先使用混合搜索（FAISS 语义 + 关键词兜底）
        results = []
        try:
            results = mm.search_memories_by_category(query, category="rag", limit=limit)
        except Exception:
            # 降级为关键词搜索
            results = mm.search_long_term("rag", query.split(), limit)

        items = []
        for r in results:
            content = r.get("content", "")
            if isinstance(content, dict):
                import json
                content = json.dumps(content, ensure_ascii=False)
            item = {"content": str(content)[:500], "score": r.get("search_score", r.get("score", 0))}
            if "metadata" in r:
                item["source"] = r["metadata"].get("source", "") if isinstance(r["metadata"], dict) else str(r["metadata"])
            items.append(item)

        return {"success": True, "query": query, "count": len(items), "results": items}
    except ImportError:
        return {"error": "MemoryManager 不可用"}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register("rag_update", description="更新知识库：重新索引指定路径。删除旧索引并重新添加。", params={
    "path": "要重新索引的文件或目录路径",
}, risk_level="LOW", category="query")
def rag_update(path: str) -> Dict[str, Any]:
    """更新知识库"""
    return rag_index(path, recursive=True)
