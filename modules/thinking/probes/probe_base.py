"""
探针基类 - 三层金字塔架构

所有探针的基类，支持三层探测：
- 第零层：JSON专家调用（0延迟，模型显式请求）
- 第一层：轻量代码探针（0延迟，90%的探测）
- 第二层：Tiny模型探针（10ms级，9%的探测）
- 第三层：标准模型探针（100ms级，1%的探测）

特性：
- 异步非阻塞：主流程永远不等探针
- 结果缓存：相同的探测只做一次
- 按需触发：只有满足阈值条件时才触发
- 优先级调度：高风险探测优先
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
import time

from utils.logger import setup_logger


class ProbePriority(Enum):
    """探针优先级"""
    CRITICAL = 5   # 关键（价值观、安全）
    HIGH = 4       # 高（事实核查）
    MEDIUM = 3     # 中（情绪）
    LOW = 2        # 低（记忆）
    MINIMAL = 1    # 最低（工具）


class ProbeLayer(Enum):
    """探针层级"""
    LIGHT = 1      # 轻量代码探针（0延迟）
    TINY = 2       # Tiny模型探针（10ms级）
    STANDARD = 3   # 标准模型探针（100ms级）


@dataclass
class ProbeSignal:
    """探针信号"""
    signal_type: str           # 信号类型
    confidence: float          # 置信度 0-1
    source: str               # 来源（哪个模型的输出）
    target: str               # 目标（哪个模型需要被调用）
    content: str              # 触发内容
    context: str              # 上下文
    priority: ProbePriority    # 优先级
    layer: ProbeLayer = ProbeLayer.LIGHT  # 探测层级
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_high_confidence(self, threshold: float = 0.7) -> bool:
        """判断是否高置信度"""
        return self.confidence >= threshold


@dataclass
class ProbeResult:
    """探针检测结果"""
    triggered: bool
    confidence: float
    reason: str
    signals: List[ProbeSignal] = field(default_factory=list)
    needs_next_layer: bool = False  # 是否需要下一层探测
    latency_ms: float = 0  # 探测耗时（毫秒）


class Probe(ABC):
    """
    探针基类 - 三层金字塔架构
    
    特性：
    - 分层探测：轻量代码 → Tiny模型 → 标准模型
    - 异步非阻塞：主流程永远不等探针
    - 结果缓存：相同的探测只做一次
    - 按需触发：只有满足阈值条件时才触发
    """
    
    # 第一层：轻量代码检测的置信度阈值
    LIGHT_CONFIDENCE_THRESHOLD = 0.8  # 超过此值直接返回，不需要模型
    
    # 第二层：Tiny模型检测的置信度阈值
    TINY_CONFIDENCE_THRESHOLD = 0.7  # 超过此值直接返回，不需要标准模型
    
    def __init__(
        self,
        name: str,
        model_manager=None,
        priority: ProbePriority = ProbePriority.MEDIUM
    ):
        self.name = name
        self.model_manager = model_manager
        self.priority = priority
        
        self.logger = setup_logger(f"probe.{name}")
        self._enabled = True
        self._cache: Dict[str, Tuple[ProbeResult, float]] = {}  # 结果缓存
        self._cache_ttl = 300  # 缓存有效期5分钟
        self._stats = {
            "total_calls": 0,
            "light_hits": 0,
            "tiny_hits": 0,
            "standard_hits": 0,
            "cache_hits": 0,
            "total_latency_ms": 0,
        }
    
    @property
    @abstractmethod
    def prompt(self) -> str:
        """探针提示词（子类实现）"""
        pass
    
    @property
    def signal_type(self) -> str:
        """信号类型"""
        return self.name
    
    @property
    def target_unit_type(self) -> str:
        """目标单元类型"""
        return "expert"
    
    def detect(self, outputs: List[Any]) -> List[ProbeSignal]:
        """
        检测是否需要触发（三层金字塔架构）
        
        Args:
            outputs: 所有模型的输出列表
            
        Returns:
            触发信号列表
        """
        if not self._enabled:
            return []
        
        if not outputs:
            return []
        
        start_time = time.time()
        self._stats["total_calls"] += 1
        
        # 构建检测上下文
        context = self._build_context(outputs)
        
        # 检查缓存
        cache_key = self._get_cache_key(context)
        cached = self._get_from_cache(cache_key)
        if cached:
            self._stats["cache_hits"] += 1
            return cached.signals
        
        # 第零层：JSON专家调用检测（模型显式请求，最高优先级）
        json_signal = self._detect_json_expert_call(outputs)
        if json_signal:
            self._stats["light_hits"] += 1  # 计入轻量层统计
            latency_ms = (time.time() - start_time) * 1000
            result = ProbeResult(
                triggered=True,
                confidence=1.0,
                reason="JSON专家调用",
                signals=[json_signal]
            )
            result.latency_ms = latency_ms
            self._stats["total_latency_ms"] += latency_ms
            self._add_to_cache(cache_key, result)
            return [json_signal]
        
        # 第一层：轻量代码检测（0延迟）
        # 仅扫描用户输入，不扫描模型输出（避免引导文本误触发）
        user_input = self._extract_user_input(outputs)
        if user_input:
            light_result = self._light_detect(user_input)
        else:
            # 没有用户输入，直接返回空结果
            light_result = ProbeResult(triggered=False, confidence=0.0, reason="无用户输入")
        
        if light_result.confidence >= self.LIGHT_CONFIDENCE_THRESHOLD:
            # 高置信度，直接返回
            self._stats["light_hits"] += 1
            latency_ms = (time.time() - start_time) * 1000
            light_result.latency_ms = latency_ms
            self._stats["total_latency_ms"] += latency_ms
            self._add_to_cache(cache_key, light_result)
            return light_result.signals
        
        # 第二层：Tiny模型检测（如果可用）
        if light_result.needs_next_layer and self.model_manager:
            tiny_result = self._tiny_detect(context, outputs)
            if tiny_result.confidence >= self.TINY_CONFIDENCE_THRESHOLD:
                # 高置信度，直接返回
                self._stats["tiny_hits"] += 1
                latency_ms = (time.time() - start_time) * 1000
                tiny_result.latency_ms = latency_ms
                self._stats["total_latency_ms"] += latency_ms
                self._add_to_cache(cache_key, tiny_result)
                return tiny_result.signals
            
            # 第三层：标准模型检测（仅在高优先级且第二层不确定时）
            if tiny_result.needs_next_layer and self.priority in (ProbePriority.CRITICAL, ProbePriority.HIGH):
                standard_result = self._standard_detect(context, outputs)
                self._stats["standard_hits"] += 1
                latency_ms = (time.time() - start_time) * 1000
                standard_result.latency_ms = latency_ms
                self._stats["total_latency_ms"] += latency_ms
                self._add_to_cache(cache_key, standard_result)
                return standard_result.signals
            
            # 使用第二层结果
            latency_ms = (time.time() - start_time) * 1000
            tiny_result.latency_ms = latency_ms
            self._stats["total_latency_ms"] += latency_ms
            self._add_to_cache(cache_key, tiny_result)
            return tiny_result.signals
        
        # 返回第一层结果
        latency_ms = (time.time() - start_time) * 1000
        light_result.latency_ms = latency_ms
        self._stats["total_latency_ms"] += latency_ms
        self._add_to_cache(cache_key, light_result)
        return light_result.signals
    
    def _detect_json_expert_call(self, outputs: List[Any]) -> Optional[ProbeSignal]:
        """
        第零层：检测JSON格式专家调用请求
        
        支持的JSON格式：
        1. 简单格式：{"expert_call": "emotion", "reason": "..."}
        2. 完整格式：{"expert_call": {"type": "emotion", "reason": "...", "confidence": 0.9}}
        3. 数组格式：{"expert_calls": [{"type": "emotion", "reason": "..."}]}
        
        Returns:
            ProbeSignal 如果检测到匹配本探针的专家调用，否则 None
        """
        for output in outputs:
            content = getattr(output, 'content', str(output))
            if not content:
                continue
            
            # 尝试提取JSON块
            json_matches = self._extract_json_blocks(content)
            
            for json_str in json_matches:
                try:
                    data = json.loads(json_str)
                    signal = self._parse_expert_call_json(data, content)
                    if signal:
                        self.logger.info(f"检测到JSON专家调用: {signal.signal_type}")
                        return signal
                except json.JSONDecodeError:
                    continue
        
        return None
    
    def _extract_json_blocks(self, text: str) -> List[str]:
        """从文本中提取JSON块"""
        json_blocks = []
        
        # 1. 查找代码块中的JSON
        code_block_pattern = r'```json\s*\n(.*?)\n```'
        for match in re.finditer(code_block_pattern, text, re.DOTALL):
            json_blocks.append(match.group(1).strip())
        
        # 2. 查找独立JSON对象（匹配 { ... } 结构）
        # 使用平衡括号匹配
        depth = 0
        start = None
        for i, char in enumerate(text):
            if char == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    json_blocks.append(text[start:i+1])
                    start = None
        
        # 3. 查找简单JSON格式（单行）
        simple_pattern = r'\{[^{}]*"expert_call"[^{}]*\}'
        for match in re.finditer(simple_pattern, text):
            json_blocks.append(match.group(0))
        
        return json_blocks
    
    def _parse_expert_call_json(self, data: Dict[str, Any], context: str) -> Optional[ProbeSignal]:
        """
        解析JSON专家调用数据
        
        支持格式：
        1. {"expert_call": "code", "reason": "..."}
        2. {"expert_call": {"type": "code", "reason": "...", "confidence": 0.9}}
        3. {"expert_call": {"type": "code_needed"}}
        4. {"expert_calls": [{"type": "code", "reason": "..."}]}
        """
        # 格式1: expert_call 是字符串
        expert_call = data.get("expert_call")
        if isinstance(expert_call, str):
            if self._matches_expert_type(expert_call):
                return ProbeSignal(
                    signal_type=self.signal_type,
                    confidence=1.0,
                    source="json_explicit_call",
                    target=self._get_target_model(),
                    content=f"JSON专家调用: {data.get('reason', '无说明')}",
                    context=context[:500],
                    priority=self.priority,
                    layer=ProbeLayer.LIGHT,
                    metadata={"explicit_call": True, "reason": data.get("reason", "")}
                )
        
        # 格式2: expert_call 是对象
        if isinstance(expert_call, dict):
            call_type = expert_call.get("type", "")
            if self._matches_expert_type(call_type):
                confidence = expert_call.get("confidence", 1.0)
                return ProbeSignal(
                    signal_type=self.signal_type,
                    confidence=float(confidence),
                    source="json_explicit_call",
                    target=self._get_target_model(),
                    content=f"JSON专家调用: {expert_call.get('reason', '无说明')}",
                    context=context[:500],
                    priority=self.priority,
                    layer=ProbeLayer.LIGHT,
                    metadata={"explicit_call": True, "reason": expert_call.get("reason", "")}
                )
        
        # 格式3: expert_calls 是数组
        expert_calls = data.get("expert_calls", [])
        if isinstance(expert_calls, list):
            for call in expert_calls:
                if isinstance(call, str) and self._matches_expert_type(call):
                    return ProbeSignal(
                        signal_type=self.signal_type,
                        confidence=1.0,
                        source="json_explicit_call",
                        target=self._get_target_model(),
                        content=f"JSON专家调用: {call}",
                        context=context[:500],
                        priority=self.priority,
                        layer=ProbeLayer.LIGHT,
                        metadata={"explicit_call": True}
                    )
                if isinstance(call, dict) and self._matches_expert_type(call.get("type", "")):
                    return ProbeSignal(
                        signal_type=self.signal_type,
                        confidence=float(call.get("confidence", 1.0)),
                        source="json_explicit_call",
                        target=self._get_target_model(),
                        content=f"JSON专家调用: {call.get('reason', call.get('type', ''))}",
                        context=context[:500],
                        priority=self.priority,
                        layer=ProbeLayer.LIGHT,
                        metadata={"explicit_call": True}
                    )
        
        return None
    
    def _matches_expert_type(self, expert_type: str) -> bool:
        """检查专家类型是否匹配本探针"""
        expert_type_lower = expert_type.lower()
        signal_type_lower = self.signal_type.lower()
        name_lower = self.name.lower()
        
        # 精确匹配
        if expert_type_lower == signal_type_lower or expert_type_lower == name_lower:
            return True
        
        # 模糊匹配（移除下划线和 _needed 后缀）
        normalized_expert = expert_type_lower.replace("_needed", "").replace("_probe", "").replace("_", "")
        normalized_signal = signal_type_lower.replace("_needed", "").replace("_probe", "").replace("_", "")
        normalized_name = name_lower.replace("_probe", "").replace("_", "")
        
        if normalized_expert == normalized_signal or normalized_expert == normalized_name:
            return True
        
        return False
    
    def _extract_user_input(self, outputs: List[Any]) -> str:
        """从输出列表中提取用户输入（sender="user" 或 marker="input"）"""
        for output in outputs:
            sender = getattr(output, 'sender', '')
            marker = getattr(output, 'marker', '')
            if sender == 'user' or marker == 'input':
                return getattr(output, 'content', '')
        return ''
    
    def _light_detect(self, context: str) -> ProbeResult:
        """
        第一层：轻量代码检测（0延迟）
        
        所有能用代码逻辑判断的问题，绝对不要用模型。
        context 应仅包含用户输入，不包含模型输出。
        """
        try:
            result_text = self._rule_based_detect(context)
            return self._parse_result_to_probe_result(result_text, context)
        except Exception as e:
            self.logger.error(f"轻量检测失败: {e}")
            return ProbeResult(
                triggered=False,
                confidence=0.0,
                reason=f"轻量检测失败: {e}",
                needs_next_layer=True
            )
    
    def _tiny_detect(self, context: str, outputs: List[Any]) -> ProbeResult:
        """
        第二层：Tiny模型检测（10ms级）
        
        处理轻量代码探针无法判断的简单语义问题。
        使用 lite 模型，异步批量处理。
        context 应仅包含用户输入。
        """
        if not self.model_manager:
            return ProbeResult(
                triggered=False,
                confidence=0.0,
                reason="Tiny模型未配置",
                needs_next_layer=True
            )
        
        try:
            response = self.model_manager.call(
                prompt=context,
                model_size="lite",
                max_tokens=100,
                temperature=0
            )
            return self._parse_result_to_probe_result(response, context)
        except Exception as e:
            self.logger.error(f"Tiny模型检测失败: {e}")
            return ProbeResult(
                triggered=False,
                confidence=0.0,
                reason=f"Tiny模型检测失败: {e}",
                needs_next_layer=True
            )
    
    def _standard_detect(self, context: str, outputs: List[Any]) -> ProbeResult:
        """
        第三层：标准模型检测（100ms级）
        
        只处理前两层无法判断的复杂问题。
        使用 small 或 middle 模型。
        """
        if not self.model_manager:
            return ProbeResult(
                triggered=False,
                confidence=0.0,
                reason="标准模型未配置"
            )
        
        try:
            response = self.model_manager.call(
                prompt=context,
                model_size="small",
                max_tokens=200,
                temperature=0
            )
            return self._parse_result_to_probe_result(response, context)
        except Exception as e:
            self.logger.error(f"标准模型检测失败: {e}")
            return ProbeResult(
                triggered=False,
                confidence=0.0,
                reason=f"标准模型检测失败: {e}"
            )
    
    def _rule_based_detect(self, context: str) -> str:
        """基于规则的备用检测（子类应重写）"""
        return "触发：否"
    
    def _parse_result_to_probe_result(self, result: str, context: str) -> ProbeResult:
        """解析检测结果为 ProbeResult"""
        lines = result.split("\n")
        triggered = False
        confidence = 0.5
        reason = ""
        
        for line in lines:
            line_lower = line.lower().strip()
            
            if "触发：是" in line or "trigger: yes" in line_lower:
                triggered = True
            elif "触发：否" in line or "trigger: no" in line_lower:
                triggered = False
            elif "置信度：" in line:
                try:
                    conf = line.split("置信度：")[1].strip()
                    confidence = float(conf)
                except (ValueError, IndexError):
                    pass
            elif "触发原因：" in line:
                reason = line.split("触发原因：")[1].strip()
        
        signals = []
        if triggered:
            signals.append(ProbeSignal(
                signal_type=self.signal_type,
                confidence=confidence,
                source="unknown",
                target=self._get_target_model(),
                content=reason or result[:100],
                context=result,
                priority=self.priority,
                layer=ProbeLayer.LIGHT
            ))
        
        return ProbeResult(
            triggered=triggered,
            confidence=confidence,
            reason=reason,
            signals=signals,
            needs_next_layer=0.3 <= confidence < 0.8  # 中等置信度需要下一层
        )
    
    def _build_context(self, outputs: List[Any]) -> str:
        """构建检测上下文"""
        parts = [
            self.prompt,
            "",
            "=" * 50,
            "## 模型输出记录",
            "(监听所有模型的思考和发言)",
            "",
        ]
        
        for out in outputs:
            sender = getattr(out, 'sender', 'unknown')
            marker = getattr(out, 'marker', 'unknown')
            content = getattr(out, 'content', str(out))
            
            parts.append(f"[{sender}]({marker}): {content[:200]}")
        
        parts.extend([
            "",
            "=" * 50,
            f"## {self.name} 探针分析",
            "请分析以上输出，判断是否需要触发" + self.target_unit_type,
            "返回格式：",
            "1. 是否触发：是/否",
            "2. 置信度：0-1",
            "3. 目标模型：模型名称",
            "4. 触发原因：...",
        ])
        
        return "\n".join(parts)
    
    def _get_target_model(self) -> str:
        """获取目标模型名称"""
        return f"{self.name}_expert"
    
    def _get_cache_key(self, context: str) -> str:
        """生成缓存键"""
        return hashlib.md5(f"{self.name}:{context[:500]}".encode()).hexdigest()
    
    def _get_from_cache(self, cache_key: str) -> Optional[ProbeResult]:
        """从缓存获取结果"""
        if cache_key in self._cache:
            result, timestamp = self._cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                return result
            else:
                del self._cache[cache_key]
        return None
    
    def _add_to_cache(self, cache_key: str, result: ProbeResult) -> None:
        """添加结果到缓存，并定期清理过期项"""
        self._cache[cache_key] = (result, time.time())

        # 定期清理过期缓存项（当缓存大小超过阈值时）
        if len(self._cache) > 1000:  # 缓存项数超过1000时清理
            expired_keys = []
            current_time = time.time()
            for key, (_, timestamp) in self._cache.items():
                if current_time - timestamp >= self._cache_ttl:
                    expired_keys.append(key)
            for key in expired_keys:
                del self._cache[key]

            # 如果清理后仍然过多，使用LRU清理（保留最新的500项）
            if len(self._cache) > 1000:
                items_by_time = sorted(
                    self._cache.items(),
                    key=lambda x: x[1][1],  # 按时间戳排序
                    reverse=True  # 最新的在前
                )
                self._cache = dict(items_by_time[:500])
    
    def enable(self) -> None:
        """启用探针"""
        self._enabled = True
        self.logger.info(f"[{self.name}] 探针已启用")
    
    def disable(self) -> None:
        """禁用探针"""
        self._enabled = False
        self.logger.info(f"[{self.name}] 探针已禁用")
    
    def is_enabled(self) -> bool:
        """是否启用"""
        return self._enabled
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        avg_latency = 0
        if self._stats["total_calls"] > 0:
            avg_latency = self._stats["total_latency_ms"] / self._stats["total_calls"]
        
        return {
            "name": self.name,
            "priority": self.priority.value,
            "enabled": self._enabled,
            "signal_type": self.signal_type,
            "target": self.target_unit_type,
            "stats": {
                "total_calls": self._stats["total_calls"],
                "light_hits": self._stats["light_hits"],
                "tiny_hits": self._stats["tiny_hits"],
                "standard_hits": self._stats["standard_hits"],
                "cache_hits": self._stats["cache_hits"],
                "avg_latency_ms": round(avg_latency, 2),
            }
        }
