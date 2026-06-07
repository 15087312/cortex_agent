"""
探针缓存管理器

管理活跃探针的生命周期
- 探针按需加载
- 30分钟无调用自动关闭
- 模板驱动
"""
import json
import os
import re
import time
import threading
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime

from utils.logger import setup_logger


@dataclass
class ActiveProbe:
    """活跃探针实例"""
    name: str
    target_model: str
    template_name: str
    last_used: float  # 时间戳
    created_at: float  # 时间戳
    trigger_count: int  # 触发次数
    description: str


class ProbeCache:
    """
    探针缓存管理器
    
    职责：
    1. 管理活跃探针实例
    2. 按模板动态生成探针
    3. 30分钟无调用自动关闭
    4. 持久化到磁盘
    """
    
    CACHE_DIR = "data/probe_cache"
    TTL_SECONDS = 1800  # 30分钟
    
    def __init__(self):
        self.logger = setup_logger("probe_cache")
        self._probes: Dict[str, ActiveProbe] = {}
        self._templates: Dict[str, Any] = {}
        self._load_templates()
        self._load_from_disk()
        
        # 启动清理线程
        self._start_cleanup_thread()
    
    def _load_templates(self):
        """加载探针模板"""
        from modules.thinking.probes.templates import get_all_templates
        
        templates = get_all_templates()
        
        # 按类型组织模板
        for t in templates.get("manager", []):
            self._templates[f"manager_{t.name}"] = t
        
        for t in templates.get("expert", []):
            self._templates[f"expert_{t.name}"] = t
        
        self.logger.info(f"加载了 {len(self._templates)} 个探针模板")
    
    def _load_from_disk(self):
        """从磁盘加载活跃探针"""
        cache_file = os.path.join(self.CACHE_DIR, "active_probes.json")
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for probe_data in data.get("probes", []):
                    probe = ActiveProbe(**probe_data)
                    # 检查是否过期
                    if self._is_expired(probe):
                        continue
                    self._probes[probe.name] = probe
                
                self.logger.info(f"从磁盘加载了 {len(self._probes)} 个活跃探针")
            except Exception as e:
                self.logger.warning(f"加载探针缓存失败: {e}")
    
    def _save_to_disk(self):
        """保存探针到磁盘"""
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(self.CACHE_DIR, "active_probes.json")
        
        try:
            data = {
                "probes": [asdict(p) for p in self._probes.values()],
                "updated_at": datetime.now().isoformat()
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"保存探针缓存失败: {e}")
    
    def _is_expired(self, probe: ActiveProbe) -> bool:
        """检查探针是否过期"""
        return (time.time() - probe.last_used) > self.TTL_SECONDS
    
    def _start_cleanup_thread(self):
        """启动清理线程"""
        import threading
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        self.logger.info("探针清理线程已启动")
    
    def _cleanup_loop(self):
        """定期清理过期探针"""
        while True:
            time.sleep(60)  # 每分钟检查一次
            self._cleanup_expired()
    
    def _cleanup_expired(self):
        """清理过期探针"""
        expired = []
        for name, probe in self._probes.items():
            if self._is_expired(probe):
                expired.append(name)
        
        if expired:
            for name in expired:
                del self._probes[name]
            self.logger.info(f"清理了 {len(expired)} 个过期探针: {expired}")
            self._save_to_disk()
    
    def get_or_create(self, template_key: str, target_model: str, description: str) -> ActiveProbe:
        """
        获取或创建探针
        
        Args:
            template_key: 模板键 (如 "manager_deep_analysis")
            target_model: 目标模型
            description: 探针描述
        """
        # 检查是否已存在
        if template_key in self._probes:
            probe = self._probes[template_key]
            probe.last_used = time.time()
            return probe
        
        # 创建新探针
        probe = ActiveProbe(
            name=template_key,
            target_model=target_model,
            template_name=template_key,
            last_used=time.time(),
            created_at=time.time(),
            trigger_count=0,
            description=description
        )
        
        self._probes[template_key] = probe
        self._save_to_disk()
        self.logger.info(f"创建新探针: {template_key} -> {target_model}")
        
        return probe
    
    def trigger(self, template_key: str) -> Optional[ActiveProbe]:
        """
        触发探针
        
        Args:
            template_key: 探针模板键
        """
        if template_key in self._probes:
            probe = self._probes[template_key]
            probe.last_used = time.time()
            probe.trigger_count += 1
            self._save_to_disk()
            return probe
        
        # 探针不存在，可能是首次触发
        return None
    
    def match_input(self, text: str, probe_type: str = "all") -> List[ActiveProbe]:
        """
        匹配输入文本，返回匹配的探针
        
        Args:
            text: 输入文本
            probe_type: "manager", "expert", 或 "all"
        """
        matched = []
        text_lower = text.lower()
        
        templates_to_check = []
        if probe_type in ("manager", "all"):
            templates_to_check.extend([
                t for k, t in self._templates.items() if k.startswith("manager_")
            ])
        if probe_type in ("expert", "all"):
            templates_to_check.extend([
                t for k, t in self._templates.items() if k.startswith("expert_")
            ])
        
        for template in templates_to_check:
            # 检查关键词
            matched_keywords = []
            for kw in template.trigger_conditions:
                if kw.lower() in text_lower:
                    matched_keywords.append(kw)
            
            # 检查正则
            matched_patterns = []
            for pattern in template.trigger_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    matched_patterns.append(pattern)
            
            # 计算置信度
            if matched_keywords or matched_patterns:
                confidence = min(1.0, (len(matched_keywords) * 0.3 + len(matched_patterns) * 0.4))
                
                if confidence >= template.min_confidence:
                    # 获取或创建活跃探针
                    probe_key = template.name
                    target_model = template.target_model
                    
                    # 确定前缀
                    prefix = "manager" if target_model == "medium_model" else "expert"
                    probe_key = f"{prefix}_{template.name}"
                    
                    probe = self.get_or_create(
                        template_key=probe_key,
                        target_model=target_model,
                        description=template.description
                    )
                    matched.append(probe)
        
        return matched
    
    def list_active(self) -> List[Dict[str, Any]]:
        """列出所有活跃探针"""
        result = []
        for probe in self._probes.values():
            age = time.time() - probe.created_at
            idle = time.time() - probe.last_used
            result.append({
                "name": probe.name,
                "target": probe.target_model,
                "description": probe.description,
                "triggers": probe.trigger_count,
                "age_seconds": int(age),
                "idle_seconds": int(idle),
                "expired": self._is_expired(probe)
            })
        return result
    
    def clear_all(self):
        """清空所有探针"""
        self._probes.clear()
        self._save_to_disk()
        self.logger.info("已清空所有探针")


# 全局实例与锁
_probe_cache: Optional[ProbeCache] = None
_probe_cache_lock = threading.Lock()


def get_probe_cache() -> ProbeCache:
    """获取探针缓存实例（线程安全的延迟初始化）"""
    global _probe_cache
    if _probe_cache is None:
        with _probe_cache_lock:
            if _probe_cache is None:  # 双重检查
                _probe_cache = ProbeCache()
    return _probe_cache