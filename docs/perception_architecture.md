# 被动感知系统架构设计

## 现状

当前感知系统是分散的，各模块独立运行：

| 模块 | 功能 | 输出 |
|------|------|------|
| WindowDetector | 检测窗口切换 | SCREEN_WINDOW 事件 |
| ScreenDiff | 像素级帧差 | SCREEN_DIFF 事件 |
| VoiceDetector | 麦克风监听 | SPEECH_DETECTED 事件 |
| WorldStateManager | 维护当前状态 | 状态快照 |
| ProactiveTrigger | 屏幕变化+空闲触发搭话 | WebSocket 推送 |

**缺失的部分：**
- UI 元素检测是工具调用，不是被动感知
- 没有统一的屏幕理解层
- Chromium 应用支持不完整

## 新架构

### 核心思路

```
用户操作 → 多源感知 → 事件总线 → 统一状态 → 按需查询
```

### 模块划分

```
perception/
├── detectors/           # 检测器（产生原始事件）
│   ├── window_detector.py      # 窗口切换
│   ├── screen_diff_detector.py # 帧差检测
│   ├── voice_detector.py       # 语音检测
│   └── ui_detector.py          # [新增] UI 元素检测
│
├──理解层/              # [新增] 统一屏幕理解
│   ├── detector_router.py      # 自动选择检测器
│   ├── touchpoint_backend.py   # 无障碍 API 后端
│   ├── cdp_backend.py          # CDP 后端
│   └── vision_backend.py       # 视觉模型后端
│
├── state/              # 状态管理
│   ├── world_state.py          # 世界状态
│   └── screen_state.py         # [新增] 屏幕状态（含 UI 元素）
│
├── events/             # 事件系统
│   ├── bus.py                  # 事件总线
│   └── types.py                # 事件类型
│
└── trigger/            # 触发器
    └── proactive_trigger.py    # 主动搭话
```

### 检测器路由器（核心）

```python
class DetectorRouter:
    """自动选择最佳检测器"""

    def detect(self, app: str = "") -> ScreenContext:
        # 1. 检测应用类型
        app_type = self._classify_app(app)  # native / chromium / unknown

        # 2. 根据类型选择后端
        if app_type == "native":
            result = self.touchpoint_backend.detect(app, depth=3)
            if result.element_count < 5:
                # 无障碍 API 信息太少，用视觉补充
                result = self._merge_with_vision(result)
        elif app_type == "chromium":
            if self.cdp_backend.is_available():
                result = self.cdp_backend.detect(app)
            else:
                result = self.vision_backend.detect(app)
        else:
            result = self.vision_backend.detect(app)

        return result
```

### 自动降级策略

```
touchpoint 元素 >= 5  → 使用 touchpoint 结果
touchpoint 元素 < 5   → 截图 + 视觉模型补充
CDP 可用              → 优先使用 CDP（Chromium 应用）
CDP 不可用            → 降级到视觉模型
```

### 数据流

```
┌─────────────┐
│ 用户操作    │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│         DetectorRouter.detect()         │
│                                         │
│  ┌───────────┐ ┌───────────┐ ┌────────┐│
│  │touchpoint │ │   CDP     │ │ vision ││
│  │  (native) │ │(chromium) │ │ (fallback)│
│  └─────┬─────┘ └─────┬─────┘ └────┬───┘│
│        └──────────────┼────────────┘    │
└───────────────────────┼─────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  ScreenContext  │
              │  - elements[]   │
              │  - app_name     │
              │  - window_title │
              │  - backend_used │
              └────────┬────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │WorldState│ │Perception│ │Proactive │
   │  更新    │ │  Pool    │ │ Trigger  │
   └──────────┘ └──────────┘ └──────────┘
```

### 实现优先级

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | DetectorRouter | 自动检测+路由 |
| P0 | touchpoint_backend | 封装 touchpoint 调用 |
| P1 | vision_backend | 截图+视觉模型分析 |
| P1 | cdp_backend | CDP 扫描（需浏览器支持） |
| P2 | ScreenContext 数据结构 | 统一输出格式 |
| P2 | 集成到 WorldState | 状态更新 |

### 与现有系统的关系

- **WindowDetector** → 保留，提供窗口切换事件
- **ScreenDiff** → 保留，提供像素变化事件
- **UI 元素检测** → 从工具调用改为被动感知（按需触发）
- **主动搭话** → 使用新的 ScreenContext 而非原始事件

### 配置项

```bash
# .env
PERCEPTION_UI_ENABLED=true           # UI 检测开关
PERCEPTION_UI_BACKEND=auto           # auto / touchpoint / cdp / vision
PERCEPTION_UI_DEPTH=3                # 检测深度
PERCEPTION_UI_FALLBACK_VISION=true   # 信息不足时是否降级到视觉模型
```
