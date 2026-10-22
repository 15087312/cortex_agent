"""共享事件类型 — 跨业务模块的事件契约。

事件类型是模块间通过 Event Bus 协作的公共协议：
  - perception 产出感知事件（screen/file/speech/...）
  - desktop_pet 等消费者按类型订阅

定义在此（而非 perception 内部）是为了让消费者订阅事件时
不依赖生产者模块，保持模块单向依赖。
"""
from __future__ import annotations


class PerceptionEventType:
    """事件类型常量（跨模块共享契约）"""
    # 感知层产出
    SCREEN_DIFF = "screen.diff"             # 帧差检测结果
    SCREEN_OCR = "screen.ocr"               # OCR 文本变化
    SCREEN_UI = "screen.ui"                 # UI 元素变化
    SCREEN_WINDOW = "screen.window"         # 窗口状态变化
    FILE_CHANGE = "file.change"             # 文件变化
    SPEECH_DETECTED = "speech.detected"     # 语音识别结果
    MCP_RESOURCE_UPDATE = "mcp.resource.update"  # MCP 资源更新

    # 检测层产出
    DIFFERENCE_DETECTED = "difference.detected"  # 检测到差异


    # 通配
    ALL = "*"                                # 订阅所有事件
