"""
统一会话管理器 — 管理主/副会话的创建、路由、查询

层级会话结构:
- 主会话 (Main Session): 总指挥(large) + 主管(supervisor) + 部分直接专家
- 副会话 (Sub-Session): 主管 + 分配给它的专家（自动创建）

消息路由规则:
- Large (总指挥): 只写入主会话
- Supervisor (主管): 写入主会话 + 自己的副会话
- Expert in sub-session: 只写入副会话
- Expert in main session: 只写入主会话
- Tool Expert: 写入所在会话
"""
import time
import threading as _threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set
from utils.logger import setup_logger
from modules.thinking.cognition.blackboard import CognitiveBlackboard

logger = setup_logger("session_manager")


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

@dataclass
class Session:
    """单个会话容器 — 拥有独立的 CognitiveBlackboard 和 ModelRunnerManager"""

    session_id: str                                    # 唯一标识
    parent_session_id: str = ""                        # "" 表示主会话（根节点）
    blackboard: Any = None                          # CognitiveBlackboard 实例
    runner_manager: Any = None                         # ModelRunnerManager 实例
    supervisor_id: str = ""                            # 拥有此副会话的主管 model_id
    supervisor_name: str = ""                          # 主管中文名（用于工具查询）
    participants: Set[str] = field(default_factory=set)  # 可在此发言的 model_id
    created_at: float = 0.0
    last_user_message_time: float = 0.0  # 用户上次说话时间戳（非用户触发不算）

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    @property
    def is_main(self) -> bool:
        return not self.parent_session_id

    @property
    def is_sub(self) -> bool:
        return bool(self.parent_session_id)

    def can_participant_speak(self, model_id: str) -> bool:
        """检查 model_id 是否可以在此会话发言"""
        return model_id in self.participants

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "is_main": self.is_main,
            "parent_session_id": self.parent_session_id or None,
            "supervisor_id": self.supervisor_id or None,
            "supervisor_name": self.supervisor_name or None,
            "participant_count": len(self.participants),
            "dialog_size": self.blackboard.size() if self.blackboard else 0,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """统一会话管理器

    替换分散的全局 dict（_dialogs, _runner_managers），成为所有会话组件的单一查找点。

    用法:
        sm = SessionManager()
        main = sm.create_main_session("abc123")
        sub = sm.create_sub_session("abc123", "supervisor_code_001", "代码主管")
        dialog = sm.get_session("abc123").blackboard
    """

    def __init__(self):
        self._sessions: Dict[str, Session] = {}           # session_id → Session
        self._supervisor_to_sub: Dict[str, str] = {}      # supervisor_model_id → sub_session_id
        self._main_session_id: str = ""

    # ------------------------------------------------------------------
    # 创建 / 销毁
    # ------------------------------------------------------------------

    def _reset_session_for_new_turn(self, session: Session) -> None:
        """完整重置会话状态，准备处理新一轮对话"""
        # 记录上一轮结束时间（仅在非首次时更新，由外部设置 last_user_message_time）
        # 不在这里更新 last_user_message_time，因为重置可能是感知系统触发的

        # 1. 先等旧 runner 停止（最多 3s），防止旧 runner 写新 dialog
        if session.runner_manager:
            try:
                import threading
                import asyncio
                done = threading.Event()
                def _shutdown():
                    try:
                        asyncio.run(session.runner_manager.shutdown_all())
                    except Exception as e:
                        logger.debug(f"[SessionManager] runner shutdown 异常: {e}")
                    done.set()
                t = threading.Thread(target=_shutdown, daemon=True)
                t.start()
                done.wait(timeout=3.0)
                logger.info(f"[SessionManager] runner_manager 关闭完成")
            except Exception as e:
                logger.warning(f"[SessionManager] 关闭 runner_manager 失败: {e}")
            session.runner_manager = None

        # 2. runner 停后再清 dialog（无竞态）
        if session.blackboard:
            logger.info(
                f"[SessionManager] 清理共享对话框: "
                f"总计 {session.blackboard.size()} 条entries"
            )
            session.blackboard.clear_turn_state()

        # 3. CognitiveBlackboard 无需重建，clear_turn_state 已足够
        # 4. 清理参与者集合（新一轮重新分配）
        session.participants.clear()

        logger.info(
            f"[SessionManager] 会话 {session.session_id} 已完整重置，"
            f"共享对话框已重建为空: {session.blackboard.size()} 条entries"
        )

    def create_main_session(self, session_id: str = "") -> Session:
        """创建主会话（每个用户对话只有一个主会话）

        警告：如果使用相同的 session_id 多次调用，会完整重置旧会话状态并重用会话元数据。
        """
        if not session_id:
            session_id = f"main_{uuid.uuid4().hex[:12]}"

        # 已存在则完整重置会话状态，重用会话
        existing = self._sessions.get(session_id)
        if existing is not None:
            logger.info(
                f"[SessionManager] 主会话已存在，完整重置: {session_id}"
            )
            self._reset_session_for_new_turn(existing)
            return existing

        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        dialog = CognitiveBlackboard(session_id=session_id, turn_id=turn_id)
        session = Session(
            session_id=session_id,
            parent_session_id="",
            blackboard=dialog,
        )
        self._sessions[session_id] = session
        self._main_session_id = session_id
        logger.info(f"[SessionManager] 创建主会话: {session_id}")
        return session

    def create_sub_session(
        self,
        parent_session_id: str,
        supervisor_model_id: str,
        supervisor_name: str = "",
    ) -> Session:
        """为主管模型创建副会话

        副会话拥有独立的 CognitiveBlackboard。同一主管重复调用返回已有副会话。

        Args:
            parent_session_id: 主会话 ID
            supervisor_model_id: 主管的 model_id
            supervisor_name: 主管中文名（如 "代码主管"）

        Returns:
            新建或已有的副会话
        """
        # 如果此主管已有副会话，直接返回
        existing_sub_id = self._supervisor_to_sub.get(supervisor_model_id)
        if existing_sub_id and existing_sub_id in self._sessions:
            logger.debug(
                f"[SessionManager] 主管 {supervisor_name} 已有副会话: {existing_sub_id}"
            )
            return self._sessions[existing_sub_id]

        sub_session_id = f"sub_{supervisor_model_id}_{uuid.uuid4().hex[:8]}"
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        dialog = CognitiveBlackboard(session_id=sub_session_id, turn_id=turn_id)

        # 创建独立的 runner_manager（延迟创建，避免循环导入）
        runner_mgr = None

        session = Session(
            session_id=sub_session_id,
            parent_session_id=parent_session_id,
            blackboard=dialog,
            runner_manager=runner_mgr,
            supervisor_id=supervisor_model_id,
            supervisor_name=supervisor_name,
            participants=set(),
        )
        # 主管自己是副会话的第一个参与者
        session.participants.add(supervisor_model_id)

        self._sessions[sub_session_id] = session
        self._supervisor_to_sub[supervisor_model_id] = sub_session_id

        logger.info(
            f"[SessionManager] 创建副会话: {sub_session_id} "
            f"主管={supervisor_name}({supervisor_model_id}) "
            f"父会话={parent_session_id}"
        )
        return session

    def destroy_sub_session(self, supervisor_model_id: str) -> bool:
        """销毁主管对应的副会话（probe_stop 时调用）"""
        sub_session_id = self._supervisor_to_sub.pop(supervisor_model_id, None)
        if not sub_session_id:
            return False

        session = self._sessions.pop(sub_session_id, None)
        if session is None:
            return False

        # 清理 runner_manager
        if session.runner_manager:
            try:
                import asyncio
                asyncio.create_task(session.runner_manager.shutdown())
            except Exception as e:
                logger.warning(f"[SessionManager] 关闭 runner_manager 失败: {e}")

        logger.info(
            f"[SessionManager] 销毁副会话: {sub_session_id} "
            f"主管={session.supervisor_name}"
        )
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取指定会话"""
        return self._sessions.get(session_id)

    def get_main_session(self) -> Optional[Session]:
        """获取主会话"""
        return self._sessions.get(self._main_session_id)

    def get_sub_for_supervisor(self, supervisor_model_id: str) -> Optional[Session]:
        """获取主管对应的副会话"""
        sub_id = self._supervisor_to_sub.get(supervisor_model_id)
        if sub_id:
            return self._sessions.get(sub_id)
        return None

    def get_sub_by_name(self, supervisor_name: str) -> Optional[Session]:
        """通过主管中文名查找副会话"""
        for sub_id in self._supervisor_to_sub.values():
            session = self._sessions.get(sub_id)
            if session and session.supervisor_name == supervisor_name:
                return session
        return None

    def list_sub_sessions(self) -> List[Session]:
        """列出所有副会话"""
        return [s for s in self._sessions.values() if s.is_sub]

    def list_all_sessions(self) -> List[Session]:
        """列出所有会话"""
        return list(self._sessions.values())

    # ------------------------------------------------------------------
    # 参与者管理
    # ------------------------------------------------------------------

    def add_participant(self, session_id: str, model_id: str) -> bool:
        """将 model_id 添加为会话参与者"""
        session = self._sessions.get(session_id)
        if session is None:
            logger.warning(f"[SessionManager] 会话不存在: {session_id}")
            return False
        session.participants.add(model_id)
        logger.debug(f"[SessionManager] {model_id} 加入会话 {session_id[:16]}")
        return True

    def remove_participant(self, session_id: str, model_id: str) -> bool:
        """从会话移除参与者"""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.participants.discard(model_id)
        return True

    def can_speak(self, model_id: str, session_id: str) -> bool:
        """检查 model_id 是否可以在指定会话发言"""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        return session.can_participant_speak(model_id)

    # ------------------------------------------------------------------
    # 会话内容查看
    # ------------------------------------------------------------------

    def view_sub_session(
        self,
        supervisor_name: str = "",
        supervisor_model_id: str = "",
        limit: int = 30,
    ) -> str:
        """供总指挥查看副会话聊天记录

        Args:
            supervisor_name: 主管中文名（如 "代码主管"）
            supervisor_model_id: 主管 model_id（备选查找方式）
            limit: 返回最近 N 条记录

        Returns:
            格式化的聊天记录文本，或错误提示
        """
        session = None

        if supervisor_name:
            session = self.get_sub_by_name(supervisor_name)
        if session is None and supervisor_model_id:
            session = self.get_sub_for_supervisor(supervisor_model_id)

        if session is None:
            available = [
                s.supervisor_name
                for s in self.list_sub_sessions()
                if s.supervisor_name
            ]
            if available:
                return (
                    f"未找到副会话。可用的主管: {', '.join(available)}"
                )
            return "当前没有活跃的副会话。请先委托任务给主管模型。"

        dialog = session.blackboard
        entries = dialog.read_dialog(limit=limit)

        if not entries:
            return f"副会话 [{session.supervisor_name}] 暂无聊天记录。"

        lines = [
            f"=== 副会话 [{session.supervisor_name}] "
            f"({len(entries)} 条记录) ===",
            f"会话ID: {session.session_id}",
            "",
        ]
        for entry in entries:
            tier_label = {"large": "总指挥", "supervisor": "主管",
                          "expert": "专家", "user": "用户"}.get(
                entry.get("tier", ""), entry.get("tier", "")
            )
            model_id = entry.get("model_id", "unknown")
            content = entry.get("content", "")
            entry_type = entry.get("entry_type", "")
            type_mark = {"thought": "思考", "response": "回复",
                         "user_input": "输入"}.get(entry_type, entry_type)
            lines.append(
                f"[{tier_label}] {model_id} ({type_mark}): "
                f"{content[:300]}"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """获取会话系统整体状态"""
        main = self.get_main_session()
        subs = self.list_sub_sessions()
        return {
            "main_session_id": self._main_session_id,
            "main_dialog_size": main.blackboard.size() if main else 0,
            "sub_session_count": len(subs),
            "sub_sessions": [
                {
                    "session_id": s.session_id,
                    "supervisor_name": s.supervisor_name,
                    "participants": len(s.participants),
                    "dialog_size": s.blackboard.size() if s.blackboard else 0,
                }
                for s in subs
            ],
        }

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """清理所有会话"""
        for session in self._sessions.values():
            if session.blackboard:
                session.blackboard.clear_turn_state()
        self._sessions.clear()
        self._supervisor_to_sub.clear()
        self._main_session_id = ""
        logger.info("[SessionManager] 所有会话已清理")


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_session_manager: Optional[SessionManager] = None
_session_manager_lock = _threading.Lock()


def get_session_manager() -> SessionManager:
    """获取全局 SessionManager 单例"""
    global _session_manager
    if _session_manager is None:
        with _session_manager_lock:
            if _session_manager is None:
                _session_manager = SessionManager()
    return _session_manager
