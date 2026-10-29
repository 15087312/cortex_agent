"""知识库启动预热 — 后台增量摄取 KNOWLEDGE_ROOTS

为什么需要：
    知识库为空时，模型调用 knowledge_search 只能拿到空结果，工具形同虚设。
    应用启动时在后台按文件哈希做一次增量摄取（内容未变的文件直接跳过），
    把配置的文档目录同步进库，模型第一次查询就有内容可引用。

约束：
    - 不阻塞启动：daemon 线程执行，异常只告警，绝不影响应用可用性
    - 幂等：进程内只执行一次（重复调用返回 None）
    - 尊重开关：KNOWLEDGE_ENABLED / KNOWLEDGE_AUTO_INGEST 任一为 False 都不启动
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("knowledge_warmup")

_thread: Optional[threading.Thread] = None
_started = False
_lock = threading.Lock()


def ingest_once(service: Any = None) -> Dict[str, Any]:
    """同步执行一次白名单全量增量摄取（供启动线程与测试直接调用）。"""
    if service is None:
        from modules.knowledge.service import get_knowledge_service
        service = get_knowledge_service()
    result = service.ingest("")
    return result if isinstance(result, dict) else {}


def _log_result(result: Dict[str, Any]) -> None:
    if result.get("error"):
        logger.warning(f"[knowledge] 启动增量摄取未完成: {result['error']}")
        return
    added = result.get("added_count", 0)
    updated = result.get("updated_count", 0)
    unchanged = result.get("unchanged_count", 0)
    skipped = result.get("skipped_count", 0)
    errors = result.get("errors") or []
    logger.info(
        f"[knowledge] 启动增量摄取完成: 新增 {added} / 更新 {updated} / "
        f"未变 {unchanged} / 跳过 {skipped} / 错误 {len(errors)}"
    )
    if errors:
        preview = "; ".join(str(e.get("path", "")) for e in errors[:3] if isinstance(e, dict))
        logger.warning(f"[knowledge] 摄取错误示例: {preview}")


def _is_enabled() -> bool:
    if not getattr(settings, "KNOWLEDGE_ENABLED", True):
        return False
    return bool(getattr(settings, "KNOWLEDGE_AUTO_INGEST", True))


def start_background_ingest(service: Any = None) -> Optional[threading.Thread]:
    """启动后台增量摄取；已启动/开关关闭时返回 None。"""
    global _thread, _started

    if not _is_enabled():
        logger.debug("[knowledge] 启动增量摄取已关闭（KNOWLEDGE_ENABLED/KNOWLEDGE_AUTO_INGEST）")
        return None

    with _lock:
        if _started:
            return None

        def _run() -> None:
            try:
                _log_result(ingest_once(service))
            except Exception as e:
                # 预热失败不影响应用启动；模型仍可显式调用 knowledge_ingest 重试
                logger.warning(f"[knowledge] 启动增量摄取失败 (非致命): {e}")

        thread = threading.Thread(target=_run, name="knowledge-ingest", daemon=True)
        try:
            thread.start()
        except Exception as e:
            logger.warning(f"[knowledge] 启动增量摄取线程创建失败 (非致命): {e}")
            return None
        _thread = thread
        _started = True
        return thread


def reset_for_tests() -> None:
    """重置进程内状态（仅测试 / 配置热更后手动重启用）。"""
    global _thread, _started
    with _lock:
        _thread = None
        _started = False


__all__ = ["ingest_once", "start_background_ingest", "reset_for_tests"]
