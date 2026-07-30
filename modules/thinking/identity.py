"""
模型身份 — 每个模型实例的独立记忆、权限、工具白名单

三层模型架构:
- large:     大模型 — 全局调度、价值观决策
- supervisor: 主管模型 — 领域任务编排、质量把控
- expert:     专家模型 — 具体子任务执行

角色人格模板从 config/prompts/roles.yaml 加载，支持外部 YAML 覆盖。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
from utils.logger import get_logger

logger = get_logger(__name__)


class ModelTier(str, Enum):
    LARGE = "large"
    SUPERVISOR = "supervisor"
    EXPERT = "expert"


# 外部 YAML 覆盖层
_merged_identities: Optional[Dict[str, dict]] = None


def get_identities() -> Dict[str, dict]:
    """获取合并后的身份字典（config/prompts/roles.yaml + 外部覆盖）"""
    global _merged_identities
    if _merged_identities is not None:
        return _merged_identities
    return _load_from_yaml()


def _load_from_yaml() -> Dict[str, dict]:
    """从 config/prompts/roles.yaml 加载角色模板"""
    try:
        from config.prompts.loader import get_loader
        loader = get_loader()
        data = loader.load("roles") or {}
        roles = data.get("roles", {})
        if roles:
            return roles
    except Exception as e:
        logger.warning(f"[Identity] 从 YAML 加载角色失败，使用空配置: {e}")
    return {}


def load_external_identities(directory: str = None) -> Dict[str, dict]:
    """加载外部 YAML 配置并合并"""
    global _merged_identities
    try:
        from modules.thinking.identity_loader import load_and_merge
        _merged_identities = load_and_merge(_load_from_yaml(), directory)
        logger.info(f"[Identity] 外部身份加载完成: {len(_merged_identities)} 个")
    except Exception as e:
        logger.warning(f"[Identity] 外部身份加载失败: {e}")
        _merged_identities = _load_from_yaml()
    return _merged_identities


# ── 默认工具白名单（按层级 + 角色）──

DEFAULT_TOOL_WHITELISTS: Dict[str, List[str]] = {
    "large": [
        "web_search", "web_fetch", "memory_match", "event_query", "exec_command",
        "transcribe_audio", "understand_screen", "detect_ui_elements", "calc", "todo",
        "tools_search", "open_app",
        "mouse_click", "mouse_move", "mouse_double_click", "mouse_scroll", "mouse_drag",
        "keyboard_type", "keyboard_press", "keyboard_hotkey", "get_mouse_position",
        "mcp_discover", "mcp_call_tool", "mcp_server_status", "mcp_register_server",
        "create_tool", "list_my_tools", "delete_tool", "edit_tool",
        "create_skill", "*",
    ],
    "orchestrator": ["web_search", "web_fetch", "memory_match", "event_query", "exec_command", "*"],
    "supervisor": [
        "web_search", "web_fetch", "memory_match", "event_query",
        "directory_tree", "list_directory", "read_text_file",
    ],
    "expert_code_reviewer": ["memory_match", "event_query", "directory_tree", "list_directory", "read_text_file"],
    "code_reviewer": ["memory_match", "event_query", "directory_tree", "list_directory", "read_text_file"],
    "expert_code_writer": ["run_command", "event_query", "directory_tree", "list_directory", "read_text_file"],
    "code_writer": ["run_command", "event_query", "directory_tree", "list_directory", "read_text_file"],
    "expert_test_writer": ["run_pytest", "run_command", "event_query", "directory_tree", "list_directory", "read_text_file"],
    "tester": ["run_pytest", "run_command", "event_query", "directory_tree", "list_directory", "read_text_file"],
    "expert_data_analyzer": ["web_search", "memory_match", "event_query", "directory_tree", "list_directory", "read_text_file"],
    "expert_customer": ["event_query"],
    "expert_emotion": ["event_query"],
    "expert_creative_writer": ["event_query"],
    "expert_ui_designer": ["event_query", "web_fetch", "directory_tree", "list_directory", "read_text_file"],
}


# ── 专家启动模式 ──

DEFAULT_STARTUP_MODES: Dict[str, str] = {
    "large": "on_demand",
    "expert_customer": "on_demand",
    "expert_emotion": "on_demand",
}


# ── 默认技能（启动时自动注入 system prompt）──

DEFAULT_SKILL_IDS: Dict[str, str] = {
    "ui_designer": "ui_design",
}


def get_startup_mode(template_key: str) -> str:
    return DEFAULT_STARTUP_MODES.get(template_key, "on_demand")


def list_persistent_experts() -> list:
    return [k for k, v in DEFAULT_STARTUP_MODES.items() if v == "persistent"]


# ── 权限配置 ──

@dataclass
class ModelPermissions:
    """模型权限 — 集中管理一个模型的所有能力边界"""
    can_start_probes: bool = False
    can_stop_probes: bool = False
    controllable_tiers: List[str] = field(default_factory=list)
    can_write_memory: bool = False
    can_inject_persona: bool = False
    allowed_tool_categories: List[str] = field(default_factory=lambda: ["query"])
    requires_tool_approval: bool = False
    can_delegate: bool = False
    delegatable_tiers: List[str] = field(default_factory=list)
    max_instances: int = 1
    max_concurrent_runners: int = 1

    def can_control_tier(self, target_tier: str) -> bool:
        return target_tier in self.controllable_tiers

    def can_delegate_to(self, target_tier: str) -> bool:
        return self.can_delegate and target_tier in self.delegatable_tiers

    def can_use_tool_category(self, category: str) -> bool:
        return category in self.allowed_tool_categories


DEFAULT_PERMISSIONS: Dict[str, ModelPermissions] = {
    # ── 大模型 ──
    "large": ModelPermissions(
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=["supervisor", "expert"],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query", "mutation", "admin", "perception", "memory"],
        can_delegate=True, delegatable_tiers=["supervisor", "expert"],
        max_instances=1, max_concurrent_runners=1,
    ),
    "orchestrator": ModelPermissions(  # alias for large
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=["supervisor", "expert"],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query", "mutation", "admin", "perception", "memory"],
        can_delegate=True, delegatable_tiers=["supervisor", "expert"],
        max_instances=1, max_concurrent_runners=1,
    ),

    # ── 主管 ──
    "supervisor_code": ModelPermissions(
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=["expert"],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query", "mutation"],
        can_delegate=True, delegatable_tiers=["expert"],
        max_instances=1, max_concurrent_runners=1,
    ),
    "code_supervisor": ModelPermissions(  # alias
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=["expert"],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query", "mutation"],
        can_delegate=True, delegatable_tiers=["expert"],
        max_instances=1, max_concurrent_runners=1,
    ),
    "supervisor_query": ModelPermissions(
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=["expert"],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query"],
        can_delegate=True, delegatable_tiers=["expert"],
        max_instances=1, max_concurrent_runners=1,
    ),
    "query_supervisor": ModelPermissions(  # alias
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=["expert"],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query"],
        can_delegate=True, delegatable_tiers=["expert"],
        max_instances=1, max_concurrent_runners=1,
    ),
    "supervisor_creative": ModelPermissions(
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=[],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query"],
        max_instances=1, max_concurrent_runners=1,
    ),
    "creative_supervisor": ModelPermissions(  # alias
        can_start_probes=True, can_stop_probes=True,
        controllable_tiers=[],
        can_write_memory=True, can_inject_persona=True,
        allowed_tool_categories=["query"],
        max_instances=1, max_concurrent_runners=1,
    ),

    # ── 专家 ──
    "expert_reviewer": ModelPermissions(requires_tool_approval=True),
    "code_reviewer": ModelPermissions(requires_tool_approval=True),  # alias
    "expert_implementer": ModelPermissions(allowed_tool_categories=["query", "mutation"], requires_tool_approval=True, max_instances=3, max_concurrent_runners=3),
    "code_writer": ModelPermissions(allowed_tool_categories=["query", "mutation"], requires_tool_approval=True, max_instances=3, max_concurrent_runners=3),  # alias
    "expert_tester": ModelPermissions(allowed_tool_categories=["query", "mutation"], requires_tool_approval=True),
    "tester": ModelPermissions(allowed_tool_categories=["query", "mutation"], requires_tool_approval=True),  # alias
    "expert_analyzer": ModelPermissions(),
    "expert_customer": ModelPermissions(),
    "expert_ui_designer": ModelPermissions(allowed_tool_categories=["query", "mutation"]),
    "expert_creative_writer": ModelPermissions(max_instances=2, max_concurrent_runners=2),
    "expert_emotion": ModelPermissions(can_write_memory=True),
}


def get_permissions(template_key: str) -> ModelPermissions:
    if template_key in DEFAULT_PERMISSIONS:
        return DEFAULT_PERMISSIONS[template_key]
    if template_key.startswith("supervisor"):
        return DEFAULT_PERMISSIONS["supervisor_code"]
    if template_key.startswith("expert"):
        return ModelPermissions()
    return ModelPermissions()


# ── ModelIdentity ──

@dataclass
class ModelIdentity:
    """模型身份 — 每个模型实例的独立配置"""

    model_id: str = ""
    name: str = ""
    tier: str = "expert"
    role: str = ""
    personality: str = ""
    speaking_style: str = ""
    expertise: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    capability: str = ""
    tool_whitelist: List[str] = field(default_factory=list)
    model_name: str = ""
    max_tokens: int = 256
    temperature: float = 0.2
    startup: str = "on_demand"
    default_skill: str = ""
    permissions: "ModelPermissions" = field(default_factory=lambda: ModelPermissions())
    metadata: Dict = field(default_factory=dict)
    api_key: Optional[str] = None
    api_url: Optional[str] = None

    @classmethod
    def from_template(cls, template_key: str, **overrides) -> "ModelIdentity":
        """从 YAML 模板创建身份，支持字段覆盖"""
        template = get_identities().get(template_key)
        if not template:
            raise ValueError(f"未知身份模板: {template_key}")

        tier = template.get("tier", "expert")
        role = template.get("role", "")

        # 工具白名单
        whitelist = list(template.get("tool_whitelist", []))
        if not whitelist:
            if role == "companion":
                whitelist = DEFAULT_TOOL_WHITELISTS.get("companion", [])
            elif tier == "large":
                whitelist = DEFAULT_TOOL_WHITELISTS["large"]
            elif tier == "supervisor":
                whitelist = DEFAULT_TOOL_WHITELISTS["supervisor"]
            else:
                whitelist = DEFAULT_TOOL_WHITELISTS.get(f"expert_{role}", [])

        # 模型名，从 YAML 的 model_names 段读取
        model_names = get_identities().get("_model_names", {})
        resolved_model_name = template.get("model_name", "") or model_names.get(tier, "")

        data = {
            "model_id": template["model_id"],
            "name": template["name"],
            "tier": tier,
            "role": role,
            "personality": template["personality"],
            "speaking_style": template["speaking_style"],
            "expertise": list(template.get("expertise", [])),
            "weaknesses": list(template.get("weaknesses", [])),
            "capability": template.get("capability", ""),
            "tool_whitelist": whitelist,
            "model_name": resolved_model_name,
            "max_tokens": template.get("max_tokens", 256),
            "temperature": template.get("temperature", 0.2),
            "startup": get_startup_mode(template_key),
            "default_skill": DEFAULT_SKILL_IDS.get(template_key, ""),
            "permissions": get_permissions(template_key),
            "metadata": {},
        }
        if "api_key" in template:
            data["api_key"] = template["api_key"]
        if "api_url" in template:
            data["api_url"] = template["api_url"]
        data.update(overrides)
        return cls(**data)

    def _tier_label(self) -> str:
        labels = {"large": "大模型层", "supervisor": "主管模型层", "expert": "专家模型层"}
        return labels.get(self.tier, self.tier)

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "tier": self.tier,
            "role": self.role,
            "expertise": self.expertise,
            "weaknesses": self.weaknesses,
            "speaking_style": self.speaking_style,
            "tool_whitelist": self.tool_whitelist,
            "permissions": {
                "can_start_probes": self.permissions.can_start_probes,
                "can_stop_probes": self.permissions.can_stop_probes,
                "can_write_memory": self.permissions.can_write_memory,
                "can_delegate": self.permissions.can_delegate,
                "allowed_tool_categories": self.permissions.allowed_tool_categories,
            },
        }
