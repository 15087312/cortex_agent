"""
探针管理工具 — 模型通过工具调用来控制探针

6 个核心工具:
- probe_start: 启动探针激活指定模型角色
- probe_stop: 停止探针
- probe_list: 列出活跃探针
- memory_write: 向指定模型写入记忆
- persona_inject: 向指定模型注入引导提示词
- request_intermediate_response: 请求中途回复（大模型在专家工作时先回复用户）

权限模型: ProbePermissionManager (large > supervisor > expert)
           + ModelPermissions 细粒度权限（优先使用）
"""
import time
import uuid
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger
from modules.thinking.context.compression import CompressionEngine
import asyncio

if TYPE_CHECKING:
    from modules.thinking.identity import ModelPermissions

logger = setup_logger("probe_tools")

# 工具调用者角色 → tier 映射
_ROLE_TO_TIER = {
    "large": "large",
    "supervisor": "supervisor",
    "expert": "expert",
}


def _get_caller_permissions(kwargs: dict = None) -> "Optional[ModelPermissions]":
    """从工具调用上下文中获取调用者的 ModelPermissions

    优先通过 _caller_model_id 精确查找，回退到 tier 查找。
    返回 None 表示无法获取，调用方应回退到硬编码权限检查。
    """
    try:
        from modules.thinking.model_factory import get_model_factory
        from modules.thinking.identity import get_permissions

        factory = get_model_factory()
        kwargs = kwargs or {}

        # 1. 通过 _caller_model_id 精确查找
        caller_model_id = kwargs.get("_caller_model_id", "")
        if caller_model_id:
            instance = factory.get(caller_model_id)
            if instance and hasattr(instance.identity, 'permissions'):
                return instance.identity.permissions

        # 2. 回退: 通过 _caller_role 找同 tier 实例
        caller_role = kwargs.get("_caller_role", "")
        if caller_role:
            tier = _ROLE_TO_TIER.get(caller_role, caller_role)
            instances = factory.list_by_tier(tier)
            if instances:
                identity = instances[0].identity
                if hasattr(identity, 'permissions'):
                    return identity.permissions

        # 3. 最后回退: 通过 template_key
        caller_role = kwargs.get("_caller_role", "large")
        return get_permissions(caller_role)
    except Exception as e:
        logger.debug(f"[权限] 获取调用者权限失败，回退到硬编码检查: {e}")
        return None


def _get_caller_tier(kwargs: dict = None) -> str:
    """从工具调用上下文中解析 caller tier

    优先使用传入的 caller_role 参数，否则从调用栈推断。
    实际上由 ToolManager._check_tool_permission 提供 caller_role，
    这里从 kwargs 中获取（如果传递了的话）。
    """
    # 工具可以通过 _caller_role 获取（由 tool_manager 注入）
    # 这里使用默认 "large"，实际权限检查在 probe_start/stop 内部完成
    return "large"


# ============================================================================
# probe_start — 启动探针
# ============================================================================

@ToolRegistry.register(
    "probe_start",
    description=(
        "启动一个探针来激活指定层级的目标模型角色。"
        "大模型(large)可以启动主管(supervisor)或专家(expert)探针；"
        "主管(supervisor)可以启动专家(expert)探针；"
        "专家(expert)不能启动任何探针。"
        "返回探针ID供 probe_stop 或 probe_list 使用。"
    ),
    params={
        "target_tier": "目标模型层级: supervisor 或 expert",
        "identity_key": "身份模板键，如 supervisor_code, supervisor_query, expert_implementer, expert_reviewer, expert_analyzer, expert_tester",
        "task_description": "分配给该模型的任务描述，将被注入为其首轮思考上下文",
        "probe_priority": "可选，探针优先级: CRITICAL(5)/HIGH(4)/MEDIUM(3)/LOW(2)/MINIMAL(1)，默认 MEDIUM",
        "ttl_seconds": "可选，探针生存秒数，默认 1800 (30分钟)",
    },
    risk_level="MEDIUM",
    category="admin",
)
def probe_start(
    target_tier: str,
    identity_key: str,
    task_description: str,
    probe_priority: str = "MEDIUM",
    ttl_seconds: int = 1800,
    **kwargs,
) -> Dict[str, Any]:
    """启动一个探针来激活指定模型

    实际激活由 ModelRunnerManager 处理（Phase 2）。
    Phase 1 中，将探针注册到 ProbeCache 并返回探针ID。
    """
    try:
        from modules.thinking.probes.probe_permission import get_probe_permission_manager

        # 确定 caller tier（从调用上下文推断）
        caller_role = kwargs.get("_caller_role", "large")
        caller_tier = _ROLE_TO_TIER.get(caller_role, "large")
        return_to_model_id = kwargs.get("return_to_model_id", "") or kwargs.get("_caller_model_id", "")
        return_to_session_id = kwargs.get("return_to_session_id", "") or kwargs.get("_session_id", "")
        task_id = kwargs.get("task_id", "") or f"task_{uuid.uuid4().hex[:12]}"

        ppm = get_probe_permission_manager()

        # 权限校验 — 优先使用 ModelPermissions，回退到硬编码层级
        caller_perms = _get_caller_permissions(kwargs)
        if caller_perms is not None:
            error = ppm.validate_probe_start_with_permissions(
                caller_perms, target_tier, identity_key, caller_tier,
            )
        else:
            error = ppm.validate_probe_start(caller_tier, target_tier, identity_key)
        if error:
            return {"success": False, "error": error}

        # 验证 identity_key
        try:
            from modules.thinking.identity import get_identities
            if identity_key not in get_identities():
                return {
                    "success": False,
                    "error": f"未知的身份模板: {identity_key}，可用: {list(get_identities().keys())}",
                }
            template = get_identities()[identity_key]
            actual_tier = template.get("tier", "")
            if actual_tier != target_tier:
                logger.warning(
                    f"[probe_start] identity_key={identity_key} 的 tier={actual_tier} "
                    f"与请求的 target_tier={target_tier} 不匹配，使用实际 tier"
                )
                target_tier = actual_tier
        except ImportError:
            pass

        # 优先级解析
        priority_map = {
            "CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "MINIMAL": 1,
        }
        priority = priority_map.get(probe_priority.upper(), 3)

        # 生成探针ID
        probe_id = f"probe_{target_tier}_{identity_key}_{uuid.uuid4().hex[:8]}"

        # —— 层级会话管理: supervisor 启动时自动创建副会话（已弃用） ——
        # DEPRECATED: 新架构使用 CognitiveBlackboard，不需要副会话
        sub_session_id = ""
        if target_tier == "supervisor":
            logger.debug("[probe_start] SessionManager 副会话已废弃，使用 CognitiveBlackboard")
            # 旧代码保留但注释，确保向后兼容
            # try:
            #     from modules.thinking.session import get_session_manager
            #     sm = get_session_manager()
            #     main = sm.get_main_session()
            #     if main:
            #         template_name = template.get("name", identity_key)
            #         template_model_id = template.get("model_id", identity_key)
            #         sub = sm.create_sub_session(
            #             parent_session_id=main.session_id,
            #             supervisor_model_id=template_model_id,
            #             supervisor_name=template_name,
            #         )
            #         sub_session_id = sub.session_id
            #         logger.info(
            #             f"[probe_start] 为主管 {template_name} 创建副会话: {sub_session_id[:24]}"
            #         )
            # except Exception as e:
            #     logger.warning(f"[probe_start] 创建副会话失败 (非致命): {e}")

        # 注册到 ProbeCache
        try:
            from modules.thinking.probes.probe_cache import get_probe_cache, ActiveProbe

            cache = get_probe_cache()
            active = cache.get_or_create(
                template_key=probe_id,
                target_model=identity_key,
                description=task_description[:200],
            )
            active.last_used = time.time()

            logger.info(
                f"[probe_start] 探针已创建: id={probe_id} "
                f"tier={target_tier} identity={identity_key} "
                f"priority={probe_priority} ttl={ttl_seconds}s"
            )
        except Exception as e:
            logger.warning(f"[probe_start] ProbeCache 注册失败 (非致命): {e}")

        # 通过 MessageBus 通知系统有新探针
        try:
            from modules.thinking.communication.message_bus import (
                Message, MessageType, get_message_bus,
            )
            bus = get_message_bus()

            # 使用 session-specific 频道确保消息被正确的 ModelRunnerManager 接收
            channel = f"model_runner_manager_{return_to_session_id[:8]}" if return_to_session_id else "model_runner_manager"

            msg = Message(
                msg_type=MessageType.SYSTEM,
                sender="probe_tools",
                recipient=channel,
                content={
                    "action": "probe_started",
                    "probe_id": probe_id,
                    "task_id": task_id,
                    "target_tier": target_tier,
                    "identity_key": identity_key,
                    "task_description": task_description[:500],
                    "return_to_model_id": return_to_model_id,
                    "return_to_session_id": return_to_session_id,
                    "priority": priority,
                    "ttl_seconds": ttl_seconds,
                    "caller_tier": caller_tier,
                },
            )
            try:
                asyncio.get_running_loop().create_task(bus.send(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.debug(f"[probe_start] MessageBus 通知失败 (非致命): {e}")

        result = {
            "success": True,
            "probe_id": probe_id,
            "task_id": task_id,
            "target_tier": target_tier,
            "identity_key": identity_key,
            "return_to_model_id": return_to_model_id,
            "priority": probe_priority,
            "ttl_seconds": ttl_seconds,
            "message": (
                f"探针已启动: {probe_id}\n"
                f"目标层级: {target_tier}\n"
                f"身份模板: {identity_key}\n"
                f"任务: {task_description[:100]}"
            ),
        }
        if sub_session_id:
            result["sub_session_id"] = sub_session_id
            result["message"] += f"\n副会话已创建: {sub_session_id[:24]}"
        return result

    except Exception as e:
        logger.error(f"[probe_start] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# probe_stop — 停止探针
# ============================================================================

@ToolRegistry.register(
    "probe_stop",
    description="停止一个正在运行的探针并清理其资源。",
    params={"probe_id": "要停止的探针ID（从 probe_start 返回或 probe_list 查询）"},
    risk_level="MEDIUM",
    category="admin",
)
def probe_stop(probe_id: str, **kwargs) -> Dict[str, Any]:
    """停止探针"""
    try:
        caller_role = kwargs.get("_caller_role", "large")
        caller_tier = _ROLE_TO_TIER.get(caller_role, "large")

        # 确定目标 tier
        target_tier = "expert"  # 默认
        if "supervisor" in probe_id:
            target_tier = "supervisor"
        elif "expert" in probe_id:
            target_tier = "expert"

        # 权限校验 — 优先使用 ModelPermissions
        from modules.thinking.probes.probe_permission import get_probe_permission_manager
        ppm = get_probe_permission_manager()
        caller_perms = _get_caller_permissions(kwargs)
        if caller_perms is not None:
            error = ppm.validate_probe_stop_with_permissions(
                caller_perms, target_tier, caller_tier,
            )
            if error:
                return {"success": False, "error": error}
        elif not ppm.can_control(caller_tier, target_tier):
            return {
                "success": False,
                "error": f"权限不足: {caller_tier} 不能停止 {target_tier} 的探针",
            }

        # 从 ProbeCache 移除
        try:
            from modules.thinking.probes.probe_cache import get_probe_cache
            cache = get_probe_cache()
            cache._probes.pop(probe_id, None)
            cache._save_to_disk()
        except Exception as e:
            logger.debug(f"[probe_stop] ProbeCache 移除失败 (非致命): {e}")

        # 通过 MessageBus 通知系统探针已停止
        try:
            from modules.thinking.communication.message_bus import (
                Message, MessageType, get_message_bus,
            )
            bus = get_message_bus()

            # 使用 session-specific 频道
            session_id = kwargs.get("_session_id", "") or kwargs.get("return_to_session_id", "")
            channel = f"model_runner_manager_{session_id[:8]}" if session_id else "model_runner_manager"

            msg = Message(
                msg_type=MessageType.SYSTEM,
                sender="probe_tools",
                recipient=channel,
                content={
                    "action": "probe_stopped",
                    "probe_id": probe_id,
                },
            )
            try:
                asyncio.get_running_loop().create_task(bus.send(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.debug(f"[probe_stop] MessageBus 通知失败 (非致命): {e}")

        logger.info(f"[probe_stop] 探针已停止: {probe_id}")

        # —— 层级会话管理: supervisor 停止时销毁副会话 ——
        if target_tier == "supervisor":
            try:
                from modules.thinking.session import get_session_manager
                sm = get_session_manager()
                # 从 probe_id 中提取 identity_key 来找 supervisor_model_id
                from modules.thinking.identity import get_identities
                for part in probe_id.split("_"):
                    if part in get_identities():
                        template = get_identities()[part]
                        supervisor_model_id = template.get("model_id", "")
                        if supervisor_model_id:
                            destroyed = sm.destroy_sub_session(supervisor_model_id)
                            if destroyed:
                                logger.info(
                                    f"[probe_stop] 副会话已销毁: supervisor={template.get('name', part)}"
                                )
                        break
            except Exception as e:
                logger.warning(f"[probe_stop] 销毁副会话失败 (非致命): {e}")

        return {"success": True, "probe_id": probe_id, "message": f"探针 {probe_id} 已停止"}

    except Exception as e:
        logger.error(f"[probe_stop] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# probe_list — 列出活跃探针
# ============================================================================

@ToolRegistry.register(
    "probe_list",
    description="列出当前所有活跃探针及其状态（名称、目标模型、触发次数、空闲时间）。",
    params={},
    risk_level="LOW",
    category="query",
)
def probe_list(**kwargs) -> Dict[str, Any]:
    """列出所有活跃探针"""
    try:
        from modules.thinking.probes.probe_cache import get_probe_cache

        cache = get_probe_cache()
        active = cache.list_active()

        if not active:
            return {"success": True, "probes": [], "total": 0, "message": "当前无活跃探针"}

        return {
            "success": True,
            "probes": active,
            "total": len(active),
            "message": f"共 {len(active)} 个活跃探针",
        }

    except Exception as e:
        logger.error(f"[probe_list] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# memory_write — 写入模型记忆
# ============================================================================

@ToolRegistry.register(
    "memory_write",
    description="向指定模型角色的会话记忆写入一条信息。用于在模型间传递上下文。",
    params={
        "target_model_id": "目标模型ID，如 supervisor_code_001, expert_implementer_001",
        "content": "要写入的记忆内容",
        "importance": "重要度 0.0-1.0，默认为 0.5",
    },
    risk_level="LOW",
    category="mutation",
)
def memory_write(
    target_model_id: str, content: str, importance: float = 0.5, **kwargs
) -> Dict[str, Any]:
    """向指定模型写入记忆"""
    try:
        caller_role = kwargs.get("_caller_role", "large")
        caller_tier = _ROLE_TO_TIER.get(caller_role, "large")

        # 推断目标 tier
        target_tier = "expert"
        if target_model_id.startswith("large"):
            target_tier = "large"
        elif target_model_id.startswith("supervisor"):
            target_tier = "supervisor"

        # 权限校验 — 优先使用 ModelPermissions
        from modules.thinking.probes.probe_permission import get_probe_permission_manager
        ppm = get_probe_permission_manager()
        caller_perms = _get_caller_permissions(kwargs)
        if caller_perms is not None:
            if not ppm.can_modify_memory_with_permissions(
                caller_perms, target_tier, caller_tier,
            ):
                return {
                    "success": False,
                    "error": f"权限不足: {caller_tier} 不能修改 {target_tier} 的记忆",
                }
        elif not ppm.can_modify_memory(caller_tier, target_tier):
            return {
                "success": False,
                "error": f"权限不足: {caller_tier} 不能修改 {target_tier} 的记忆",
            }

        # 通过 MessageBus 发送记忆写入消息（目标模型的 ModelRunner 消费）
        try:
            from modules.thinking.communication.message_bus import (
                Message, MessageType, get_message_bus,
            )
            bus = get_message_bus()
            msg = Message(
                msg_type=MessageType.TASK_ASSIGN,
                sender="probe_tools",
                recipient=target_model_id,
                content={
                    "action": "memory_write",
                    "content": content[:2000],
                    "importance": max(0.0, min(1.0, importance)),
                    "caller_tier": caller_tier,
                },
            )
            try:
                asyncio.get_running_loop().create_task(bus.send(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.warning(f"[memory_write] MessageBus 发送失败: {e}")

        logger.info(
            f"[memory_write] {caller_tier} → {target_model_id}: "
            f"{content[:80]} (importance={importance})"
        )
        return {
            "success": True,
            "target_model_id": target_model_id,
            "importance": importance,
            "message": f"已向 {target_model_id} 写入记忆 (importance={importance})",
        }

    except Exception as e:
        logger.error(f"[memory_write] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# persona_inject — 注入人格引导
# ============================================================================

@ToolRegistry.register(
    "persona_inject",
    description=(
        "向指定模型的思考循环注入一段引导文本（相当于临时修改其系统提示词）。"
        "用于动态调整模型的行为、语气或关注点。"
    ),
    params={
        "target_model_id": "目标模型ID，如 supervisor_code_001",
        "guidance_text": "要注入的引导文本",
    },
    risk_level="MEDIUM",
    category="mutation",
)
def persona_inject(
    target_model_id: str, guidance_text: str, **kwargs
) -> Dict[str, Any]:
    """向指定模型注入引导提示词"""
    try:
        caller_role = kwargs.get("_caller_role", "large")
        caller_tier = _ROLE_TO_TIER.get(caller_role, "large")

        # 推断目标 tier
        target_tier = "expert"
        if target_model_id.startswith("large"):
            target_tier = "large"
        elif target_model_id.startswith("supervisor"):
            target_tier = "supervisor"

        # 权限校验 — 优先使用 ModelPermissions
        from modules.thinking.probes.probe_permission import get_probe_permission_manager
        ppm = get_probe_permission_manager()
        caller_perms = _get_caller_permissions(kwargs)
        if caller_perms is not None:
            if not ppm.can_modify_memory_with_permissions(
                caller_perms, target_tier, caller_tier,
            ):
                return {
                    "success": False,
                    "error": f"权限不足: {caller_tier} 不能修改 {target_tier} 的人格提示词",
                }
        elif not ppm.can_modify_memory(caller_tier, target_tier):
            return {
                "success": False,
                "error": f"权限不足: {caller_tier} 不能修改 {target_tier} 的人格提示词",
            }

        # 通过 MessageBus 发送人格注入消息
        try:
            from modules.thinking.communication.message_bus import (
                Message, MessageType, get_message_bus,
            )
            bus = get_message_bus()
            msg = Message(
                msg_type=MessageType.TASK_ASSIGN,
                sender="probe_tools",
                recipient=target_model_id,
                content={
                    "action": "persona_inject",
                    "guidance_text": guidance_text[:2000],
                    "caller_tier": caller_tier,
                },
            )
            try:
                asyncio.get_running_loop().create_task(bus.send(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.warning(f"[persona_inject] MessageBus 发送失败: {e}")

        logger.info(
            f"[persona_inject] {caller_tier} → {target_model_id}: "
            f"{guidance_text[:80]}"
        )
        return {
            "success": True,
            "target_model_id": target_model_id,
            "message": f"已向 {target_model_id} 注入引导文本",
        }

    except Exception as e:
        logger.error(f"[persona_inject] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# request_intermediate_response — 中途回复
# ============================================================================

@ToolRegistry.register(
    "request_intermediate_response",
    description=(
        "请求从当前已完成的思考中提取中间回复，发送给用户。"
        "用于大模型在主管/专家仍在工作时，先给用户一个初步答案。"
        "中间回复会以 [preliminary] 前缀显示给用户，最终回复不受影响。"
    ),
    params={
        "max_length": "可选，中间回复最大长度（字符），默认 500",
    },
    risk_level="LOW",
    category="mutation",
)
def request_intermediate_response(
    max_length: int = 500,
    **kwargs,
) -> Dict[str, Any]:
    """请求中途回复 — 大模型可以在专家工作时先回复用户

    从 CognitiveBlackboard 中提取最近的思考内容作为中间回复，
    通过 MessageBus 发送 preliminary_response 事件到 CLI。
    """
    try:
        # 从 CognitiveBlackboard 获取最近的思考内容构建中间回复
        session_id = kwargs.get("_session_id", "")
        caller_role = kwargs.get("_caller_role", "large")

        intermediate_text = ""

        # 尝试从 SessionManager 获取 CognitiveBlackboard
        try:
            from modules.thinking.session import get_session_manager
            sm = get_session_manager()

            if session_id:
                session = sm.get_session(session_id)
                dialog = session.blackboard if session else None
            else:
                # 无 session_id 时，尝试主会话
                main_session = sm.get_main_session()
                dialog = main_session.blackboard if main_session else None
        except Exception:
            dialog = None

        if dialog:
            # 读取最近的 thought 类型条目
            entries = dialog.read_dialog(limit=10)
            thoughts = [e for e in entries if e.get("type") == "thought"]
            if thoughts:
                # 取最近的思考内容
                latest = thoughts[-1]
                content = latest.get("content", "")
                # 尝试提取有意义的部分
                import re
                cleaned = re.sub(r'【[^】]+】', '', content)
                cleaned = re.sub(r'<tool_use>.*?</tool_use>', '', cleaned, flags=re.DOTALL)
                paragraphs = [p.strip() for p in cleaned.split('\n\n') if len(p.strip()) > 20]

                engine = CompressionEngine()
                max_tokens = max(max_length // 4, 50)

                if paragraphs:
                    intermediate_text = engine._truncate_to_tokens(paragraphs[-1], max_tokens)
                else:
                    intermediate_text = engine._truncate_to_tokens(content, max_tokens)

        if not intermediate_text:
            return {
                "success": False,
                "error": "暂无足够的思考内容可用于中间回复",
            }

        # 通过 MessageBus 发送 preliminary_response 事件
        try:
            from modules.thinking.communication.message_bus import (
                Message, MessageType, get_message_bus,
            )
            bus = get_message_bus()
            msg = Message(
                msg_type=MessageType.BROADCAST,
                sender="probe_tools",
                recipient="broadcast",
                content={
                    "action": "preliminary_response",
                    "content": intermediate_text,
                    "caller_tier": _ROLE_TO_TIER.get(caller_role, "large"),
                },
                metadata={"event": "preliminary_response"},
            )
            try:
                asyncio.get_running_loop().create_task(bus.send(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.warning(f"[intermediate_response] MessageBus 发送失败: {e}")

        logger.info(
            f"[intermediate_response] {caller_role} 发送中途回复 "
            f"({len(intermediate_text)} 字符)"
        )
        return {
            "success": True,
            "content": intermediate_text,
            "message": (
                f"中途回复已发送 ({len(intermediate_text)} 字符)\n"
                f"{intermediate_text[:200]}"
            ),
        }

    except Exception as e:
        logger.error(f"[intermediate_response] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# recall_guidance — 按需召回操作指南
# ============================================================================

# 模块级存储：编排器注入的当前会话引导文本
_session_guidance: Dict[str, Dict[str, str]] = {}


def set_session_guidance(session_id: str, guidance: Dict[str, str]) -> None:
    """编排器调用，写入当前会话的引导文本供 recall_guidance 查询"""
    _session_guidance[session_id] = guidance


def clear_session_guidance(session_id: str) -> None:
    """会话结束时清理"""
    _session_guidance.pop(session_id, None)


@ToolRegistry.register(
    "recall_guidance",
    description=(
        "当你需要查询系统操作指南或可用资源时调用此工具。"
        "这是一个参考文档查询工具，不会执行任何操作，只返回信息。"
        "当你需要记住某个主题的详细方法时，随时可以调用它来「想起来」。"
    ),
    params={
        "topic": (
            "查询主题: delegation(如何委托任务给主管/专家模型，含6个探针工具用法和9个身份模板)、"
            "identities(可用模型身份模板速查)、"
            "expert_context(当前会话的情绪/价值观/安全上下文)"
        ),
    },
    risk_level="LOW",
    category="query",
)
def recall_guidance(topic: str = "delegation", **kwargs) -> Dict[str, Any]:
    """按需召回操作指南 —— 大模型在想用时主动查询

    三个主题：
    - delegation: 完整委托指南（工具用法 + 委托工作流 + 注意事项）
    - identities: 可用身份模板速查表
    - expert_context: 当前会话的专家分析结果
    """
    topic = topic.strip().lower()
    session_id = kwargs.get("_session_id", "")

    if topic in ("delegation", "delegate", "委托", "guide"):
        return {
            "success": True,
            "topic": "delegation",
            "content": _build_delegation_reference(),
        }

    if topic in ("identities", "identity", "身份", "模板", "template"):
        return {
            "success": True,
            "topic": "identities",
            "content": _build_identities_reference(),
        }

    if topic in ("expert_context", "expert", "context", "专家", "上下文"):
        guidance = _session_guidance.get(session_id, {})
        if not guidance:
            return {
                "success": True,
                "topic": "expert_context",
                "content": "当前无缓存的专家上下文。请基于用户输入自行判断。",
            }
        return {
            "success": True,
            "topic": "expert_context",
            "content": (
                f"情绪: {guidance.get('emotion', 'neutral')} (强度 {guidance.get('intensity', '0.5')})\n"
                f"语气引导: {guidance.get('tone', '')}\n"
                f"价值观: {guidance.get('values', '')}\n"
                f"策略: {guidance.get('strategy', '')}\n"
                f"风险等级: {guidance.get('risk', 'none')}\n"
                f"安全建议: {guidance.get('safety', '')}"
            ),
        }

    return {
        "success": False,
        "error": f"未知主题: {topic}，可用: delegation, identities, expert_context",
    }


def _build_delegation_reference() -> str:
    """构建委托指南参考文档"""
    return """【多模型协作委托指南】

## 委托方式

使用 `delegate_task` 工具委托任务给主管，主管会自动调度专家执行。

## 可用主管（按 role 参数）

| role 参数 | 主管名称 | 专长 |
|-----------|---------|------|
| code_supervisor | 代码主管 | 代码审查、架构设计、技术风险评估、测试策略 |
| query_supervisor | 查询主管 | 信息检索、数据分析、知识整合、需求澄清 |

## 三阶段工作流

1. 分析任务 → 判断是否需要委托（简单问题自己回答）
2. 用 `delegate_task(role="code_supervisor", task="...")` 委托给对应主管
3. 主管会按"目标分析→规划与委托→等待整合"三阶段执行
4. 主管完成后结果自动返回，你整合后回复用户

## 注意事项

- 不要在简单问题上过度委托（如"你好"、"1+1等于几"）
- 给主管的 task_description 要清晰具体
- 你是唯一与用户交互的出口
- **重要**：delegate_task 参数是 `role`（如 code_supervisor），不是 identity_key"""


def _build_identities_reference() -> str:
    """构建身份模板速查表（delegate_task 参数使用 role 而非 identity_key）"""
    return """【可用模型身份模板速查】

## 主管 (supervisor) — 委托时使用 role 参数

| role（delegate_task参数） | 主管名称 | 专长 |
|-------------------------|---------|------|
| code_supervisor | 代码主管 | 代码审查、架构设计、技术风险评估、测试策略 |
| query_supervisor | 查询主管 | 信息检索、数据分析、知识整合、需求澄清 |
| creative_supervisor | 创意主管 | 创意规划、内容结构、需求分析、结果整合 |

## 专家 (expert) — 主管通过委托激活

| role（可被主管调度） | 专家名称 | 专长 |
|----------------------|---------|------|
| code_reviewer | 审查专家 | 代码审查、安全审计、代码规范检查 |
| code_writer | 实现专家 | 代码实现、算法设计、重构、性能优化 |
| test_writer | 测试专家 | 测试编写、边界分析、回归测试 |
| data_analyzer | 分析专家 | 数据分析、信息检索、趋势分析 |
| emotion | 情绪分析师 | 情绪识别、语气指导、共情沟通 |
| memory_manager | 记忆管理员 | 记忆分类、语义检索、索引维护 |

## 说明

- **identity_key**: 内部配置名（如 supervisor_code），对模型不可见
- **role**: delegate_task 调用时使用的参数值（如 code_supervisor）
- 总指挥可直接委托主管，主管内部管理专家调度"""


# ============================================================================
# view_sub_session — 查看副会话聊天记录
# ============================================================================

@ToolRegistry.register(
    "view_sub_session",
    description=(
        "查看主管模型的副会话聊天记录。总指挥(大模型)用于了解主管和专家在副会话中的工作进展。"
        "每个主管启动时会自动创建独立的副会话，副会话中主管与专家协作。"
        "正常情况下总指挥只看主会话，需要检查副会话进展时调用此工具。"
    ),
    params={
        "supervisor_name": "主管中文名，如 '代码主管' 或 '查询主管'",
        "limit": "可选，返回最近 N 条记录，默认 30",
    },
    risk_level="LOW",
    category="query",
)
def view_sub_session(supervisor_name: str = "", limit: int = 30, **kwargs) -> Dict[str, Any]:
    """查看副会话聊天记录（总指挥专用）

    Args:
        supervisor_name: 主管中文名（"代码主管" / "查询主管"）
        limit: 返回最近 N 条记录

    Returns:
        格式化的聊天记录
    """
    try:
        from modules.thinking.session import get_session_manager
        sm = get_session_manager()

        # 如果没指定主管名，列出所有可用副会话
        if not supervisor_name:
            subs = sm.list_sub_sessions()
            if not subs:
                return {
                    "success": True,
                    "result": "当前没有活跃的副会话。请先委托任务给主管模型。",
                }
            available = [s.supervisor_name for s in subs if s.supervisor_name]
            return {
                "success": True,
                "result": (
                    f"当前有 {len(subs)} 个活跃副会话。\n"
                    f"可用主管: {', '.join(available) if available else '(无名称)'}\n"
                    f"请指定 supervisor_name 查看具体副会话内容。"
                ),
            }

        chat_text = sm.view_sub_session(
            supervisor_name=supervisor_name,
            limit=limit,
        )
        return {"success": True, "result": chat_text}

    except Exception as e:
        logger.error(f"[view_sub_session] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# memory_search — 记忆检索工具（所有模型可用）
# ============================================================================

@ToolRegistry.register(
    "memory_search",
    description=(
        "搜索记忆库，支持按分类、时间范围、模型ID进行语义+关键词混合检索。"
        "所有模型都可以使用此工具检索自己或其他模型的记忆（受权限限制）。"
        "分类选项: dialog(对话) / thought(思考) / summary(总结) / preference(偏好) / evolution(进化) / event(事件)"
        "时间范围: 1h / 6h / 24h / 7d / 30d / all"
    ),
    params={
        "query": "搜索查询文本，支持自然语言描述",
        "category": "可选，记忆类型过滤: dialog/thought/summary/preference/evolution/event",
        "time_range": "可选，时间范围: 1h/6h/24h/7d/30d/all，默认 7d",
        "limit": "可选，返回条数，默认 10",
        "model_id": "可选，搜索特定模型的记忆（默认搜索自己的记忆）",
    },
    risk_level="LOW",
    category="query",
)
def memory_search(
    query: str = "",
    category: str = None,
    time_range: str = "7d",
    limit: int = 10,
    model_id: str = None,
    **kwargs
) -> Dict[str, Any]:
    """搜索记忆库 — 语义+关键词混合检索

    优先通过 MemoryManagerExpert 检索（如果常驻运行），
    回退到直接实例化 MemoryManager。

    Args:
        query: 搜索查询文本
        category: 记忆类型过滤 (dialog/thought/summary/preference/evolution/event)
        time_range: 时间范围 (1h/6h/24h/7d/30d/all)
        limit: 返回条数
        model_id: 搜索特定模型的记忆

    Returns:
        搜索结果 {success, result, count, entries}
    """
    if not query:
        return {"success": False, "error": "query 参数不能为空"}

    try:
        # 获取 caller 信息
        caller_model_id = kwargs.get("_caller_model_id", "")
        session_id = kwargs.get("_session_id", "")

        # 优先级1: 通过 MemoryManagerExpert 检索（常驻运行中）
        try:
            from modules.thinking.experts.memory_manager_expert import MemoryManagerExpert
            # 尝试通过已有的 runner_manager 获取
            from modules.thinking.core.model_runner import get_runner_manager
            rm = get_runner_manager(session_id or "")
            expert_runner = None
            for rid, runner in rm.get_active_runners().items():
                if getattr(runner, 'identity_key', '') == 'expert_memory_manager':
                    expert_runner = runner
                    break

            if expert_runner and hasattr(expert_runner, '_expert_instance'):
                expert = expert_runner._expert_instance
                if isinstance(expert, MemoryManagerExpert):
                    results = expert.search(
                        query=query,
                        category=category,
                        time_range=time_range or "7d",
                        limit=limit or 10,
                        model_id=model_id or caller_model_id or None,
                    )
                    search_method = "MemoryManagerExpert"
                    return {
                        "success": True,
                        "result": f"找到 {len(results)} 条相关记忆（通过记忆管理员）",
                        "count": len(results),
                        "entries": results,
                        "search_method": search_method,
                    }
        except Exception as e:
            logger.debug(f"[memory_search] 通过 MemoryManagerExpert 检索失败: {e}")

        # 优先级2: 直接实例化 MemoryManager
        from modules.memory.core.memory_manager import MemoryManager
        effective_model_id = model_id or caller_model_id or ""
        mm_config = None
        if effective_model_id:
            from modules.memory.core.memory_config import get_default_config
            # 根据 model_id 推断 tier
            tier = "expert"
            if "large" in effective_model_id:
                tier = "large"
            elif "supervisor" in effective_model_id:
                tier = "supervisor"
            mm_config = get_default_config(tier, effective_model_id)

        mm = MemoryManager(
            model_id=effective_model_id,
            config=mm_config,
        )

        results = mm.search_memories_by_category(
            query=query,
            category=category,
            time_range=time_range or "7d",
            limit=limit or 10,
        )

        return {
            "success": True,
            "result": f"找到 {len(results)} 条相关记忆",
            "count": len(results),
            "entries": results,
            "search_method": "MemoryManager",
        }

    except Exception as e:
        logger.error(f"[memory_search] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# probe_create — 动态创建新的主管身份
# ============================================================================

@ToolRegistry.register(
    "probe_create",
    description=(
        "动态创建新的主管身份，用于扩展系统功能。"
        "仅特定专家角色可使用此工具。"
        "返回新身份的标识和探针ID。"
    ),
    params={
        "target_tier": "目标层级: supervisor 或 expert",
        "identity_key": "新身份的唯一标识，如 supervisor_security_001",
        "personality": "人物设定描述，如'安全审查专家，严谨高效'",
        "expertise": "擅长领域，逗号分隔，如'代码安全,架构设计,最佳实践'",
        "speaking_style": "可选，说话风格，默认'直接、高效'",
        "task_description": "可选，初始任务描述，用于启动新身份的首轮思考",
    },
    risk_level="MEDIUM",
    category="system",
)
def probe_create(
    target_tier: str,
    identity_key: str,
    personality: str,
    expertise: str,
    speaking_style: str = "直接、高效",
    task_description: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """动态创建并启动新的主管身份。

    工具专家使用此工具来创建系统所需的新身份，当委托的角色不存在时。
    """
    try:
        from modules.thinking.identity import get_identities

        # 验证权限
        caller_role = kwargs.get("_caller_role", "")
        if caller_role not in ("expert", "supervisor", "large"):
            return {
                "success": False,
                "error": f"无权限创建新身份，仅工具专家可用。当前身份: {caller_role}",
            }

        # 检查身份是否已存在
        if identity_key in get_identities():
            return {
                "success": False,
                "error": f"身份 {identity_key} 已存在，无需重复创建",
            }

        # 解析expertise字段
        expertise_list = [e.strip() for e in expertise.split("，") if e.strip()]
        if not expertise_list:
            expertise_list = [personality.split("，")[0] if "，" in personality else "通用"]

        # 创建新的身份模板
        new_identity = {
            "model_id": f"{identity_key}_001",
            "name": personality.split("，")[0] if "，" in personality else "新身份",
            "tier": target_tier,
            "role": identity_key,
            "personality": personality,
            "speaking_style": speaking_style,
            "expertise": expertise_list,
            "weaknesses": ["不在职责范围内的任务"],
        }

        # 动态注册到 get_identities()
        get_identities()[identity_key] = new_identity
        logger.info(f"[probe_create] 新身份已注册: {identity_key}")

        # 如果提供了任务描述，立即启动该身份
        probe_id = ""
        if task_description or identity_key:
            try:
                result = probe_start(
                    target_tier=target_tier,
                    identity_key=identity_key,
                    task_description=task_description or f"你被创建为 {identity_key}，请根据你的职责执行任务。",
                    probe_priority="MEDIUM",
                    ttl_seconds=3600,
                    **kwargs,
                )
                if result.get("success"):
                    probe_id = result.get("probe_id", "")
                    logger.info(f"[probe_create] 新身份已启动: probe={probe_id}")
                else:
                    logger.warning(f"[probe_create] 启动新身份失败: {result.get('error')}")
            except Exception as e:
                logger.warning(f"[probe_create] 启动新身份出错: {e}")

        return {
            "success": True,
            "identity_key": identity_key,
            "probe_id": probe_id,
            "message": f"新身份 {identity_key} 已创建并启动（探针ID: {probe_id}）",
        }
    except Exception as e:
        logger.error(f"[probe_create] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# tool_download — 下载和注册新工具
# ============================================================================

@ToolRegistry.register(
    "tool_download",
    description=(
        "下载并注册新工具到系统中。"
        "仅特定专家角色可使用此工具。"
    ),
    params={
        "tool_name": "工具名称，如 tool_security_scan",
        "plugin_name": "可选，插件名称",
        "source_url": "可选，下载源URL（暂不支持，建议手动放置文件）",
    },
    risk_level="MEDIUM",
    category="system",
)
def tool_download(
    tool_name: str,
    plugin_name: str = "",
    source_url: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """下载和注册新工具。

    当模型需要使用不存在的工具时，调用此工具来发现和注册。
    """
    try:
        from infra.tool_manager.tool_registry import ToolRegistry as TR

        # 验证权限
        caller_role = kwargs.get("_caller_role", "")
        if caller_role not in ("expert", "supervisor", "large"):
            return {
                "success": False,
                "error": f"无权限下载工具，仅工具专家可用。当前身份: {caller_role}",
            }

        # 检查工具是否已注册
        func = TR.get_func(tool_name)
        if func:
            logger.info(f"[tool_download] 工具 {tool_name} 已注册")
            return {
                "success": True,
                "tool_name": tool_name,
                "message": f"工具 {tool_name} 已在本地注册，可直接使用。"
            }

        # 从 URL 下载（如果提供）
        if source_url:
            logger.warning(f"[tool_download] 从 URL 下载工具暂未实现")
            return {
                "success": False,
                "tool_name": tool_name,
                "message": f"从 URL 下载工具暂不支持。请通过插件系统安装或手动实现工具。",
            }

        # 未找到工具
        return {
            "success": False,
            "tool_name": tool_name,
            "message": f"工具 {tool_name} 未找到。请检查："
                      f"1. 工具名是否正确；"
                      f"2. 内置工具是否已加载；"
                      f"3. 是否安装并启用了对应的插件。",
        }
    except Exception as e:
        logger.error(f"[tool_download] 失败: {e}")
        return {"success": False, "tool_name": tool_name, "message": str(e)}
