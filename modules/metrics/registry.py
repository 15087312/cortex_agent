"""
指标注册表 - 管理所有指标定义
"""
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger("metrics_registry")


@dataclass
class MetricDefinition:
    """指标定义"""
    name: str
    type: str  # counter, gauge, histogram, summary
    description: str
    unit: str = ""
    module: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class MetricRegistry:
    """指标注册表"""

    def __init__(self):
        self._metrics: Dict[str, MetricDefinition] = {}
        self._register_default_metrics()

    def _register_default_metrics(self):
        """注册默认指标"""
        default_metrics = [
            # 系统指标
            ("system.cpu.percent", "gauge", "CPU 使用率", "%", "system"),
            ("system.memory.percent", "gauge", "内存使用率", "%", "system"),
            ("system.disk.percent", "gauge", "磁盘使用率", "%", "system"),
            ("system.uptime", "gauge", "系统运行时间", "seconds", "system"),

            # 记忆模块指标
            ("memory.short_term.size", "gauge", "短期记忆大小", "items", "memory"),
            ("memory.long_term.size", "gauge", "长期记忆大小", "items", "memory"),
            ("memory.save.latency", "histogram", "记忆保存延迟", "ms", "memory"),
            ("memory.load.latency", "histogram", "记忆加载延迟", "ms", "memory"),
            ("memory.search.latency", "histogram", "记忆搜索延迟", "ms", "memory"),
            ("memory.rag.vectors", "gauge", "RAG 向量数量", "items", "memory"),
            ("memory.ops.total", "counter", "记忆操作总数", "ops", "memory"),

            # 思维模块指标
            ("thinking.think.latency", "histogram", "思考延迟", "ms", "thinking"),
            ("thinking.think.rounds", "histogram", "思考轮次", "rounds", "thinking"),
            ("thinking.experts.active", "gauge", "活跃专家数", "experts", "thinking"),
            ("thinking.ops.total", "counter", "思考操作总数", "ops", "thinking"),

            # 感知模块指标
            ("perception.events.total", "counter", "感知事件总数", "events", "perception"),
            ("perception.events.rate", "gauge", "感知事件速率", "events/s", "perception"),
            ("perception.latency", "histogram", "感知延迟", "ms", "perception"),

            # 资源模块指标
            ("resource.probes.active", "gauge", "活跃探针数", "probes", "resource"),
            ("resource.models.loaded", "gauge", "已加载模型数", "models", "resource"),
            ("resource.gpu.utilization", "gauge", "GPU 利用率", "%", "resource"),

            # 注意力模块指标
            ("attention.modules.active", "gauge", "激活模块数", "modules", "attention"),
            ("attention.decision.latency", "histogram", "注意力决策延迟", "ms", "attention"),

            # 输出模块指标
            ("output.latency", "histogram", "输出延迟", "ms", "output"),
            ("output.tokens", "histogram", "输出 token 数", "tokens", "output"),

            # 错误指标
            ("errors.total", "counter", "错误总数", "errors", "system"),
            ("errors.by_type", "counter", "按类型错误数", "errors", "system"),
        ]

        for name, mtype, desc, unit, module in default_metrics:
            self.register(name, mtype, desc, unit, module)

    def register(
        self,
        name: str,
        mtype: str,
        description: str,
        unit: str = "",
        module: str = "",
        tags: List[str] = None
    ) -> MetricDefinition:
        """注册指标"""
        if name in self._metrics:
            logger.debug(f"指标 {name} 已存在，跳过注册")
            return self._metrics[name]

        metric = MetricDefinition(
            name=name,
            type=mtype,
            description=description,
            unit=unit,
            module=module,
            tags=tags or []
        )
        self._metrics[name] = metric
        logger.debug(f"注册指标: {name}")
        return metric

    def get(self, name: str) -> Optional[MetricDefinition]:
        """获取指标定义"""
        return self._metrics.get(name)

    def get_all(self) -> List[MetricDefinition]:
        """获取所有指标"""
        return list(self._metrics.values())

    def get_by_module(self, module: str) -> List[MetricDefinition]:
        """按模块获取指标"""
        return [m for m in self._metrics.values() if m.module == module]

    def exists(self, name: str) -> bool:
        """检查指标是否存在"""
        return name in self._metrics


metric_registry = MetricRegistry()