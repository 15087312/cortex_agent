"""知识库工具 — 大模型主动查阅与维护项目知识

与记忆工具的分工：
- event_query / memory_match 查「经历过什么」（历史对话、经验、决策）
- knowledge_search 查「文档里怎么写」（规范、接口、配置、架构说明）
两者不互相替代；回答需要出处的问题时应优先查知识库。

权限约定：查询类 LOW（免审批），写入类 MEDIUM（走安全门审批）。
能力经 service_registry("knowledge_base") 获取，本模块不直接 import modules。
"""
import json
from typing import Any, Dict

from infra.tool_manager.tool_registry import ToolRegistry
from infra.tool_manager.service_registry import get_capability
from utils.logger import setup_logger

logger = setup_logger("knowledge_tools")

# MCP 层对 str 类型工具结果按 4000 字符硬截断（combined_provider.MAX_TOOL_RESULT_CHARS），
# 超长 JSON 会被截成非法字符串，模型只能拿到半截对象。知识库结果天然可能超长
# （多命中 / 多文档 / 多错误），因此统一在工具内按预算收缩，留 400 字符余量。
_MAX_JSON_CHARS = 3600

# 命中条目的元数据字段；正文由 rendered 统一承载，避免同一段文本在 JSON 里出现两次
# （重复会把默认 top_k=5 的结果直接顶到 4000 字符上限）。
_HIT_META_KEYS = (
    "chunk_id", "doc_id", "title", "heading_path", "source_path",
    "score", "char_start", "char_end",
)


def _encode(payload: Dict[str, Any], limit: int = _MAX_JSON_CHARS) -> str:
    """序列化为不超过 limit 的**合法** JSON。

    收缩顺序：先截 rendered 长文本，再从最长的列表尾部删元素并标记 truncated。
    保证模型拿到的永远是完整可解析的对象，而不是被硬截断的半截字符串。
    """
    data = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    originals = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text

    rendered = data.get("rendered")
    if isinstance(rendered, str) and len(rendered) > 400:
        data["rendered"] = rendered[: max(200, len(rendered) - (len(text) - limit) - 100)]
        data["truncated"] = True
        text = json.dumps(data, ensure_ascii=False, indent=2)

    for _ in range(200):
        if len(text) <= limit:
            break
        lists = [(k, v) for k, v in data.items() if isinstance(v, list) and v]
        if not lists:
            break
        key, items = max(lists, key=lambda kv: len(json.dumps(kv[1], ensure_ascii=False)))
        data[key] = items[:-1]
        data["truncated"] = True
        if key in ("hits", "documents"):
            data["count"] = len(data[key])
            data["total"] = originals.get(key, len(data[key]))
        count_key = f"{key}_count"
        if isinstance(data.get(count_key), int):
            data[count_key] = len(data[key])
        text = json.dumps(data, ensure_ascii=False, indent=2)

    if len(text) > limit:
        return json.dumps({
            "truncated": True,
            "hint": "结果过大，请缩小 top_k / limit 或指定 path_prefix 后重试",
        }, ensure_ascii=False)
    return text


def _service():
    factory = get_capability("knowledge_base")
    return factory() if factory else None


def _unavailable() -> str:
    return json.dumps({
        "error": "知识库能力未注册（bootstrap 未加载）",
        "hint": "请确认应用经 bootstrap.register_business_capabilities() 启动",
    }, ensure_ascii=False)


@ToolRegistry.register(
    name="knowledge_search",
    description=(
        "检索项目知识库（文档/规范/接口说明），返回带出处的原文片段。"
        "当问题涉及「文档里怎么写的」「接口参数」「配置项含义」「架构设计」时使用；"
        "回答时请标注来源文档与章节。查历史经历请用 event_query。"
    ),
    params={
        "query": "检索问题或关键词（自然语言即可）",
        "top_k": "返回条数，默认5（最多20）",
        "path_prefix": "可选，只检索路径以此开头的文档（如 docs/）",
    },
    source="builtin",
    risk_level="LOW",
    category="query",
    tags=["knowledge", "rag"],
    core=True,
)
def knowledge_search(query: str, top_k: str = "5", path_prefix: str = "") -> str:
    """检索知识库 — 返回 JSON（hits + 渲染文本）"""
    svc = _service()
    if svc is None:
        return _unavailable()
    try:
        k = max(1, min(20, int(top_k or 5)))
        hits = svc.search(query, top_k=k, path_prefix=path_prefix.strip())
        if not hits:
            return _encode({
                "hits": [], "count": 0,
                "hint": "知识库中未找到相关内容；可尝试 knowledge_list 查看已入库文档，或改用 event_query 查历史记忆",
            })
        metas = [{key: h.get(key) for key in _HIT_META_KEYS} for h in hits]
        return _encode({
            "hits": metas,
            "count": len(metas),
            "rendered": svc.search_text(query, top_k=k, path_prefix=path_prefix.strip()),
        })
    except Exception as e:
        logger.warning(f"[knowledge_search] 失败: {e}")
        return json.dumps({"error": str(e), "hits": [], "count": 0}, ensure_ascii=False)


@ToolRegistry.register(
    name="knowledge_list",
    description="列出知识库已入库的文档（标题/路径/块数/更新时间），用于确认某份文档是否已摄取。",
    params={"limit": "返回条数，默认50（最多200）"},
    source="builtin",
    risk_level="LOW",
    category="query",
    tags=["knowledge", "rag"],
    core=False,
)
def knowledge_list(limit: str = "50") -> str:
    """列出已入库文档"""
    svc = _service()
    if svc is None:
        return _unavailable()
    try:
        n = max(1, min(200, int(limit or 50)))
        docs = svc.list_docs(limit=n)
        return _encode({
            "documents": docs,
            "count": len(docs),
            "stats": svc.stats(),
        })
    except Exception as e:
        logger.warning(f"[knowledge_list] 失败: {e}")
        return json.dumps({"error": str(e), "documents": [], "count": 0}, ensure_ascii=False)


@ToolRegistry.register(
    name="knowledge_ingest",
    description=(
        "把文件或目录摄取进知识库（切分 + 向量化）。路径必须落在 KNOWLEDGE_ROOTS 白名单内；"
        "内容未变的文件自动跳过，内容变更会升版本并重建该文件的片段。path 留空则按白名单全量增量摄取。"
    ),
    params={
        "path": "可选，文件或目录路径（相对项目根）；留空=按白名单全量增量摄取",
    },
    source="builtin",
    risk_level="MEDIUM",
    category="mutation",
    tags=["knowledge", "rag"],
    core=False,
)
def knowledge_ingest(path: str = "") -> str:
    """摄取文件/目录到知识库"""
    svc = _service()
    if svc is None:
        return _unavailable()
    try:
        return _encode(svc.ingest(path.strip()))
    except Exception as e:
        logger.warning(f"[knowledge_ingest] 失败: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@ToolRegistry.register(
    name="knowledge_delete",
    description="从知识库删除一份文档（连同其片段与向量）。doc_id 可先用 knowledge_list 查询。",
    params={"doc_id": "文档 ID（kd_ 开头）"},
    source="builtin",
    risk_level="MEDIUM",
    category="mutation",
    tags=["knowledge", "rag"],
    core=False,
)
def knowledge_delete(doc_id: str) -> str:
    """删除知识库文档"""
    svc = _service()
    if svc is None:
        return _unavailable()
    try:
        if not doc_id or not doc_id.strip():
            return json.dumps({"error": "需要 doc_id"}, ensure_ascii=False)
        return _encode(svc.delete(doc_id.strip()))
    except Exception as e:
        logger.warning(f"[knowledge_delete] 失败: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@ToolRegistry.register(
    name="knowledge_reindex",
    description=(
        "维护知识库索引。full=false 按文件哈希做增量摄取（默认）；"
        "full=true 清空向量索引并按库内片段整库重嵌（embedding 维度变更后使用，较慢）。"
    ),
    params={"full": "true/false，默认 false"},
    source="builtin",
    risk_level="MEDIUM",
    category="mutation",
    tags=["knowledge", "rag"],
    core=False,
)
def knowledge_reindex(full: str = "false") -> str:
    """重建知识库索引"""
    svc = _service()
    if svc is None:
        return _unavailable()
    try:
        is_full = str(full).strip().lower() in ("true", "1", "yes", "full")
        return _encode(svc.reindex(full=is_full))
    except Exception as e:
        logger.warning(f"[knowledge_reindex] 失败: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
