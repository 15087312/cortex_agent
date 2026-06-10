"""
工具发现系统 - 让模型能搜索和发现匹配的工具

长期改动 #7 & #8 的基础设施

功能:
1. Tool Search - 基于关键词和语义的工具搜索
2. Dynamic Context Construction - 根据任务动态选择工具子集

使用场景:
- 模型: "我需要读取和修改文件，有什么工具?"
- 系统: 搜索关键词 ["read", "file", "write"] → 返回 [read_file, write_file, ...]
- 模型: 只将这些工具注入到当前上下文，避免无关工具污染

实现策略:
- Phase 1: 关键词匹配 (快速)
- Phase 2: 嵌入向量搜索 (准确) — 需要 FAISS/embedding 集成
- Phase 3: 任务上下文感知 (智能) — 根据对话历史推荐工具
"""

import threading
import time
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from .tool_registry import ToolRegistry, ToolInfo


@dataclass
class ToolSearchResult:
    """工具搜索结果"""
    tool_name: str
    tool_info: ToolInfo
    relevance_score: float  # 0-1, 越高越相关
    match_reason: str  # 匹配原因: "keyword" / "tag" / "category" / "description"


# 索引缓存 TTL（秒）
_INDEX_CACHE_TTL = 300  # 5 分钟


class ToolDiscoveryEngine:
    """工具发现引擎 - 帮助模型找到合适的工具"""

    def __init__(self):
        self._tool_keywords_cache = {}  # {tool_name: [keywords]}
        self._tag_index = {}  # {tag: [tool_names]}
        self._last_build_time = 0.0
        self._build_indexes()

    def _maybe_rebuild_indexes(self):
        """如果缓存过期则重建索引"""
        if time.time() - self._last_build_time > _INDEX_CACHE_TTL:
            self._build_indexes()

    def _build_indexes(self):
        """构建工具索引（关键词、标签等）"""
        self._tool_keywords_cache.clear()
        self._tag_index.clear()
        for name, tool in ToolRegistry._tools.items():
            # 提取工具关键词: 名字 + 描述中的关键词
            keywords = self._extract_keywords(name, tool)
            self._tool_keywords_cache[name] = keywords

            # 构建标签索引
            for tag in tool.tags:
                if tag not in self._tag_index:
                    self._tag_index[tag] = []
                self._tag_index[tag].append(name)
        self._last_build_time = time.time()

    def _extract_keywords(self, tool_name: str, tool_info: ToolInfo) -> List[str]:
        """从工具名和描述提取关键词"""
        keywords = set()

        # 工具名本身是关键词
        keywords.add(tool_name)
        keywords.update(tool_name.split("_"))

        # 从描述提取关键词 (简单分词)
        description = tool_info.description.lower()
        for word in description.split():
            if len(word) > 3 and not word.startswith("【"):
                keywords.add(word.strip(",.;:\"'()"))

        # 工具分类
        keywords.add(f"category:{tool_info.category}")
        keywords.add(f"risk:{tool_info.risk_level}")

        return list(keywords)

    def search(
        self,
        query: str,
        limit: int = 5,
        min_relevance: float = 0.3,
    ) -> List[ToolSearchResult]:
        """搜索匹配的工具

        Args:
            query: 搜索查询 (自然语言或关键词)
            limit: 返回最多多少结果
            min_relevance: 最小相关度阈值

        Returns:
            按相关度排序的工具列表
        """
        self._maybe_rebuild_indexes()
        query_lower = query.lower()
        query_keywords = query_lower.split()

        results = []

        for tool_name, tool_info in ToolRegistry._tools.items():
            relevance, reason = self._calculate_relevance(
                query_lower,
                query_keywords,
                tool_name,
                tool_info,
            )

            if relevance >= min_relevance:
                results.append(
                    ToolSearchResult(
                        tool_name=tool_name,
                        tool_info=tool_info,
                        relevance_score=relevance,
                        match_reason=reason,
                    )
                )

        # 按相关度排序
        results.sort(key=lambda x: (-x.relevance_score, x.tool_name))

        return results[:limit]

    def _calculate_relevance(
        self,
        query_lower: str,
        query_keywords: List[str],
        tool_name: str,
        tool_info: ToolInfo,
    ) -> Tuple[float, str]:
        """计算工具与查询的相关度

        返回: (相关度分数 0-1, 匹配原因)
        """
        score = 0.0
        reason = ""

        # 1. 精确名字匹配 (权重 0.8)
        if query_lower == tool_name.lower():
            return 1.0, "exact_name"

        # 2. 名字包含查询 (权重 0.6)
        if query_lower in tool_name.lower():
            score = max(score, 0.6)
            reason = "name_contains"

        # 3. 标签匹配 (权重 0.7)
        for tag in tool_info.tags:
            if query_lower in tag.lower() or tag.lower() in query_lower:
                score = max(score, 0.7)
                reason = "tag_match"

        # 4. 关键词匹配 (权重 0.4-0.6，取决于匹配数量)
        keywords = self._tool_keywords_cache.get(tool_name, [])
        matched_keywords = [k for k in query_keywords if any(k in kw for kw in keywords)]
        if matched_keywords:
            keyword_score = 0.4 + min(0.2, len(matched_keywords) * 0.05)
            score = max(score, keyword_score)
            reason = f"keyword_match ({len(matched_keywords)} keywords)"

        # 5. 分类匹配 (权重 0.3)
        if f"category:{tool_info.category}" in query_lower:
            score = max(score, 0.3)
            reason = "category_match"

        return score, reason

    def get_tools_by_category(self, category: str) -> List[str]:
        """按分类获取工具列表

        分类: query / mutation / admin
        """
        return [
            name for name, tool in ToolRegistry._tools.items()
            if tool.category == category
        ]

    def get_tools_by_tag(self, tag: str) -> List[str]:
        """按标签获取工具列表"""
        return self._tag_index.get(tag, [])

    def recommend_tools_for_task(
        self,
        task_description: str,
        max_tools: int = 10,
    ) -> List[str]:
        """根据任务描述推荐工具

        这是 Phase 3 的基础：动态上下文构造
        """
        # 步骤 1: 搜索匹配的工具
        search_results = self.search(task_description, limit=max_tools)

        # 步骤 2: 根据风险等级过滤 (expert 不能看 HIGH 工具)
        # 这里需要知道调用者的角色，所以返回完整搜索结果
        # 调用者负责根据权限过滤

        return [r.tool_name for r in search_results]


# 全局单例
_discovery_engine = None
_discovery_engine_lock = threading.Lock()


def get_tool_discovery_engine() -> ToolDiscoveryEngine:
    """获取全局工具发现引擎"""
    global _discovery_engine
    if _discovery_engine is None:
        with _discovery_engine_lock:
            if _discovery_engine is None:
                _discovery_engine = ToolDiscoveryEngine()
    return _discovery_engine
