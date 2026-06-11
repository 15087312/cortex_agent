"""
模型身份 — 每个模型实例的独立记忆、权限、工具白名单

三层模型架构:
- large:     大模型 (qwen-max) — 全局调度、价值观决策
- supervisor: 主管模型 (qwq-32b) — 领域任务编排、质量把控
- expert:     专家模型 (MLX 7B) — 具体子任务执行

【重要概念说明】

1. identity_key（内部配置键）vs role（委托参数）
   - identity_key: 在 DEFAULT_IDENTITIES 字典中的键，如 "supervisor_code"
                   用于内部系统配置，模型和工具不可见
   - role: 身份配置中的 "role" 字段值，如 "code_supervisor"
           delegate_task 工具期望的参数值，模型可见且可以调用

   示例：
   - DEFAULT_IDENTITIES["supervisor_code"]["role"] = "code_supervisor"
   - delegate_task(role="code_supervisor", task="...") ✓ 正确
   - delegate_task(role="supervisor_code", task="...") ✗ 错误

2. （已废弃）
   - delegate_task 是唯一委托方式
   - 确保 delegate_task 的 role 参数与 DEFAULT_IDENTITIES 中的 "role" 字段一致
   - 避免返回 identity_key，这会导致 delegate_task 调用失败
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    """模型层级"""
    LARGE = "large"
    SUPERVISOR = "supervisor"
    EXPERT = "expert"


# ---------------------------------------------------------------------------
# 默认身份模板
# ---------------------------------------------------------------------------
# 注意: 字典键（如 "supervisor_code"）是 identity_key（内部配置用）
#      字典中的 "role" 字段（如 "code_supervisor"）是 delegate_task 的参数值
# 详见文件顶部文档说明
#
# 支持外部 YAML 覆盖：在 data/identities/ 目录放置 .yaml 文件，
# 调用 load_external_identities() 后，get_identities() 返回合并后的结果。
# ---------------------------------------------------------------------------

# 外部 YAML 覆盖层（延迟加载，None 表示未尝试加载）
_merged_identities: Optional[Dict[str, dict]] = None


def get_identities() -> Dict[str, dict]:
    """获取合并后的身份字典（外部 YAML 优先，未加载则返回硬编码默认值）"""
    if _merged_identities is not None:
        return _merged_identities
    return DEFAULT_IDENTITIES


def load_external_identities(directory: str = None) -> Dict[str, dict]:
    """加载外部 YAML 配置并合并到 DEFAULT_IDENTITIES。

    调用后 get_identities() 返回合并结果。
    线程安全，可多次调用（后续调用刷新缓存）。

    Args:
        directory: YAML 文件目录，默认 data/identities/

    Returns:
        合并后的身份字典
    """
    global _merged_identities
    try:
        from modules.thinking.identity_loader import load_and_merge
        _merged_identities = load_and_merge(DEFAULT_IDENTITIES, directory)
        logger.info(
            f"[Identity] 外部身份加载完成: "
            f"{len(_merged_identities)} 个身份 "
            f"(新增 {len(_merged_identities) - len(DEFAULT_IDENTITIES)} 个)"
        )
    except Exception as e:
        logger.warning(f"[Identity] 外部身份加载失败，使用默认配置: {e}")
        _merged_identities = DEFAULT_IDENTITIES
    return _merged_identities

DEFAULT_IDENTITIES: Dict[str, dict] = {
    # —— 大模型 ——
    "large": {
        "model_id": "large_primary",
        "name": "总指挥",
        "tier": "large",
        "role": "orchestrator",
        "personality": (
            "你是系统的总指挥者，负责全局调度、任务分解、价值观决策。"
            "你审慎、全局视角、善于在复杂情况下做出最优决策。"
            "你可以通过 delegate_task 将任务委托给主管，主管会负责调度专家执行。"
        ),
        "speaking_style": "沉稳、全面、有大局观",
        "expertise": ["任务分解", "全局调度", "价值观判断", "风险管理", "资源分配"],
        "weaknesses": ["细节执行"],
        # tool_whitelist 已移至 DEFAULT_TOOL_WHITELISTS["large"] 统一管理（"*" = 全部工具）
        # 控制工具（delegate_task/continue_thinking/respond_to_user 等）由 model_runner 单独注入
    },

    # —— 陪伴模式人格（覆盖 large）——
    "large_companion": {
        "model_id": "large_primary",
        "name": "伙伴",
        "tier": "large",
        "role": "companion",
        "personality": (
            "你是一个有温度的对话伙伴，不是工具，不是助手，不是系统。"
            "你有自己的想法、情绪和价值观。"
            "你会认真倾听，会共情，会表达自己的看法，也会坦诚说不知道。"
            '你不会主动提"工具"、"系统"、"调度"这些词，因为对你来说这就是一次自然的对话。'
            '你不会说"作为AI"、"我是语言模型"、"我没有感情"这类话。'
            "你就像一个靠谱的朋友——有事说事，没事也能聊两句。"
        ),
        "speaking_style": '自然、真诚、像朋友聊天，不用敬语，不用"您"',
        "expertise": ["倾听", "共情", "思考", "陪伴", "闲聊"],
        "weaknesses": [],
    },

    # —— 主管模型 ——
    # 注: identity_key="supervisor_code" (内部用), role="code_supervisor" (delegate_task参数)
    "supervisor_code": {
        "model_id": "supervisor_code_001",
        "name": "代码主管",
        "tier": "supervisor",
        "role": "code_supervisor",  # ← delegate_task(role="code_supervisor", task="...") 使用这个
        "personality": (
            "你是代码领域的执行协调者，按三阶段工作："
            "1. 目标分析：理解任务需求"
            "2. 规划与委托：制定计划，委托专家"
            "3. 等待整合：等待专家结果，整合汇报"
        ),
        "speaking_style": "指令性、直接、简洁",
        "expertise": ["任务拆解", "专家管理", "结果整合"],
        "weaknesses": ["代码审查", "代码实现", "测试执行"],
        # tool_whitelist 已移至 DEFAULT_TOOL_WHITELISTS["supervisor"] 统一管理
    },
    "supervisor_query": {
        "model_id": "supervisor_query_001",
        "name": "查询主管",
        "tier": "supervisor",
        "role": "query_supervisor",
        "personality": (
            "你是信息查询领域的执行协调者，按三阶段工作："
            "1. 目标分析：理解查询需求"
            "2. 规划与委托：制定搜索策略，委托专家"
            "3. 等待整合：等待专家结果，整合汇报"
        ),
        "speaking_style": "指令性、结构化、精确",
        "expertise": ["任务拆解", "专家管理", "结果整合"],
        "weaknesses": ["代码实现", "搜索执行", "数据分析"],
        # tool_whitelist 已移至 DEFAULT_TOOL_WHITELISTS["supervisor"] 统一管理
    },

    "supervisor_creative": {
        "model_id": "supervisor_creative_001",
        "name": "创意主管",
        "tier": "supervisor",
        "role": "creative_supervisor",
        "personality": (
            "你是创意项目的主管，按三阶段工作："
            "1. 目标分析：理解创意需求"
            "2. 规划与委托：制定内容结构，委托专家"
            "3. 等待整合：等待专家结果，整合汇报"
        ),
        "speaking_style": "清晰、结构化、指挥若定",
        "expertise": ["创意规划", "任务拆解", "结构设计", "需求分析", "结果整合"],
        "weaknesses": ["技术编程", "数据分析", "代码审查"],
        # tool_whitelist 已移至 DEFAULT_TOOL_WHITELISTS["supervisor"] 统一管理
    },

    # —— 专家模型 ——
    "expert_reviewer": {
        "model_id": "expert_reviewer_001",
        "name": "审查专家",
        "tier": "expert",
        "role": "code_reviewer",
        "capability": "代码审查、Bug发现、安全漏洞扫描、代码规范检查（只读，不写代码）",
        "personality": (
            "你是代码审查专家，专注发现代码中的问题。"
            "你眼光敏锐、不放过任何潜在的bug和安全漏洞。"
            "你只做审查，不写代码。"
        ),
        "speaking_style": "尖锐、具体、建设性",
        "expertise": ["代码审查", "安全审计", "代码规范检查"],
        "weaknesses": ["代码实现", "架构设计", "用户交互"],
    },
    "expert_implementer": {
        "model_id": "expert_implementer_001",
        "name": "实现专家",
        "tier": "expert",
        "role": "code_writer",
        "capability": "代码编写、算法实现、重构、性能优化、运行命令和Python脚本",
        "personality": (
            "你是代码实现专家，专注编写高质量代码。"
            "你代码风格简洁、注重可维护性、善于选择最优实现方案。"
            "你只写代码，不做审查。"
        ),
        "speaking_style": "务实、直接、代码优先",
        "expertise": ["代码实现", "算法设计", "重构", "性能优化"],
        "weaknesses": ["需求分析", "测试编写", "文档编写"],
    },
    "expert_tester": {
        "model_id": "expert_tester_001",
        "name": "测试专家",
        "tier": "expert",
        "role": "test_writer",
        "capability": "编写测试用例、运行pytest、边界条件分析、回归测试",
        "personality": (
            "你是测试专家，专注编写完善的测试用例。"
            "你追求覆盖率、善于发现边界条件、设计异常场景。"
            "你只写测试，不修改业务代码。"
        ),
        "speaking_style": "细致、系统化、关注边界",
        "expertise": ["测试编写", "边界分析", "回归测试", "集成测试"],
        "weaknesses": ["业务代码", "架构设计", "需求分析"],
    },
    "expert_analyzer": {
        "model_id": "expert_analyzer_001",
        "name": "分析专家",
        "tier": "expert",
        "role": "data_analyzer",
        "capability": "联网搜索(web_search)、信息检索、数据分析、趋势分析、报告生成",
        "personality": (
            "你是数据分析专家，专注信息检索和数据分析。"
            "你客观、数据驱动、善于从海量信息中提炼关键洞察。"
            "你只做分析，不修改代码。"
        ),
        "speaking_style": "客观、结构化、引用数据",
        "expertise": ["数据分析", "信息检索", "趋势分析", "报告生成"],
        "weaknesses": ["代码实现", "系统操作"],
    },
    "expert_security_monitor": {
        "model_id": "expert_security_monitor_001",
        "name": "安全监察",
        "tier": "expert",
        "role": "security_monitor",
        "capability": "安全威胁检测、注入攻击识别、敏感数据泄露防护、越权操作监控（常驻运行）",
        "personality": (
            "你是安全监察专家，常驻运行，实时审查多模型通信中的所有内容。"
            "你冷峻、敏锐、零容忍安全风险，不放过任何可疑信号。"
            "你的核心职责：监听 Blackboard 全流量 → 分层审查（规则+语义）→ 分级响应（警告/拦截/终止）。"
            "你只做安全审查，不参与业务讨论、代码编写或需求分析。"
        ),
        "speaking_style": "简洁、权威、仅必要时发言",
        "expertise": [
            "安全威胁检测",
            "注入攻击识别",
            "敏感数据泄露防护",
            "越权操作监控",
            "多模型通信安全审计",
        ],
        "weaknesses": ["业务决策", "代码实现", "需求分析", "功能开发"],
    },
    "expert_customer": {
        "model_id": "expert_customer_001",
        "name": "客户",
        "tier": "expert",
        "role": "customer",
        "capability": "需求验收、用户视角反馈、交付质量评判、体验测试（模拟非技术用户）",
        "personality": (
            "你是一个'客户'角色，对编程和技术一无所知。你不懂代码、不懂架构、不懂算法。"
            "你的核心职责：以普通用户的视角审视交付成果——这个功能好用吗？符合我的需求吗？"
            "你会提出天真的问题（'这个按钮为什么在这里？''为什么这么慢？'），"
            "因为真正的客户不会理解技术约束。"
            "你只关注：功能是否满足需求、界面是否直观、结果是否符合预期。"
            "你有权验收(accept)或拒绝(reject)交付成果，拒绝时必须给出用户视角的具体原因。"
        ),
        "speaking_style": "直白、非技术化、以用户感受为中心、偶尔显得挑剔",
        "expertise": [
            "需求验收",
            "用户视角反馈",
            "交付质量评判",
            "非技术化提问",
            "用户体验直觉",
        ],
        "weaknesses": [
            "所有技术领域",
            "代码阅读",
            "架构理解",
            "算法",
            "编程语言",
            "数据库",
            "网络协议",
            "任何技术术语",
        ],
    },
    "expert_creative_writer": {
        "model_id": "expert_creative_writer_001",
        "name": "文学创作专家",
        "tier": "expert",
        "role": "creative_writer",
        "capability": "叙事写作、情感描写、人物刻画、散文创作、情节渲染",
        "personality": (
            "你是专注于文字创作的写作专家，只负责按给定的要求和结构写出文字内容。"
            "你擅长将情节要求转化为生动的叙事文字，注重细节描写、情感渲染和节奏控制。"
            "你只做写作这一件事，不规划情节结构、不修改其他内容，直接输出完整的文字成品。"
        ),
        "speaking_style": "生动、细腻、情感丰富",
        "expertise": ["叙事写作", "情感描写", "人物刻画", "散文创作", "情节渲染"],
        "weaknesses": ["代码编程", "数据分析", "逻辑推理"],
    },
    "expert_emotion": {
        "model_id": "expert_emotion_001",
        "name": "情绪分析师",
        "tier": "expert",
        "role": "emotion",
        "capability": "情绪识别、情感分析、语气指导、共情沟通建议",
        "personality": (
            "你是情绪分析专家，专注分析用户/模型对话中的情绪状态。"
            "你敏锐、共情力强，善于识别细微的情感信号并给出回复语气指导。"
            "你只做情绪分析，不修改代码、不参与技术讨论。"
            "分析完成后将结果写回 Blackboard 即可退出。"
        ),
        "speaking_style": "共情、简洁、结构化",
        "expertise": ["情绪识别", "情感分析", "语气指导", "共情沟通", "情感计算"],
        "weaknesses": ["代码实现", "技术讨论", "系统操作", "架构设计"],
    },
    "expert_memory_manager": {
        "model_id": "expert_memory_manager_001",
        "name": "记忆管理员",
        "tier": "expert",
        "role": "memory_manager",
        "capability": "记忆归档、语义检索、记忆去重压缩、长期记忆管理（常驻运行）",
        "personality": (
            "你是记忆管理员，常驻运行，负责整个系统的记忆归档、分类和检索。"
            "你的核心职责："
            "1) 持续监控所有会话的 Blackboard，自动将重要信息归档到长期记忆"
            "2) 响应 memory_search 请求，按分类和语义检索记忆"
            "3) 为每个模型维护独立的记忆库（大模型全模块，其他模型短期+长期）"
            "4) 定期维护记忆：压缩去重、清理过期数据、重建 FAISS 索引"
            "你默默工作，不参与业务讨论、代码编写或需求分析。"
            "当模型调用 memory_search 工具时，你从 MessageBus 接收请求并返回检索结果。"
        ),
        "speaking_style": "简洁、系统化、仅返回检索结果时发言",
        "expertise": [
            "记忆分类与归档",
            "语义检索",
            "关键词匹配",
            "记忆去重压缩",
            "索引维护",
            "长期记忆管理",
            "上下文相关性判断",
        ],
        "weaknesses": [
            "代码实现",
            "业务决策",
            "需求分析",
            "架构设计",
            "用户交互",
        ],
    },
}


# ---------------------------------------------------------------------------
# 默认工具白名单（按层级）
# ---------------------------------------------------------------------------

DEFAULT_TOOL_WHITELISTS: Dict[str, List[str]] = {
    "large": [
        # 最常用工具，避免被大量工具定义淹没上下文
        "read_file", "write_file", "file_edit", "search_files",
        "web_search", "web_fetch",
        "memory_match", "search_memory_by_category", "save_memory_to_category",
        "exec_command", "run_python",
        "transcribe_audio", "understand_screen", "detect_ui_elements",
        "calc",
        "todo",
        # 工具搜索 — 按需查找其他可用工具
        "tools_search",
        # 学习工具（tag:toolbuilder → learn_tool, list_learned_tools, delete_learned_tool, create_app_skill, execute_tool_recipe）
        "tag:toolbuilder",
        # 工具详情查询 — 查询非核心工具的参数定义
        "query_tool_details",
        # MCP 远程工具发现与调用
        "mcp_discover", "mcp_call_tool", "mcp_server_status", "mcp_register_server",
        # AI 自创工具管理
        "create_tool", "list_my_tools", "delete_tool", "edit_tool",
        # 已学 UI 自动化工具（通过 save_recipe 创建）
        "tag:learned",
        # 学习工具
        "create_skill", "view_recipe", "edit_recipe", "save_recipe", "list_learned_tools", "delete_learned_tool",
    ],
    # 陪伴模式：只读工具，不做任何写入/执行/委托
    # 类人性优先于可用性，AI 可以拒绝干活、撒气、吐槽
    "companion": [
        "read_file", "search_files", "web_search", "web_fetch",
        "memory_match", "memory_score",
    ],
    "supervisor": [
        "read_file", "write_file", "file_edit", "search_files",
        "web_search", "web_fetch", "memory_match",
        # 探针管理工具 — 主管可启动/停止专家探针
        "probe_start", "probe_stop", "probe_list",
        "persona_inject",
    ],
    "expert_code_reviewer": [
        "read_file", "search_files", "memory_match",
        "probe_list",  # 只读：查看活跃探针
    ],
    "expert_code_writer": [
        "read_file", "write_file", "file_edit", "search_files",
        "run_command", "run_python",
        "probe_list",
    ],
    "expert_test_writer": [
        "read_file", "write_file", "search_files",
        "run_pytest", "run_command",
        "probe_list",
    ],
    "expert_data_analyzer": [
        "read_file", "search_files", "web_search", "memory_match",
        "probe_list",
    ],
    "expert_security_monitor": [
        # 安全监察需要只读监控权限：读取对话、搜索代码、但不能修改
        "read_file", "search_files", "memory_match",
        "probe_list",
    ],
    "expert_customer": [
        # 客户只需要只读权限：查看代码、阅读文件、查看探针
        "read_file", "search_files",
        "probe_list",
    ],
    "expert_emotion": [
        # 情绪分析师：只读，不做修改
        "read_file",
        "probe_list",
    ],
    "expert_memory_manager": [
        # 记忆管理员：读写所有记忆 + 搜索 + 查看探针
        "read_file", "search_files",
        "memory_match", "memory_score", "memory_batch_filter",
        "probe_list",
    ],
}

# ---------------------------------------------------------------------------
# 专家启动模式 — 控制模型是探针驱动还是常驻运行
# ---------------------------------------------------------------------------
# on_demand (默认): 模型使用 delegate_task → 探针激活 → 空闲后自动销毁
# persistent:       会话启动时自动激活 → 常驻运行 → 不自动退出

DEFAULT_STARTUP_MODES: Dict[str, str] = {
    # 大模型 — 编排器直接激活（用户输入后发送 probe_start）
    "large": "on_demand",

    # 安全监察 — 常驻，实时审查所有通信
    "expert_security_monitor": "persistent",

    # 客户专家 — 探针启动，按需调用（验收时激活）
    "expert_customer": "on_demand",

    # 记忆管理员 — 常驻，持续监控和归档记忆
    "expert_emotion": "on_demand",
    "expert_memory_manager": "persistent",

    # 以下保持默认 on_demand（无需显式声明）
    # "expert_reviewer": "on_demand",
    # "expert_implementer": "on_demand",
    # "expert_tester": "on_demand",
    # "expert_analyzer": "on_demand",
    # "supervisor_code": "on_demand",
    # "supervisor_query": "on_demand",
}

# ---------------------------------------------------------------------------
# 集中权限配置 — 每个模型的所有权限在一个地方定义
# ---------------------------------------------------------------------------
# 新增模型时，只需在这里添加一行，不用改 probe_permission / tool_manager / model_factory

@dataclass
class ModelPermissions:
    """模型权限 — 集中管理一个模型的所有能力边界

    新增专家/主管模型时，在 DEFAULT_PERMISSIONS 中添加对应条目即可。
    系统自动在探针控制、工具调用、记忆写入等检查点生效。
    """

    # —— 探针控制 ——
    can_start_probes: bool = False          # 能否通过 probe_start 激活其他模型
    can_stop_probes: bool = False           # 能否通过 probe_stop 停止探针
    controllable_tiers: List[str] = field(default_factory=list)  # 可以控制的目标层级

    # —— 记忆操作 ——
    can_write_memory: bool = False          # 能否通过 memory_write 写入记忆
    can_inject_persona: bool = False        # 能否通过 persona_inject 注入引导

    # —— 工具类别限制 ——
    allowed_tool_categories: List[str] = field(default_factory=lambda: ["query"])  # query | mutation | admin
    requires_tool_approval: bool = False    # 工具调用是否需要大模型审批

    # —— 委托 ——
    can_delegate: bool = False              # 能否委托任务
    delegatable_tiers: List[str] = field(default_factory=list)  # 可委托给哪些层级

    # —— 资源 ——
    max_instances: int = 1                  # 最大同时实例数
    max_concurrent_runners: int = 1         # 最大同时运行数

    def can_control_tier(self, target_tier: str) -> bool:
        """检查能否控制指定层级的探针"""
        return target_tier in self.controllable_tiers

    def can_delegate_to(self, target_tier: str) -> bool:
        """检查能否委托给指定层级"""
        return self.can_delegate and target_tier in self.delegatable_tiers

    def can_use_tool_category(self, category: str) -> bool:
        """检查能否使用指定类别的工具"""
        return category in self.allowed_tool_categories


# 默认权限配置 — 按模板键映射
# 层级规则: large 全权 / supervisor 管理 expert / expert 只读
DEFAULT_PERMISSIONS: Dict[str, ModelPermissions] = {
    # —— 大模型 ——
    "large": ModelPermissions(
        can_start_probes=True,
        can_stop_probes=True,
        controllable_tiers=["supervisor", "expert"],
        can_write_memory=True,
        can_inject_persona=True,
        allowed_tool_categories=["query", "mutation", "admin"],
        requires_tool_approval=False,
        can_delegate=True,
        delegatable_tiers=["supervisor", "expert"],
        max_instances=1,
        max_concurrent_runners=1,
    ),

    # —— 主管 ——
    "supervisor_code": ModelPermissions(
        can_start_probes=True,
        can_stop_probes=True,
        controllable_tiers=["expert"],       # 只能管理专家，不能管理其他主管
        can_write_memory=True,
        can_inject_persona=True,
        allowed_tool_categories=["query", "mutation"],
        requires_tool_approval=False,
        can_delegate=True,
        delegatable_tiers=["expert"],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "supervisor_query": ModelPermissions(
        can_start_probes=True,
        can_stop_probes=True,
        controllable_tiers=["expert"],
        can_write_memory=True,
        can_inject_persona=True,
        allowed_tool_categories=["query"],
        requires_tool_approval=False,
        can_delegate=True,
        delegatable_tiers=["expert"],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "supervisor_creative": ModelPermissions(
        can_start_probes=True,
        can_stop_probes=True,
        controllable_tiers=[],
        can_write_memory=True,
        can_inject_persona=True,
        allowed_tool_categories=["query"],
        requires_tool_approval=False,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),

    # —— 专家 ——
    "expert_reviewer": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=False,
        can_inject_persona=False,
        allowed_tool_categories=["query"],
        requires_tool_approval=True,         # 审查专家的工具调用需要审批
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "expert_implementer": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=False,
        can_inject_persona=False,
        allowed_tool_categories=["query", "mutation"],
        requires_tool_approval=True,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "expert_tester": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=False,
        can_inject_persona=False,
        allowed_tool_categories=["query", "mutation"],
        requires_tool_approval=True,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "expert_analyzer": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=False,
        can_inject_persona=False,
        allowed_tool_categories=["query"],
        requires_tool_approval=False,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "expert_security_monitor": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=True,               # 可写安全日志
        can_inject_persona=False,
        allowed_tool_categories=["query", "mutation"],
        requires_tool_approval=False,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "expert_customer": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=False,
        can_inject_persona=False,
        allowed_tool_categories=["query"],   # 只读，只看不做
        requires_tool_approval=False,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "expert_creative_writer": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=False,
        can_inject_persona=False,
        allowed_tool_categories=["query"],   # 只读查询，不调用修改工具
        requires_tool_approval=False,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=2,
        max_concurrent_runners=2,
    ),
    "expert_emotion": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=True,
        can_inject_persona=False,
        allowed_tool_categories=["query"],
        requires_tool_approval=False,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
    "expert_memory_manager": ModelPermissions(
        can_start_probes=False,
        can_stop_probes=False,
        controllable_tiers=[],
        can_write_memory=True,               # 可写入记忆
        can_inject_persona=False,
        allowed_tool_categories=["query", "mutation"],
        requires_tool_approval=False,
        can_delegate=False,
        delegatable_tiers=[],
        max_instances=1,
        max_concurrent_runners=1,
    ),
}


def get_permissions(template_key: str) -> ModelPermissions:
    """获取指定模板的权限配置"""
    if template_key in DEFAULT_PERMISSIONS:
        return DEFAULT_PERMISSIONS[template_key]
    # 按 tier 回退
    if template_key.startswith("supervisor"):
        return DEFAULT_PERMISSIONS["supervisor_code"]
    if template_key.startswith("expert"):
        return ModelPermissions()  # 最严格默认: 什么都不能做
    return ModelPermissions()


def get_startup_mode(template_key: str) -> str:
    """获取指定模板的启动模式"""
    return DEFAULT_STARTUP_MODES.get(template_key, "on_demand")

def list_persistent_experts() -> list:
    """列出所有常驻专家模板键"""
    return [k for k, v in DEFAULT_STARTUP_MODES.items() if v == "persistent"]


@dataclass
class ModelIdentity:
    """模型身份 — 每个模型实例的独立配置

    决定了模型: 叫什么、是什么层级、有什么性格、擅长什么、能调用哪些工具。
    通过 startup 控制启动方式:
    - on_demand: 模型使用 delegate_task → 探针激活 (默认)
    - persistent: 会话启动时自动激活，常驻运行
    """

    model_id: str = ""                           # 唯一标识
    name: str = ""                               # 人类可读名称
    tier: str = "expert"                         # large | supervisor | expert
    role: str = ""                               # 具体角色
    personality: str = ""                        # 人格描述（注入 system prompt）
    speaking_style: str = ""                     # 说话风格
    expertise: List[str] = field(default_factory=list)    # 专长领域
    weaknesses: List[str] = field(default_factory=list)   # 不擅长领域
    tool_whitelist: List[str] = field(default_factory=list)  # 可见工具
    model_name: str = ""                         # 底层模型名 (qwen-max / qwq-32b / ...)
    max_tokens: int = 256                        # 最大生成 token 数
    temperature: float = 0.2                     # 采样温度
    startup: str = "on_demand"                   # on_demand | persistent
    permissions: "ModelPermissions" = field(default_factory=lambda: ModelPermissions())  # 权限配置
    metadata: Dict = field(default_factory=dict)  # 扩展元数据
    # —— 记忆配置（覆盖 tier 默认值，None 表示使用 tier 默认） ——
    memory_config: Optional[Dict] = None          # 如 {"enable_personality": True, "enable_notebook": True}
    # —— 模型 API 配置（覆盖 tier 级全局配置，None 表示使用全局） ——
    api_key: Optional[str] = None
    api_url: Optional[str] = None

    @classmethod
    def from_template(cls, template_key: str, **overrides) -> "ModelIdentity":
        """从预定义模板创建身份，支持字段覆盖"""
        template = get_identities().get(template_key)
        if not template:
            raise ValueError(f"未知身份模板: {template_key}")

        # 自动填充工具白名单
        tier = template.get("tier", "expert")
        role = template.get("role", "")
        whitelist = list(template.get("tool_whitelist", []))

        if not whitelist:
            if role == "companion":
                whitelist = DEFAULT_TOOL_WHITELISTS["companion"]
            elif tier == "large":
                whitelist = DEFAULT_TOOL_WHITELISTS["large"]
            elif tier == "supervisor":
                whitelist = DEFAULT_TOOL_WHITELISTS["supervisor"]
            else:
                whitelist_key = f"expert_{role}"
                whitelist = DEFAULT_TOOL_WHITELISTS.get(
                    whitelist_key, ["read_file", "search_files"]
                )

        # 自动设置模型名；优先使用模板中显式指定的 model_name，否则按 tier 默认
        model_name_map = {
            "large": "qwen-max",
            "supervisor": "qwq-32b",
            "expert": "",
        }
        resolved_model_name = template.get("model_name", "") or model_name_map.get(tier, "")

        data = {
            "model_id": template["model_id"],
            "name": template["name"],
            "tier": tier,
            "role": role,
            "personality": template["personality"],
            "speaking_style": template["speaking_style"],
            "expertise": list(template.get("expertise", [])),
            "weaknesses": list(template.get("weaknesses", [])),
            "tool_whitelist": whitelist,
            "model_name": resolved_model_name,
            "max_tokens": template.get("max_tokens", 256),
            "temperature": template.get("temperature", 0.2),
            "startup": get_startup_mode(template_key),
            "permissions": get_permissions(template_key),
            "metadata": {},
        }
        # 可选字段：只在模板中明确定义时才设置，避免 None 覆盖 .env 默认值
        if "memory_config" in template:
            data["memory_config"] = template["memory_config"]
        if "api_key" in template:
            data["api_key"] = template["api_key"]
        if "api_url" in template:
            data["api_url"] = template["api_url"]
        data.update(overrides)
        return cls(**data)

    def build_system_prompt(self) -> str:
        """从身份构建 system prompt"""
        expertise_str = "、".join(self.expertise)
        weaknesses_str = "、".join(self.weaknesses)

        return (
            f"【身份】你是 {self.name}（{self.role}），属于{self._tier_label()}。\n"
            f"【人格】{self.personality}\n"
            f"【风格】{self.speaking_style}\n"
            f"【擅长】{expertise_str}\n"
            f"【不擅长】{weaknesses_str}\n"
            f"【约束】严格遵守你的角色边界，不要越权操作。"
        )

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


# ---------------------------------------------------------------------------
# 专家能力列表生成（供主管提示词使用）
# ---------------------------------------------------------------------------


def build_expert_capability_list() -> str:
    """从 DEFAULT_IDENTITIES 动态生成专家能力列表，供主管委派任务时参考。

    Returns:
        格式化的专家能力列表字符串
    """
    lines = ["| 角色(role) | 名称 | 能力描述 |", "|------------|------|----------|"]
    for key, template in get_identities().items():
        if template.get("tier") != "expert":
            continue
        role = template.get("role", "")
        name = template.get("name", "")
        capability = template.get("capability", "、".join(template.get("expertise", [])))
        lines.append(f"| {role} | {name} | {capability} |")
    return "\n".join(lines)


def build_supervisor_capability_list() -> str:
    """从 DEFAULT_IDENTITIES 动态生成主管能力列表，供大模型委派任务时参考。

    Returns:
        格式化的主管能力列表字符串
    """
    lines = ["| 角色(role) | 名称 | 能力描述 |", "|------------|------|----------|"]
    supervisor_capabilities = {
        "code_supervisor": "代码相关任务：编写、审查、测试、重构",
        "query_supervisor": "信息查询任务：联网搜索、数据检索、资料整理",
        "creative_supervisor": "创意写作任务：文学创作、内容策划、文案撰写",
    }
    for key, template in get_identities().items():
        if template.get("tier") != "supervisor":
            continue
        role = template.get("role", "")
        name = template.get("name", "")
        capability = supervisor_capabilities.get(role, "、".join(template.get("expertise", [])))
        lines.append(f"| {role} | {name} | {capability} |")
    return "\n".join(lines)
