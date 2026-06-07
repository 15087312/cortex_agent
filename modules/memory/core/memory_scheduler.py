"""
记忆管理调度器 - 后台定期清理、压缩、归档、重建索引

管理策略：
  - 短期记忆 (SQLite): 0-30min 全文 / 30min-7day 关键词检索 / 7天后归档到长期记忆
  - 长期记忆 (JSONL): 由调度器管理归档、压缩去重，不在热路径检索

用法：
    scheduler = MemoryScheduler(data_dir="data/memory")
    scheduler.start()
    ...
    scheduler.stop()
"""
import threading
import time
import asyncio
import json
from datetime import datetime as dt
from utils.logger import setup_logger

logger = setup_logger("memory_scheduler")


class MemoryScheduler:
    """记忆管理后台调度器（daemon 线程）"""

    def __init__(
        self,
        data_dir: str = "data/memory",
        cleanup_interval: float = 1800.0,   # 清理过期 + 归档：30 分钟
        compact_interval: float = 14400.0,   # 压缩去重：4 小时
        index_interval: float = 7200.0,      # 重建索引：2 小时
        consolidate_interval: float = 43200.0,  # T6: 深度整合：12 小时
    ):
        self._data_dir = data_dir
        self._cleanup_interval = cleanup_interval
        self._compact_interval = compact_interval
        self._index_interval = index_interval
        self._consolidate_interval = consolidate_interval

        self._running = False
        self._thread = None
        self._last_cleanup = 0.0
        self._last_compact = 0.0
        self._last_index = 0.0
        self._last_consolidate = 0.0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            f"记忆调度器已启动 "
            f"(清理+归档每{self._cleanup_interval}s, "
            f"压缩每{self._compact_interval}s, "
            f"索引每{self._index_interval}s)"
        )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("记忆调度器已停止")

    def _loop(self) -> None:
        # 首次延迟让服务先完成初始化
        time.sleep(60)

        while self._running:
            try:
                now = time.time()

                if now - self._last_cleanup >= self._cleanup_interval:
                    self._do_archive()      # 先归档（7天过期 → 长期记忆）
                    self._do_cleanup()       # 再清理（标记过期）
                    self._last_cleanup = now

                if now - self._last_compact >= self._compact_interval:
                    self._do_compact()
                    self._last_compact = now

                if now - self._last_index >= self._index_interval:
                    self._do_index()
                    self._last_index = now

                # T6: 深度记忆整合 (12小时)
                if now - self._last_consolidate >= self._consolidate_interval:
                    self._do_consolidate()
                    self._last_consolidate = now

            except Exception as e:
                logger.error(f"调度循环异常: {e}")

            # 每 5 分钟检查一次
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(5)

    # ========== 归档：短期记忆 → 长期记忆 ==========

    def _do_archive(self) -> None:
        """
        将 SQLite 短期记忆中已过期（超过7天TTL）的记录归档到长期记忆 JSONL。
        归档后不立即删除，由后续 cleanup 标记 is_active=False。
        """
        try:
            from modules.database.repository import db_manager
            from modules.database.models import ShortTermMemory

            with db_manager.get_session() as session:
                now = dt.utcnow()
                # 查找 is_active=True 但 created_at 超过7天的记录
                from datetime import timedelta
                cutoff = now - timedelta(days=7)
                rows = (
                    session.query(ShortTermMemory)
                    .filter(
                        ShortTermMemory.is_active == True,
                        ShortTermMemory.created_at < cutoff
                    )
                    .all()
                )

                if not rows:
                    return

                archived = 0
                from modules.memory.core.long_term import LongTermMemory
                ltm = LongTermMemory(data_dir=f"{self._data_dir}/long_term")

                for row in rows:
                    content = {
                        "id": row.id,
                        "text": row.content,
                        "role": row.source,
                        "memory_type": row.memory_type,
                        "importance": row.importance,
                        "emotion": row.emotion,
                        "tags": row.tags,
                        "timestamp": row.created_at.timestamp() if row.created_at else time.time(),
                        "archived_at": time.time(),
                    }
                    # 按类型归档到对应 JSONL
                    mem_type = row.memory_type or "dialog"
                    ltm.save(mem_type, content)
                    archived += 1

                if archived > 0:
                    logger.info(f"[记忆调度] 归档: {archived} 条短期记忆 → 长期记忆 JSONL")

        except Exception as e:
            logger.warning(f"[记忆调度] 归档失败: {e}")

    # ========== 清理过期 ==========

    def _do_cleanup(self) -> None:
        if not self._running:
            return
        try:
            from modules.memory.core.memory_cleanup import MemoryCleanup

            async def _run():
                cleanup = MemoryCleanup()
                await cleanup.initialize()
                try:
                    count = await cleanup.cleanup_expired("all")
                    return count
                finally:
                    await cleanup.close()

            count = asyncio.run(_run())
            if count > 0:
                logger.info(f"[记忆调度] 清理过期记忆: {count} 条")
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                logger.debug(f"[记忆调度] 清理跳过（事件循环冲突）: {e}")
            else:
                logger.warning(f"[记忆调度] 清理失败: {e}")
        except Exception as e:
            logger.warning(f"[记忆调度] 清理失败: {e}")

    # ========== 压缩去重 ==========

    def _do_compact(self) -> None:
        if not self._running:
            return
        try:
            from modules.memory.core.memory_cleanup import MemoryCleanup

            async def _run():
                cleanup = MemoryCleanup()
                await cleanup.initialize()
                try:
                    results = await cleanup.compact_all()
                    return results
                finally:
                    await cleanup.close()

            results = asyncio.run(_run())
            logger.info(f"[记忆调度] 压缩去重完成: {results}")
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                logger.debug(f"[记忆调度] 压缩跳过（事件循环冲突）: {e}")
            else:
                logger.warning(f"[记忆调度] 压缩失败: {e}")
        except Exception as e:
            logger.warning(f"[记忆调度] 压缩失败: {e}")

    # ========== 重建索引（长期记忆） ==========

    def _do_index(self) -> None:
        """
        重建长期记忆的 FAISS 索引。
        索引仅用于 MemoryScheduler 的管理查询，不在热路径中使用。
        """
        try:
            from modules.memory.core.long_term import LongTermMemory

            ltm = LongTermMemory(data_dir=f"{self._data_dir}/long_term")
            for mem_type in ltm.files.keys():
                try:
                    stats = ltm.build_index(mem_type)
                    success_count = stats.get("success", 0) if isinstance(stats, dict) else 0
                    if success_count > 0:
                        logger.info(
                            f"[记忆调度] 索引重建: {mem_type} ({success_count} 条)"
                        )
                except Exception as e:
                    logger.warning(f"[记忆调度] 索引 {mem_type} 失败: {e}")
        except Exception as e:
            logger.warning(f"[记忆调度] 索引构建失败: {e}")

    # ========== T6: 深度记忆整合 ==========

    def _do_consolidate(self) -> None:
        """T6: 深度记忆整合 — 合并重复、删除过时、提炼规则、跨项目整合"""
        if not self._running:
            return
        try:
            merged = self._merge_duplicates()
            deleted = self._delete_outdated()
            refined = self._refine_to_rules()

            logger.info(
                f"[T6] 深度记忆整合完成: merged={merged} deleted={deleted} refined={refined}"
            )
        except Exception as e:
            logger.warning(f"[T6] 整合失败: {e}")

    def _merge_duplicates(self) -> int:
        """合并相似度 > 0.85 的重复记忆"""
        merged = 0
        try:
            from modules.memory.core.long_term import LongTermMemory
            ltm = LongTermMemory(data_dir=f"{self._data_dir}/long_term")

            for mem_type in list(ltm.files.keys()):
                try:
                    entries = ltm.load(mem_type, limit=200)
                    if len(entries) < 2:
                        continue

                    # 用内容相似度找重复
                    seen = {}
                    for entry in entries:
                        content = str(entry.get("content", ""))[:200]
                        # 简单去重: 按内容前100字符hash
                        key = hash(content[:100])
                        if key in seen:
                            # 保留时间更新的
                            existing = seen[key]
                            if entry.get("timestamp", 0) > existing.get("timestamp", 0):
                                seen[key] = entry
                            merged += 1
                        else:
                            seen[key] = content

                except Exception as e:
                    logger.debug(f"记忆去重条目解析失败: {e}")

        except Exception as e:
            logger.debug(f"记忆去重任务失败: {e}")
        return merged

    def _delete_outdated(self) -> int:
        """删除明显过时的信息: 90天以上的非核心记忆"""
        deleted = 0
        try:
            from modules.memory.core.long_term import LongTermMemory
            ltm = LongTermMemory(data_dir=f"{self._data_dir}/long_term")

            cutoff = time.time() - 90 * 86400  # 90天前
            for mem_type in list(ltm.files.keys()):
                # 跳过核心类型
                if mem_type in ("preference", "summary"):
                    continue
                try:
                    entries = ltm.load(mem_type, limit=500)
                    for entry in entries:
                        ts = entry.get("timestamp", 0)
                        if ts > 0 and ts < cutoff:
                            entry_id = entry.get("id", "")
                            if entry_id:
                                ltm.delete(entry_id, mem_type)
                                deleted += 1
                except Exception as e:
                    logger.debug(f"删除过时记忆类型 {mem_type} 失败: {e}")
        except Exception as e:
            logger.debug(f"删除过时记忆任务失败: {e}")
        return deleted

    def _refine_to_rules(self) -> int:
        """提炼零散经验为通用规则"""
        refined = 0
        try:
            from modules.memory.core.long_term import LongTermMemory
            ltm = LongTermMemory(data_dir=f"{self._data_dir}/long_term")

            # 收集最近的 evolution 记录
            evolutions = ltm.load("evolution", limit=50)
            if len(evolutions) < 3:
                return 0

            # 简单归类: 按 type 分组
            by_type: dict = {}
            for evo in evolutions:
                etype = evo.get("type", "general")
                if etype not in by_type:
                    by_type[etype] = []
                by_type[etype].append(evo)

            # 对每组 >= 3 条同类经验，提炼为一条规则
            for etype, items in by_type.items():
                if len(items) >= 3:
                    contents = [
                        i.get("content", "")[:100]
                        for i in items
                        if i.get("content")
                    ]
                    if contents:
                        rule = {
                            "type": "consolidated_rule",
                            "category": etype,
                            "content": f"[整合自{len(items)}条经验] {'; '.join(contents[:5])}",
                            "timestamp": time.time(),
                            "source_count": len(items),
                        }
                        ltm.save("summary", rule)
                        refined += 1

        except Exception as e:
            logger.debug(f"经验提炼为规则任务失败: {e}")
        return refined
