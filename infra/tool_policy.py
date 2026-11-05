"""工具策略常量 —— 与模块无关的纯数据，供 thinking 与 security_system 共用。

放在 infra 而不是 modules/thinking：安全系统需要按层级查白名单，
若直接 import thinking.identity 就形成 security_system → thinking 的实依赖；
thinking 又反向依赖 security_system 的门控，两者构成环。
常量下沉到 infra 后，双方都只依赖 infra。
"""
from typing import Dict, List


DEFAULT_TOOL_WHITELISTS: Dict[str, List[str]] = {
    "large": [
        "web_search", "web_fetch", "memory_match", "event_query", "deep_recall",
        "knowledge_search", "knowledge_list", "knowledge_ingest", "knowledge_delete",
        "knowledge_reindex",
        "causal_graph_edit", "exec_command",
        "transcribe_audio", "understand_screen", "detect_ui_elements", "calc", "todo",
        "tools_search", "open_app",
        "mouse_click", "mouse_move", "mouse_double_click", "mouse_scroll", "mouse_drag",
        "keyboard_type", "keyboard_press", "keyboard_hotkey", "get_mouse_position",
        "mcp_discover", "mcp_call_tool", "mcp_server_status", "mcp_register_server",
        "create_tool", "list_my_tools", "delete_tool", "edit_tool",
        "create_skill", "*",
    ],
    "orchestrator": ["web_search", "web_fetch", "memory_match", "event_query", "deep_recall", "knowledge_search", "knowledge_list", "exec_command", "*"],
    "supervisor": [
        "web_search", "web_fetch", "memory_match", "event_query", "deep_recall",
        "knowledge_search", "knowledge_list",
        "causal_graph_edit",
        "directory_tree", "list_directory", "read_text_file", "todo",
    ],
    "expert_code_reviewer": ["memory_match", "event_query", "knowledge_search", "knowledge_list", "directory_tree", "list_directory", "read_text_file", "todo"],
    "code_reviewer": ["memory_match", "event_query", "knowledge_search", "knowledge_list", "directory_tree", "list_directory", "read_text_file", "todo"],
    "expert_code_writer": ["run_command", "write_text_file", "event_query", "knowledge_search", "knowledge_list", "directory_tree", "list_directory", "read_text_file", "todo"],
    "code_writer": ["run_command", "write_text_file", "event_query", "knowledge_search", "knowledge_list", "directory_tree", "list_directory", "read_text_file", "todo"],
    "expert_test_writer": ["run_pytest", "run_command", "write_text_file", "event_query", "knowledge_search", "knowledge_list", "directory_tree", "list_directory", "read_text_file", "todo"],
    "tester": ["run_pytest", "run_command", "write_text_file", "event_query", "knowledge_search", "knowledge_list", "directory_tree", "list_directory", "read_text_file", "todo"],
    "expert_data_analyzer": ["web_search", "memory_match", "event_query", "knowledge_search", "knowledge_list", "directory_tree", "list_directory", "read_text_file", "todo"],
    "expert_customer": ["event_query", "todo"],
    "expert_emotion": ["event_query", "todo"],
    "expert_creative_writer": ["event_query", "todo"],
    "expert_ui_designer": ["event_query", "knowledge_search", "knowledge_list", "web_fetch", "write_text_file", "directory_tree", "list_directory", "read_text_file", "todo"],
}


__all__ = ["DEFAULT_TOOL_WHITELISTS"]
