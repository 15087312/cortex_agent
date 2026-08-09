"""感知触发思考 — 感知到高强度变化后自动触发 AI 主动思考

- 由 PERCEPTION_TRIGGER_THINK 全局开关控制（设置页可关）
- 冷却 PERCEPTION_TRIGGER_COOLDOWN 秒 / 强度阈值 PERCEPTION_TRIGGER_MIN_INTENSITY
- 触发时复用 call_outreach_llm（与主动搭话同一 LLM 链路）生成主动消息并推前端
"""
import asyncio
import threading
import time
from typing import List

from utils.logger import setup_logger

logger = setup_logger("trigger_think")

_state = {"last": 0.0}
_lock = threading.Lock()


def register() -> None:
    """注册到差异检测器的高强度回调"""
    from modules.perception.difference import get_detector
    get_detector().on_high_intensity(_trigger)
    logger.info("感知触发思考已注册（高强度差异 → AI 主动思考）")


def _has_active_connections() -> bool:
    """前端是否在线且推送链路可达（发握手确认，通了才算）。

    无连接时不触发——主动消息广播给空连接会直接丢失，还白耗一次 LLM 调用。
    """
    try:
        from modules.perception.trigger import confirm_frontend_connection
        return confirm_frontend_connection()
    except Exception:
        return False


def _trigger(differences: List) -> None:
    if not _has_active_connections():
        logger.debug("无活跃前端连接，跳过感知触发思考")
        return
    from config.settings import settings
    cd = max(1, int(getattr(settings, "PERCEPTION_TRIGGER_COOLDOWN", 60) or 60))
    min_int = float(getattr(settings, "PERCEPTION_TRIGGER_MIN_INTENSITY", 50) or 50)
    with _lock:
        now = time.time()
        if now - _state["last"] < cd:
            return
        _state["last"] = now
    strong = [d for d in differences if float(getattr(d, "intensity", 0) or 0) >= min_int]
    if not strong:
        return
    desc = "、".join(
        f"{getattr(d, 'source_type', '?')}:{str(getattr(d, 'description', ''))[:30]}"
        for d in strong[:3]
    )
    threading.Thread(target=_run, args=(desc,), daemon=True).start()


def _run(desc: str) -> None:
    try:
        asyncio.run(_think(desc))
    except Exception as e:
        logger.debug(f"触发思考执行失败: {e}")


async def _think(desc: str) -> None:
    from modules.perception.trigger import call_outreach_llm
    from modules.thinking.frontend_channel import generate_and_push

    await generate_and_push(
        None,  # 广播：任意活跃连接可达即推送
        lambda: call_outreach_llm(
            f"检测到环境高强度变化（{desc}）。请自然简短地关心/提醒用户（1-2 句），不要提'感知'或'检测'。",
            "",
        ),
        msg_type="proactive",
        event="trigger_think",
        role="assistant",
        data={"label": "感知触发", "source": "trigger_think"},
    )
