"""知识摄取 — 可配置多根目录白名单 + 哈希增量 + 索引维护

根目录白名单由 settings.KNOWLEDGE_ROOTS 提供（换行/逗号/分号分隔，相对路径按项目根
解析，也可直接写单个文件精确授权）。ingest 只接受解析后落在白名单之内的路径，
防路径穿越与误摄取。

增量规则：
    同 source_path 且 content_hash 未变 → 跳过（只刷新 indexed_at）
    content_hash 变了     → version+1，删旧向量与旧片段，重新切分嵌入
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from config.settings import settings
from utils.logger import setup_logger
from modules.knowledge.chunker import (
    chunk_document, chunk_embedding_text, derive_title, detect_mime, normalize_text,
)
from modules.knowledge.models import (
    IngestResult, KnowledgeChunk, KnowledgeDoc,
)
from modules.knowledge.store import KnowledgeStore

logger = setup_logger("knowledge_ingestor")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".py", ".json", ".yaml", ".yml", ".toml"}
_IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".venv311", ".mypy_cache", ".pytest_cache", "dist", "build", ".idea", ".vscode",
}
_ROOT_SPLIT = re.compile(r"[\n,;]+")


# ---------------------------------------------------------------------------
# 根目录白名单
# ---------------------------------------------------------------------------

def parse_roots(spec: Optional[str] = None) -> List[Path]:
    """把配置串解析为绝对路径列表（不去重、不校验存在性）。"""
    raw = settings.KNOWLEDGE_ROOTS if spec is None else spec
    items = _ROOT_SPLIT.split(raw or "")
    roots: List[Path] = []
    for item in items:
        item = item.strip().strip('"').strip("'")
        if not item:
            continue
        p = Path(os.path.expanduser(item))
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        roots.append(p.resolve())
    return roots


def resolve_knowledge_roots(spec: Optional[str] = None, warn: bool = True) -> List[Path]:
    """解析并过滤出真实存在的白名单根（保序去重）。"""
    seen, roots = set(), []
    for p in parse_roots(spec):
        if p in seen:
            continue
        seen.add(p)
        if p.exists():
            roots.append(p)
        elif warn:
            logger.warning(f"[knowledge] 知识库根目录不存在，已忽略: {p}")
    return roots


def is_path_allowed(path: str | Path, roots: Optional[Sequence[Path]] = None) -> bool:
    """路径是否落在白名单内（单个文件条目按全等匹配，目录条目按子孙匹配）。"""
    try:
        target = Path(path).resolve()
    except OSError:
        return False
    for root in (roots if roots is not None else resolve_knowledge_roots(warn=False)):
        try:
            if root.is_file():
                if target == root:
                    return True
            elif target == root or target.is_relative_to(root):
                return True
        except (OSError, ValueError):
            continue
    return False


def _display_path(path: Path) -> str:
    """库内 source_path：项目内用相对路径，项目外保留绝对路径。"""
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            yield Path(dirpath) / name


def _read_text(path: Path) -> Optional[str]:
    """读文本文件；含 NUL 视为二进制返回 None。"""
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


class KnowledgeIngestor:
    """文件摄取器（扫描 / 增量 / 删除 / 重建）"""

    def __init__(self, store: Optional[KnowledgeStore] = None, roots: Optional[str] = None):
        self._store = store
        self._roots_spec = roots

    @property
    def store(self) -> KnowledgeStore:
        if self._store is None:
            self._store = KnowledgeStore.get_instance()
        return self._store

    def roots(self) -> List[Path]:
        return resolve_knowledge_roots(self._roots_spec)

    def scan(self) -> List[Path]:
        """列出白名单内所有受支持的文件。"""
        files: List[Path] = []
        for root in self.roots():
            for p in _iter_files(root):
                if p.suffix.lower() in SUPPORTED_EXTS:
                    files.append(p)
        return files

    # @link:block Knowledge_Ingest = Write
    def ingest_path(self, path: str | Path) -> IngestResult:
        """摄取单个文件或目录（目录递归）。路径必须在白名单内。"""
        result = IngestResult()
        target = Path(path)
        if not target.is_absolute():
            target = (PROJECT_ROOT / target)
        try:
            target = target.resolve()
        except OSError as e:
            result.errors.append({"path": str(path), "error": f"路径解析失败: {e}"})
            return result

        roots = self.roots()
        if not roots:
            result.errors.append({
                "path": str(target),
                "error": "知识库根目录白名单为空或均不存在，请检查 KNOWLEDGE_ROOTS 配置",
            })
            return result
        if not is_path_allowed(target, roots):
            result.errors.append({
                "path": str(target),
                "error": "路径不在知识库白名单内（KNOWLEDGE_ROOTS）",
                "allowed_roots": [str(r) for r in roots],
            })
            return result
        if not target.exists():
            result.errors.append({"path": str(target), "error": "路径不存在"})
            return result

        files = [target] if target.is_file() else [
            p for p in _iter_files(target) if p.suffix.lower() in SUPPORTED_EXTS
        ]
        for f in files:
            self._ingest_one(f, result)
        return result

    def ingest_roots(self) -> IngestResult:
        """按白名单全量增量摄取（未变化的文件自动跳过）。"""
        result = IngestResult()
        roots = self.roots()
        if not roots:
            result.errors.append({
                "path": (self._roots_spec or settings.KNOWLEDGE_ROOTS),
                "error": "知识库根目录白名单为空或均不存在，请检查 KNOWLEDGE_ROOTS 配置",
            })
            return result
        for f in self.scan():
            self._ingest_one(f, result)
        return result

    # @link:block Knowledge_IngestOne = Write
    def _ingest_one(self, path: Path, result: IngestResult) -> None:
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            result.skipped.append({"path": str(path), "reason": f"不支持的类型 {ext}"})
            return
        try:
            size = path.stat().st_size
        except OSError as e:
            result.errors.append({"path": str(path), "error": f"stat 失败: {e}"})
            return
        if size > int(getattr(settings, "KNOWLEDGE_MAX_FILE_BYTES", 5_000_000)):
            result.skipped.append({"path": str(path), "reason": f"文件过大 {size} bytes"})
            return

        raw = _read_text(path)
        if raw is None:
            result.skipped.append({"path": str(path), "reason": "二进制文件，已跳过"})
            return
        text = normalize_text(raw)
        if not text.strip():
            result.skipped.append({"path": str(path), "reason": "内容为空"})
            return

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source_path = _display_path(path)
        store = self.store
        existing = store.get_document_by_path(source_path)

        if existing and existing.content_hash == content_hash:
            store.touch_indexed(existing.id)
            result.unchanged.append({"id": existing.id, "source_path": source_path})
            return

        title = derive_title(text, str(path))
        try:
            drafts = chunk_document(
                text,
                source_path=source_path,
                target_tokens=int(getattr(settings, "KNOWLEDGE_CHUNK_TOKENS", 400)),
                overlap_tokens=int(getattr(settings, "KNOWLEDGE_CHUNK_OVERLAP", 80)),
                min_tokens=int(getattr(settings, "KNOWLEDGE_MIN_CHUNK_TOKENS", 20)),
                max_chunks=int(getattr(settings, "KNOWLEDGE_MAX_CHUNKS_PER_FILE", 5000)),
            )
        except ValueError as e:
            result.errors.append({"path": str(path), "error": str(e)})
            return
        if not drafts:
            result.skipped.append({"path": str(path), "reason": "切分后无有效内容"})
            return

        # 内容变更：先清旧向量，再覆盖片段与元数据
        if existing:
            old_ids = [c.id for c in store.list_chunks_by_doc(existing.id)]
            store.remove_vectors(old_ids)

        doc = KnowledgeDoc(
            id=existing.id if existing else "",
            title=title,
            source_path=source_path,
            content_hash=content_hash,
            version=(existing.version + 1) if existing else 1,
            mime=detect_mime(str(path)),
            size_bytes=size,
            chunk_count=len(drafts),
        )
        doc_id = store.upsert_document(doc)
        chunks: List[KnowledgeChunk] = store.replace_chunks(doc_id, drafts)

        embedded = self._embed_chunks(chunks, title)
        entry = {
            "id": doc_id,
            "title": title,
            "source_path": source_path,
            "chunks": len(chunks),
            "vectors": embedded,
            "version": doc.version,
        }
        (result.updated if existing else result.added).append(entry)
        logger.info(
            f"[knowledge] {'更新' if existing else '入库'} {source_path} "
            f"({len(chunks)} 块, {embedded} 向量, v{doc.version})"
        )

    def _embed_chunks(self, chunks: Sequence[KnowledgeChunk], title: str) -> int:
        if not chunks:
            return 0
        texts = [chunk_embedding_text(title, c.heading_path, c.text) for c in chunks]
        vectors = self.store.embed_texts(texts)
        return self.store.add_vectors([c.id for c in chunks], vectors)

    # ------------------------------------------------------------------
    # 删除 / 重建
    # ------------------------------------------------------------------

    def delete(self, doc_id: str) -> Dict[str, Any]:
        return self.store.delete_document(doc_id)

    def reindex(self, full: bool = False) -> Dict[str, Any]:
        """full=False：按哈希做增量摄取；full=True：清空向量后整库重嵌。"""
        if not full:
            return {"mode": "incremental", **self.ingest_roots().to_dict()}

        store = self.store
        store.reset_index()
        total_vec = 0
        docs = store.list_documents(limit=100000)
        for doc in docs:
            chunks = store.list_chunks_by_doc(doc.id)
            if chunks:
                total_vec += self._embed_chunks(chunks, doc.title)
            store.touch_indexed(doc.id)
        return {"mode": "full", "documents": len(docs), "vectors": total_vec}


def get_ingestor() -> KnowledgeIngestor:
    return KnowledgeIngestor()


__all__ = [
    "KnowledgeIngestor", "get_ingestor", "resolve_knowledge_roots",
    "parse_roots", "is_path_allowed", "SUPPORTED_EXTS", "PROJECT_ROOT",
]
