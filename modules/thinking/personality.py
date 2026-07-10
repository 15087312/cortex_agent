"""
人格系统 — 切换说话风格和语气

人格与身份系统的关系：
- 身份（Identity）定义角色的能力（擅长/不擅长）、角色名称
- 人格（Personality）只定义说话风格和语气，运行时可随时切换
- 人格是对身份的上层覆盖，不影响模型能力
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("personality")

PERSONALITIES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "personalities",
)


@dataclass
class Personality:
    """人格定义"""
    id: str
    name: str
    style: str = ""
    voice_rules: str = ""


# 加载器缓存
_personalities: Dict[str, Personality] = {}
_loaded = False


def _load_all() -> Dict[str, Personality]:
    """扫描 personalities 目录加载所有人格"""
    global _loaded, _personalities
    if _loaded:
        return _personalities

    result: Dict[str, Personality] = {}
    if not os.path.isdir(PERSONALITIES_DIR):
        logger.warning(f"[人格] 目录不存在: {PERSONALITIES_DIR}")
        _personalities = result
        _loaded = True
        return result

    for fname in sorted(os.listdir(PERSONALITIES_DIR)):
        if not fname.endswith(".yaml") and not fname.endswith(".yml"):
            continue
        fpath = os.path.join(PERSONALITIES_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or "id" not in data:
                continue
            personality = Personality(
                id=str(data["id"]),
                name=str(data.get("name", data["id"])),
                style=str(data.get("style", "")),
                voice_rules=str(data.get("voice_rules", "")),
            )
            result[personality.id] = personality
        except Exception as e:
            logger.warning(f"[人格] 加载失败: {fname} — {e}")

    _personalities = result
    _loaded = True
    logger.info(f"[人格] 已加载 {len(result)} 个人格: {list(result.keys())}")
    return result


def get_personality(personality_id: str) -> Optional[Personality]:
    """获取指定人格；不存在则返回 None"""
    all_p = _load_all()
    return all_p.get(personality_id)


def list_personalities() -> Dict[str, Personality]:
    """列出所有人格"""
    return dict(_load_all())


def reload_personalities() -> None:
    """强制重新加载（文件变更时使用）"""
    global _loaded
    _loaded = False
    _load_all()
