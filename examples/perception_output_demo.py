"""
感知输出格式演示

展示被动感知系统的结构化输出格式
"""
from modules.perception.integration import PerceptionIntegrator
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
import time


def demo_perception_output():
    """演示感知输出格式"""
    print("=" * 60)
    print("被动感知系统 - 结构化输出演示")
    print("=" * 60)
    
    integrator = PerceptionIntegrator()
    
    # 模拟窗口切换事件
    print("\n1. 窗口切换事件")
    print("-" * 40)
    event1 = PerceptionEvent(
        event_type=PerceptionEventType.SCREEN_WINDOW,
        source="window",
        payload={
            "app_name": "Google Chrome",
            "window_title": "Google 搜索",
            "prev_app": "Terminal",
            "prev_window": "zsh",
        },
    )
    integrator._on_perception_event(event1)
    
    # 模拟OCR文本变化事件
    print("\n2. OCR文本变化事件")
    print("-" * 40)
    event2 = PerceptionEvent(
        event_type=PerceptionEventType.SCREEN_OCR,
        source="ocr",
        payload={
            "text": "搜索结果：今天天气晴朗",
            "new_lines": ["搜索结果：今天天气晴朗", "温度：25°C"],
            "roi_name": "搜索结果区域",
        },
    )
    integrator._on_perception_event(event2)
    
    # 模拟文件变化事件
    print("\n3. 文件变化事件")
    print("-" * 40)
    event3 = PerceptionEvent(
        event_type=PerceptionEventType.FILE_CHANGE,
        source="file",
        payload={
            "change": "修改",
            "path": "config/settings.py",
        },
    )
    integrator._on_perception_event(event3)
    
    # 模拟对话变化事件
    print("\n4. 对话变化事件")
    print("-" * 40)
    event4 = PerceptionEvent(
        event_type=PerceptionEventType.DIALOG_CHANGE,
        source="dialog",
        payload={
            "change": "用户发送了消息：今天天气怎么样",
            "role": "user",
        },
    )
    integrator._on_perception_event(event4)
    
    # 输出结构化结果
    print("\n" + "=" * 60)
    print("结构化输出结果")
    print("=" * 60)
    
    prompt = integrator.get_attention_prompt()
    print(prompt)
    
    print("\n" + "=" * 60)
    print("上下文摘要（注入模型prompt）")
    print("=" * 60)
    
    summary = integrator.get_context_summary()
    print(summary)


if __name__ == "__main__":
    demo_perception_output()