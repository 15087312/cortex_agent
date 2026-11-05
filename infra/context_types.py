"""上下文片段的共享契约类型。

为什么放在 infra
    `ContextFragment` 是感知、记忆、良知等**多个来源**共同产出的数据单位：
    每个 Source 产出一个 Fragment，交给 TurnContext 池化。它原本定义在
    `modules/thinking/context/pool.py`，于是产生了一个逻辑循环：

        perception → thinking   感知侧需要 Fragment 类型（perception/pool.py）
        thinking   → perception 思考侧需要取感知快照（sources/perception_source.py）

    但 `ContextFragment` 是**纯数据**：@dataclass，零行为、零依赖、零 IO。
    它被两个模块共享，不属于任何一方。下沉 infra 后双方都只依赖 infra，
    循环消失，且没有引入任何新的间接层——类型定义仍只有一份。

约定
    - 这是**契约**类型：只增字段，不改语义。新增字段必须有默认值，
      否则所有既有构造点会同时破裂。
    - `target_roles` 用 Tuple 而非 List：Fragment 会被多个角色视图共享，
      可变默认值会在视图间串改。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class ContextFragment:
    """上下文片段 — 每个 Source 产出一个"""
    source: str                          # "memory" | "perception" | "conscience" | ...
    content: str                         # 格式化后的纯文本
    target_roles: Tuple[str, ...]        # 哪些角色应看到此片段
    section_title: str                   # prompt 中显示的标题
    priority: int = 0                    # 排序权重，越小越靠前
    ttl_turns: int = 0                   # 0=仅本轮, -1=永久
