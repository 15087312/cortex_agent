"""
感知集成器 — 将感知系统采集的数据注入模型上下文

设计意图：
  被动感知系统（屏幕/OCR/文件/对话变化）采集的数据需要进入模型 prompt
  才能被模型"看到"。这个模块就是做这个连接的。

  数据流：
  感知事件（screen.diff / file.change / ...）
    → PerceptionEventBus
      → PerceptionIntegrator 订阅并接收
        → _attention_items 累计（去重，最多 20 条）
          → get_context_summary() 返回 "【环境感知】..."
            → 编排器每轮对话调用 → 注入模型 prompt

  这个连接曾经是断的（_attention_items 始终为空），
  现在通过订阅事件总线实时填充。

ThinkTrigger（差异→思考触发器）：
  这是另一条路径——高强度差异（intensity ≥ 50）可以主动触发思考，
  但需要外部注入 trigger_port。当前未连接，预留接口。
"""
import threading
from typing import List, Dict, Any, Optional
from utils.logger import setup_logger

logger = setup_logger("perception_integration")


class PerceptionIntegrator:
    """
    感知集成器

    将感知系统无缝集成到AI对话流程。
    订阅感知事件总线，自动将环境变化（屏幕/OCR/文件等）注入模型上下文。
    """

    def __init__(self):
        self._auto_monitoring = True
        self._context_injection_enabled = True
        self._attention_items: List[Dict[str, Any]] = []
        self._max_attention = 20
        self._sub_id: str = ""
        logger.info("感知集成器初始化完成")

    def start(self) -> None:
        """启动感知监控并订阅事件"""
        if self._auto_monitoring:
            from modules.perception import get_perception_system
            ps = get_perception_system()
            if not ps._started:
                ps.setup()
                ps.start()
            # 订阅感知事件，自动填充注意力池
            self._subscribe_events()
            logger.info("感知监控已启动，已订阅感知事件")

    def _subscribe_events(self) -> None:
        """订阅感知事件总线"""
        try:
            from modules.perception.events.bus import get_event_bus
            from modules.perception.events.types import PerceptionEventType

            event_bus = get_event_bus()

            # 订阅所有感知事件类型（屏幕、OCR、文件、差异等）
            # 订阅所有相关事件类型；每个订阅独立 try/except 防止单一失败阻塞其他
            for event_type in [
                PerceptionEventType.SCREEN_DIFF,
                PerceptionEventType.SCREEN_WINDOW,
                PerceptionEventType.SCREEN_OCR,
                PerceptionEventType.FILE_CHANGE,
                PerceptionEventType.SPEECH_DETECTED,
                PerceptionEventType.DIFFERENCE_DETECTED,
            ]:
                try:
                    event_bus.subscribe(event_type, self._on_perception_event)
                except Exception:
                    pass
            logger.info("已订阅感知事件")
        except Exception as e:
            logger.debug(f"订阅感知事件失败 (非致命): {e}")

    def _on_perception_event(self, event) -> None:
        """感知事件回调 — 添加到注意力池"""
        try:
            payload = event.payload if hasattr(event, 'payload') else {}
            event_type = event.event_type if hasattr(event, 'event_type') else 'unknown'
            source = payload.get('source_type', payload.get('type', event_type.split('.')[0] if '.' in event_type else 'unknown'))
            
            # 根据事件类型提取具体信息
            description = self._extract_description(event_type, payload)
            intensity = payload.get('intensity', 0.5)

            if isinstance(description, str) and description:
                # 去重：仅对完全相同的描述去重（避免截断匹配误杀）
                for item in self._attention_items[-5:]:
                    existing = item.get("description", "")
                    if existing == description:
                        return

                self._attention_items.append({
                    "source": source,
                    "event_type": event_type,
                    "description": description[:300],
                    "intensity": intensity,
                    "payload": payload,
                })
                if len(self._attention_items) > self._max_attention:
                    self._attention_items = self._attention_items[-self._max_attention:]
        except Exception as e:
            logger.debug(f"处理感知事件异常 (非致命): {e}")

    def _extract_description(self, event_type: str, payload: Dict[str, Any]) -> str:
        """根据事件类型提取自然语言描述

        将结构化的 payload 转为人类可读的描述文本。
        """
        try:
            if event_type == "screen.diff":
                ratio = payload.get("change_ratio", 0)
                regions = payload.get("changed_regions", [])
                intensity = "大幅" if ratio > 0.3 else "中等" if ratio > 0.1 else "小幅"
                region_info = f"{len(regions)} 个区域" if regions else ""
                return f"屏幕{intensity}变化 ({ratio*100:.0f}% 面积{', ' + region_info if region_info else ''})"

            elif event_type == "screen.window":
                # 窗口变化：返回具体窗口信息
                app_name = payload.get("app_name", "")
                window_title = payload.get("window_title", "")
                prev_app = payload.get("prev_app", "")
                prev_window = payload.get("prev_window", "")
                
                if prev_app and prev_app != app_name:
                    return f"窗口切换: {prev_app} → {app_name} ({window_title})"
                elif prev_window and prev_window != window_title:
                    return f"窗口标题变化: {app_name} [{prev_window}] → [{window_title}]"
                else:
                    return f"当前窗口: {app_name} - {window_title}"
            
            elif event_type == "screen.ocr":
                # OCR变化：返回新增文本
                new_lines = payload.get("new_lines", [])
                text = payload.get("text", "")
                roi_name = payload.get("roi_name", "屏幕")
                
                if new_lines:
                    new_text = "\n".join(new_lines[:5])  # 最多5行
                    return f"屏幕新文本 [{roi_name}]: {new_text}"
                elif text:
                    return f"屏幕文本 [{roi_name}]: {text[:200]}"
            
            elif event_type == "file.change":
                # 文件变化：返回文件路径和操作
                change = payload.get("change", "")
                path = payload.get("path", "")
                if change and path:
                    return f"文件{change}: {path}"
                return f"文件变化: {change or path or '未知'}"
            
            elif event_type == "speech.detected":
                # 语音识别：返回识别文本
                text = payload.get("text", "")
                if text:
                    return f"语音识别: {text}"
            
            elif event_type == "difference.detected":
                # 差异检测：返回差异描述
                description = payload.get("description", "")
                intensity = payload.get("intensity", 0)
                if description:
                    return f"环境差异 (强度{intensity:.0f}/100): {description[:200]}"
            
            # 兜底：返回通用描述
            description = payload.get("description", payload.get("text", ""))
            if description:
                return f"[{event_type}] {description[:200]}"
            
            return ""
        except Exception as e:
            return ""

    def stop(self) -> None:
        """停止感知监控"""
        from modules.perception import get_perception_system
        ps = get_perception_system()
        ps.stop()
        if self._sub_id:
            try:
                from modules.perception.events.bus import get_event_bus
                get_event_bus().unsubscribe(self._sub_id)
            except Exception:
                pass
            self._sub_id = ""
        logger.info("感知监控已停止")

    def update_dialog(self, messages: List[Dict]) -> None:
        """更新对话记录（预留接口）"""
        pass

    def add_dialog_change(self, role: str, content: str) -> None:
        """添加对话变化（预留接口）"""
        pass

    def _add_to_attention(self, change, urgency: float = 0.5) -> None:
        """向注意力池添加项（预留接口）"""
        pass

    def get_attention_prompt(self) -> str:
        """获取注意力提示（结构化输出）"""
        if not self._attention_items:
            return ""
        
        items = self._attention_items[-5:]  # 最近5条
        
        # 按事件类型分组
        grouped = {
            "windows": [],   # 窗口变化
            "text": [],      # 文本/OCR变化
            "files": [],     # 文件变化
            "other": [],     # 其他
        }
        
        for item in items:
            event_type = item.get("event_type", "")
            description = item.get("description", "")
            
            if not description:
                continue
            
            if "window" in event_type:
                grouped["windows"].append(description)
            elif "ocr" in event_type:
                grouped["text"].append(description)
            elif "file" in event_type:
                grouped["files"].append(description)
            else:
                grouped["other"].append(description)
        
        # 构建结构化输出
        sections = []
        
        if grouped["windows"]:
            sections.append("【窗口状态】\n" + "\n".join(grouped["windows"]))
        
        if grouped["text"]:
            sections.append("【屏幕文本】\n" + "\n".join(grouped["text"]))
        
        if grouped["files"]:
            sections.append("【文件变化】\n" + "\n".join(grouped["files"]))
        
        if grouped["other"]:
            sections.append("【其他感知】\n" + "\n".join(grouped["other"]))
        
        if not sections:
            return ""
        
        return "【环境感知】\n" + "\n\n".join(sections)

    def build_system_prompt(self, base_prompt: str) -> str:
        """构建系统提示词（注入感知信息）"""
        if not self._context_injection_enabled:
            return base_prompt
        attention_prompt = self.get_attention_prompt()
        if attention_prompt:
            return f"{base_prompt}\n\n{attention_prompt}"
        return base_prompt

    def build_messages(self, messages: List[Dict], system_prompt: str = None) -> List[Dict]:
        """构建完整的消息列表（包含感知上下文）"""
        if system_prompt:
            system_prompt = self.build_system_prompt(system_prompt)
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        return full_messages

    def get_context_summary(self) -> str:
        """获取感知上下文摘要（由编排层调用，注入到模型 prompt）

        Returns:
            非空字符串表示有环境感知信息需要注入
        """
        attention_prompt = self.get_attention_prompt()
        if attention_prompt:
            logger.info(
                f"感知上下文注入 LLM prompt: {len(attention_prompt)} 字符, "
                f"{len(self._attention_items)} 条注意力项: "
                f"{[it['event_type'] for it in self._attention_items[-5:]]}"
            )
            return attention_prompt
        logger.debug("感知上下文为空（_attention_items 无事件）")
        return ""


_perception_integrator_instance = None
_perception_integrator_lock = threading.Lock()


def get_perception_integrator() -> PerceptionIntegrator:
    """获取感知集成器全局单例（线程安全，双重检查锁）"""
    global _perception_integrator_instance
    if _perception_integrator_instance is None:
        with _perception_integrator_lock:
            if _perception_integrator_instance is None:
                _perception_integrator_instance = PerceptionIntegrator()
    return _perception_integrator_instance


# 向后兼容
perception_integrator = None
