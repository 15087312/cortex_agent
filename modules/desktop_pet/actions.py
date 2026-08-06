"""桌宠互动动作模板 — 19 动作 5 分类（借鉴桌宠养成游戏）

每动作：id / label / category / icon(Lucide 名称) / prompt(给模型的情境陈述) / effects(状态增减)
prompt 只陈述互动发生的事实，不规定模型如何回复（避免影响模型行为）。
icon 名称用于前端圆环菜单渲染 Lucide SVG + Chat 页互动消息图标。
"""
from typing import Dict, List, Optional

# 分类（圆环内圈）
CATEGORIES: List[Dict] = [
    {"id": "feed", "label": "喂食", "icon": "utensils"},
    {"id": "pet", "label": "抚摸", "icon": "hand"},
    {"id": "play", "label": "玩耍", "icon": "gamepad-2"},
    {"id": "care", "label": "照顾", "icon": "sparkles"},
    {"id": "mood", "label": "心情", "icon": "smile"},
]

# 动作（圆环外圈）
ACTIONS: List[Dict] = [
    # ── 喂食 ──
    {"id": "cake", "label": "送蛋糕", "category": "feed", "icon": "cake",
     "prompt": "你送来了一个美味的蛋糕给桌宠吃。",
     "effects": {"satiety": 25, "mood": 8}},
    {"id": "fish", "label": "喂小鱼", "category": "feed", "icon": "fish",
     "prompt": "你递给桌宠一条新鲜的小鱼。",
     "effects": {"satiety": 22, "mood": 6}},
    {"id": "meat", "label": "喂肉肉", "category": "feed", "icon": "beef",
     "prompt": "你端来一份香喷喷的烤肉给桌宠。",
     "effects": {"satiety": 25, "mood": 5}},
    {"id": "cherry", "label": "送樱桃", "category": "feed", "icon": "cherry",
     "prompt": "你送上一颗红润的樱桃给桌宠。",
     "effects": {"satiety": 15, "mood": 10}},
    {"id": "milk", "label": "送牛奶", "category": "feed", "icon": "milk",
     "prompt": "你递来一杯温热的牛奶给桌宠。",
     "effects": {"satiety": 18, "mood": 6}},
    # ── 抚摸 ──
    {"id": "head", "label": "摸头", "category": "pet", "icon": "hand",
     "prompt": "你轻轻摸了摸桌宠的头。",
     "effects": {"mood": 15}},
    {"id": "highfive", "label": "击掌", "category": "pet", "icon": "handshake",
     "prompt": "你伸出手和桌宠击掌。",
     "effects": {"mood": 12, "energy": 3}},
    {"id": "tickle", "label": "挠痒痒", "category": "pet", "icon": "feather",
     "prompt": "你挠了挠桌宠的痒痒。",
     "effects": {"mood": 18}},
    {"id": "hug", "label": "抱抱", "category": "pet", "icon": "heart",
     "prompt": "你张开双臂抱住了桌宠。",
     "effects": {"mood": 20}},
    # ── 玩耍 ──
    {"id": "ball", "label": "玩球球", "category": "play", "icon": "dribbble",
     "prompt": "你扔来一个球球和桌宠玩接球。",
     "effects": {"mood": 15, "energy": -15}},
    {"id": "hide", "label": "捉迷藏", "category": "play", "icon": "eye-off",
     "prompt": "你和桌宠玩起了捉迷藏。",
     "effects": {"mood": 15, "energy": -12}},
    {"id": "lift", "label": "举高高", "category": "play", "icon": "rocket",
     "prompt": "你把桌宠举高高转了一圈。",
     "effects": {"mood": 12, "energy": -8}},
    # ── 照顾 ──
    {"id": "brush", "label": "梳毛毛", "category": "care", "icon": "brush",
     "prompt": "你帮桌宠梳顺了毛毛。",
     "effects": {"cleanliness": 25, "mood": 6}},
    {"id": "bath", "label": "洗澡澡", "category": "care", "icon": "shower-head",
     "prompt": "你给桌宠洗了个香喷喷的澡。",
     "effects": {"cleanliness": 35, "energy": -5}},
    {"id": "sleep", "label": "哄睡觉", "category": "care", "icon": "moon",
     "prompt": "你温柔地哄桌宠睡觉。",
     "effects": {"energy": 30, "mood": 5}},
    {"id": "blanket", "label": "盖被子", "category": "care", "icon": "bed-double",
     "prompt": "你给桌宠盖上了暖和的被子。",
     "effects": {"energy": 15, "mood": 10}},
    # ── 心情 ──
    {"id": "praise", "label": "夸夸", "category": "mood", "icon": "star",
     "prompt": "你夸桌宠今天特别棒。",
     "effects": {"mood": 12}},
    {"id": "comfort", "label": "安慰", "category": "mood", "icon": "heart-handshake",
     "prompt": "你温柔地安慰了桌宠。",
     "effects": {"mood": 15}},
    {"id": "joke", "label": "讲笑话", "category": "mood", "icon": "laugh",
     "prompt": "你给桌宠讲了个笑话。",
     "effects": {"mood": 15}},
]

_BY_ID: Dict[str, Dict] = {a["id"]: a for a in ACTIONS}
_BY_CATEGORY: Dict[str, List[Dict]] = {}
for a in ACTIONS:
    _BY_CATEGORY.setdefault(a["category"], []).append(a)


def get_action(action_id: str) -> Optional[Dict]:
    return _BY_ID.get(action_id)


def actions_by_category() -> Dict[str, List[Dict]]:
    return _BY_CATEGORY


def public_actions() -> List[Dict]:
    """前端圆环菜单 + Chat 图标检测所需（不含 effects 内部细节）"""
    return [
        {"id": a["id"], "label": a["label"], "category": a["category"],
         "icon": a["icon"], "prompt": a["prompt"]}
        for a in ACTIONS
    ]
