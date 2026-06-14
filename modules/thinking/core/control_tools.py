"""
Continuous thinking control tools and task context.

These controls belong to the thinking loop rather than a model runner because
 they decide loop lifecycle, completion semantics, and result routing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


CONTINUE_THINKING_TOOL = {
    "type": "function",
    "function": {
        "name": "continue_thinking",
        "description": (
            "控制当前连续思考循环是否继续执行。只有当本次思考循环目标已经完成，"
            "并且结果已经可以返回给目标模型或用户时，才将 continue 设为 false。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "continue": {
                    "type": "boolean",
                    "description": "true=继续当前思考循环，false=结束当前思考循环并返回结果",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "下一轮思考前等待秒数，范围 1-60。等待专家结果或外部数据时可适当延长。",
                },
                "reason": {
                    "type": "string",
                    "description": "继续或终止当前思考循环的原因",
                },
                "result_summary": {
                    "type": "string",
                    "description": "当 continue=false 时，写入最终回复内容（要返回给用户/委托方的实际文本，不是任务描述）",
                },
            },
            "required": ["continue"],
        },
    }
}


DELEGATE_TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate_task",
        "description": (
            "控制当前连续思考循环发起内部委托。该工具只登记委托请求，"
            "由 ContinuousThinker 通过 DelegationPort 分发并追踪等待状态。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "专家/主管角色名称，请从系统提示中的「可用主管」「可用专家」列表选择。不要编造不存在的角色名。需要联网搜索、读取网页、读写文件、执行命令、查询记忆时用专家；需要代码审查/实现/测试/分析时用对应专家或主管。",
                },
                "task": {
                    "type": "string",
                    "description": "需要委托的具体任务描述，必须包含工具目标和关键参数，例如：搜索『暗区突围 当前玩家数量』并返回来源和摘要",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "发起委托后建议等待秒数，范围 1-60。未传时由 ContinuousThinker 自动决定。",
                },
            },
            "required": ["role", "task"],
        },
    }
}

# Control tools flow via tool_calls to ModelRunner which routes them to thinker


CREATE_SUPERVISOR_TOOL = {
    "type": "function",
    "function": {
        "name": "create_supervisor",
        "description": "创建新的主管模型。通常不需要，请优先使用 delegate_task 委托已有角色。只有在需要非标准角色时才使用此功能。",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "新主管的角色名称（中文），例如「社会议题分析主管」",
                },
                "template_key": {
                    "type": "string",
                    "description": "身份模板键。不传则使用通用分析主管模板(supervisor_default)。可选：supervisor_code(代码), supervisor_query(查询), supervisor_creative(创意), supervisor_default(通用分析)",
                    "enum": ["supervisor_code", "supervisor_query", "supervisor_creative", "supervisor_default"],
                },
                "personality": {
                    "type": "string",
                    "description": "可选。自定义人格描述，例如「你是社会议题分析专家，关注性别平等、历史演进、跨文化比较」。如果不传则使用模板默认人格。",
                },
                "responsibilities": {
                    "type": "string",
                    "description": "可选。职责描述，会附加到系统提示中，例如「负责全面分析社会议题的历史背景、主要流派、核心主张、当前影响和文化差异」",
                },
                "task": {
                    "type": "string",
                    "description": "创建后立即委托给该主管的初始任务",
                },
            },
            "required": ["role"],
        },
    },
}

RESPOND_TO_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "respond_to_user",
        "description": "向用户输出最终回复。只有当任务已经完成、信息已经充分、可以直接回复用户时才调用此工具。如果还需要等待委托结果或继续思考，不要调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要回复给用户的最终内容。只放真正要回复给用户的话，不要写任务描述、内部状态或 JSON。",
                },
            },
            "required": ["content"],
        },
    },
}

REQUEST_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "request_skill",
        "description": (
            "请求激活一个技能。激活后你将扮演该技能定义的角色，遵循其规章和流程。"
            "当你判断当前任务需要特定专业知识或流程时使用。"
            "使用 list_skills 查看可用技能。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "技能 ID，如 code_review, architecture_design, problem_diagnosis",
                },
            },
            "required": ["skill_id"],
        },
    },
}

STOP_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "stop_skill",
        "description": (
            "停用当前激活的技能，恢复默认角色。"
            "当技能任务已完成、需要切换到其他技能、或不需要特定角色时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "停用原因，例如「代码审查已完成」",
                },
            },
            "required": [],
        },
    },
}

LIST_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": "列出所有可用技能。在不确定使用哪个技能时先调用此工具查看。",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

QUERY_TOOL_DETAILS_TOOL = {
    "type": "function",
    "function": {
        "name": "query_tool_details",
        "description": (
            "查询某个工具的完整定义（参数、描述、用法）。"
            "系统中只有常用工具附带了完整参数定义，其余工具仅列出名称。"
            "使用此工具获取非核心工具的参数详情后，才能正确调用该工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "要查询的工具名称",
                },
            },
            "required": ["tool_name"],
        },
    },
}


REQUEST_MODE_CHANGE_TOOL = {
    "type": "function",
    "function": {
        "name": "request_mode_change",
        "description": (
            "请求切换执行模式。\n"
            "- plan/edit/yolo/control: 切换工作模式，需用户确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "请求切换模式的原因",
                },
                "suggested_mode": {
                    "type": "string",
                    "description": "建议的目标模式",
                    "enum": ["plan", "edit", "yolo", "control"],
                },
            },
            "required": ["reason", "suggested_mode"],
        },
    },
}


ASK_USER_INTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user_intent",
        "description": (
            "向用户提问并获取选择。当你不确定用户的具体意图、偏好或选择时使用。"
            "提供你猜测的几个选项让用户选择，用户也可以输入自定义答案。"
            "例如：「你想让我重构哪个模块？」附带选项 [认证模块, 数据库层, API路由]"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "猜测的选项列表（2-5个），用户可以选择其中一个或输入自定义答案",
                    "minItems": 2,
                    "maxItems": 5,
                },
                "context": {
                    "type": "string",
                    "description": "可选。为什么需要问这个问题的背景说明",
                },
            },
            "required": ["question", "options"],
        },
    },
}


@dataclass
class ThinkingTaskContext:
    """一次连续思考循环的任务契约。"""

    task_id: str
    loop_goal: str
    origin_model_id: str
    return_to_model_id: str = ""
    return_to_session_id: str = ""
    caller_tier: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ThinkingControlDecision:
    """模型对当前思考循环的控制决策。"""

    should_continue: bool = True
    wait_seconds: Optional[int] = None
    reason: str = ""
    result_summary: str = ""
    delegations: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ThinkingControlDecision":
        wait_seconds = payload.get("wait_seconds")
        if wait_seconds is not None:
            try:
                wait_seconds = max(1, min(60, int(wait_seconds)))
            except Exception:
                wait_seconds = None
        return cls(
            should_continue=bool(payload.get("continue", True)),
            wait_seconds=wait_seconds,
            reason=str(payload.get("reason", "") or ""),
            result_summary=str(payload.get("result_summary", "") or ""),
            delegations=list(payload.get("delegations", []) or []),
            raw=dict(payload),
        )
