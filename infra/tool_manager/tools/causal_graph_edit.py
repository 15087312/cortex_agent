"""因果图编辑工具 — 大模型自主修正错误的因果关系

深度回忆(deep_recall)输出的因果链可能包含错误的因果关联（模型臆造 / 共现误判）。
此工具允许大模型删除错误的因果边或因果节点，修正因果图。

设计：
- delete_edge: 按 from/to 节点标签删除一条因果边（消除错误因果关系）
- delete_node: 按标签删除因果节点（连带其所有边，并解除事件关联）
"""
import json

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("causal_graph_edit")


def _resolve_nodes(graph, label: str):
    """按标签模糊查找节点，返回节点列表（可能多个匹配）"""
    if not label or not label.strip():
        return []
    return graph.find_nodes_by_label(label.strip())


def _clean_events_of_nodes(removed_ids: set) -> int:
    """解除事件与被删除节点的因果关联，返回清理的事件数"""
    from modules.memory.event_store import EventStore
    store = EventStore.get_instance()
    cleaned = 0
    for ev in store.list_events(limit=1000):
        ids = [x for x in (ev.causal_node_ids or []) if x not in removed_ids]
        if ids != (ev.causal_node_ids or []):
            ev.causal_node_ids = ids
            store.save_event(ev)
            cleaned += 1
    return cleaned


@ToolRegistry.register(
    name="causal_graph_edit",
    description=(
        "编辑因果图，修正错误的因果关系。当深度回忆(deep_recall)给出的因果链是错的时，"
        "用此工具删除错误的因果边或因果节点。delete_node 会连带删除该节点的所有边，"
        "并解除事件与该节点的关联。删除是持久化的，请先确认因果关系确实错误再操作。"
    ),
    params={
        "action": "操作类型：delete_edge=删除一条因果边；delete_node=删除一个因果节点",
        "from_label": "delete_edge 必填：因节点标签（如：需求频繁变更）",
        "to_label": "delete_edge 必填：果节点标签（如：项目延期）",
        "node_label": "delete_node 必填：要删除的节点标签（如：人手不足）",
    },
    risk_level="MEDIUM",
    category="mutation",
    core=True,
    tags=["memory", "causal"],
)
def causal_graph_edit(action: str = "", from_label: str = "", to_label: str = "",
                      node_label: str = "", **kwargs) -> str:
    """编辑因果图 — 返回 JSON 字符串"""
    try:
        from modules.memory.causal_graph import CausalGraph

        graph = CausalGraph.get_instance()
        action = (action or "").strip()

        if action == "delete_edge":
            if not (from_label and from_label.strip()) or not (to_label and to_label.strip()):
                return json.dumps({"error": "delete_edge 需要同时提供 from_label 和 to_label"},
                                  ensure_ascii=False)
            from_nodes = _resolve_nodes(graph, from_label)
            to_nodes = _resolve_nodes(graph, to_label)
            if not from_nodes or not to_nodes:
                return json.dumps({
                    "error": "未找到标签匹配的节点",
                    "from_matches": [n.label for n in from_nodes],
                    "to_matches": [n.label for n in to_nodes],
                }, ensure_ascii=False)

            from_ids = {n.id for n in from_nodes}
            to_ids = {n.id for n in to_nodes}
            deleted = []
            for edge in graph.list_all_edges():
                if edge.from_id in from_ids and edge.to_id in to_ids:
                    if graph.delete_edge(edge.id):
                        deleted.append({
                            "id": edge.id,
                            "from": graph.get_node(edge.from_id).label if graph.get_node(edge.from_id) else edge.from_id,
                            "to": graph.get_node(edge.to_id).label if graph.get_node(edge.to_id) else edge.to_id,
                            "confidence": edge.confidence,
                        })
            if not deleted:
                return json.dumps({
                    "error": "未找到该方向的因果边",
                    "hint": "请确认方向（from_label 是因，to_label 是果），或检查标签是否与深度回忆输出一致",
                }, ensure_ascii=False)
            return json.dumps({
                "success": True,
                "deleted_edges": deleted,
                "count": len(deleted),
            }, ensure_ascii=False)

        elif action == "delete_node":
            if not (node_label and node_label.strip()):
                return json.dumps({"error": "delete_node 需要提供 node_label"}, ensure_ascii=False)
            nodes = _resolve_nodes(graph, node_label)
            if not nodes:
                return json.dumps({"error": f"未找到标签匹配的节点: {node_label}"}, ensure_ascii=False)

            deleted = []
            removed_ids = set()
            for n in nodes:
                if graph.delete_node(n.id):
                    deleted.append({"id": n.id, "label": n.label})
                    removed_ids.add(n.id)
            cleaned = _clean_events_of_nodes(removed_ids) if removed_ids else 0
            return json.dumps({
                "success": True,
                "deleted_nodes": deleted,
                "count": len(deleted),
                "cleaned_events": cleaned,
            }, ensure_ascii=False)

        return json.dumps({
            "error": f"未知 action: {action}",
            "supported": ["delete_edge", "delete_node"],
        }, ensure_ascii=False)

    except Exception as e:
        logger.warning(f"[causal_graph_edit] 失败: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
