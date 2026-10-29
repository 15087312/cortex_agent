"""知识库数据模型 — 文档 / 片段 / 命中结果

与事件记忆的 MemoryEvent 刻意保持独立：知识片段带原文与精确字符区间，
不做 LLM 改写、不随时间衰减、不按模型归属隔离。
"""
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class KnowledgeDoc:
    """已入库文档（一份文件一条记录）"""
    id: str = ""                    # kd_<uuid12>
    title: str = ""
    source_path: str = ""           # 相对项目根（不在项目内时为绝对路径），唯一
    content_hash: str = ""          # sha256(规范化全文)，增量判断依据
    version: int = 1                # 内容变化即 +1，不保留历史（二期可加）
    mime: str = "text/markdown"
    size_bytes: int = 0
    chunk_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    indexed_at: str = ""            # 最近一次索引成功时间

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source_path": self.source_path,
            "version": self.version,
            "mime": self.mime,
            "size_bytes": self.size_bytes,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "indexed_at": self.indexed_at,
        }


@dataclass
class KnowledgeChunk:
    """文档片段 — 检索的最小单位，text 为原文子串（未改写）"""
    id: str = ""                    # kc_<uuid12>
    doc_id: str = ""
    seq: int = 0                    # 文档内顺序，用于相邻合并
    heading_path: str = ""          # "设计计划 > 5. 索引与切分"
    text: str = ""
    char_start: int = 0             # 在规范化全文中的起止，供精确回溯原文
    char_end: int = 0
    token_count: int = 0
    terms: str = ""                 # jieba 预分词（空格分隔），供关键词路

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "seq": self.seq,
            "heading_path": self.heading_path,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "token_count": self.token_count,
        }


@dataclass
class KnowledgeHit:
    """检索命中 — 携带完整出处，可直接渲染为可引用条目"""
    chunk_id: str = ""
    doc_id: str = ""
    title: str = ""
    heading_path: str = ""
    text: str = ""
    score: float = 0.0
    source_path: str = ""
    char_start: int = 0
    char_end: int = 0
    seq: int = 0
    token_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "title": self.title,
            "heading_path": self.heading_path,
            "text": self.text,
            "score": round(self.score, 4),
            "source_path": self.source_path,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass
class IngestResult:
    """单次摄取结果（可聚合多个文件）"""
    added: list = field(default_factory=list)      # [{id, title, source_path, chunks}]
    updated: list = field(default_factory=list)
    unchanged: list = field(default_factory=list)  # [{id, source_path}]
    skipped: list = field(default_factory=list)    # [{path, reason}]
    errors: list = field(default_factory=list)     # [{path, error}]

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def updated_count(self) -> int:
        return len(self.updated)

    @property
    def unchanged_count(self) -> int:
        return len(self.unchanged)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "errors": self.errors,
            "added_count": self.added_count,
            "updated_count": self.updated_count,
            "unchanged_count": self.unchanged_count,
            "skipped_count": self.skipped_count,
            "error_count": self.error_count,
        }
