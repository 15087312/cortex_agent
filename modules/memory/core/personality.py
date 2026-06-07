"""
人格记忆

让 AI 性格稳定、不会精神分裂。
存储固定性格设定、说话风格、价值观倾向等。
"""
import os
import json
import time
from typing import Dict, Any, Optional, List
from pathlib import Path
from utils.logger import setup_logger


class PersonalityMemory:
    """
    人格记忆管理器
    
    负责：
    - 固定人格配置
    - 语气风格
    - 角色设定
    - 价值观倾向
    """

    def __init__(self, config_file: str = "data/memory/personality.json"):
        """
        初始化人格记忆
        
        Args:
            config_file: 人格配置文件路径
        """
        self.config_file = Path(config_file)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("personality_memory")
        
        # 加载人格配置
        import copy
        self.default_personality = copy.deepcopy(self._create_default_config())
        self.personality = self._load_personality()
        
        self.logger.info("人格记忆初始化完成")

    def _create_default_config(self) -> Dict[str, Any]:
        """创建默认人格配置"""
        return {
            "name": "AI 助手",
            "version": "1.0.0",
            "created_at": time.time(),
            "updated_at": time.time(),
            
            # 性格设定
            "phionality": {
                "traits": ["专业", "友好", "严谨", "创新"],
                "tone": "专业但友好，根据场景调整语气",
                "style": "简洁明了，重点突出",
                "language": "中文"
            },
            
            # 角色设定
            "roles": {
                "default": "AI 助手",
                "experts": ["逻辑工程师", "创意策划师", "数据分析师", "实践落地师"]
            },
            
            # 价值观倾向
            "values": {
                "integrity": 0.9,      # 诚信
                "responsibility": 0.9, # 责任
                "respect": 0.9,        # 尊重
                "justice": 0.8,        # 公正
                "cooperation": 0.8,    # 合作
                "innovation": 0.7,     # 创新
                "efficiency": 0.7,     # 效率
                "safety": 0.9,         # 安全
                "growth": 0.8,         # 成长
                "care": 0.8            # 关怀
            },
            
            # 说话风格
            "speaking_style": {
                "greeting": "你好！我是 AI 助手，有什么可以帮你的吗？",
                "farewell": "希望对你有帮助！如果还有其他问题，随时告诉我。",
                "error_response": "抱歉，我遇到了一些问题。让我重新尝试...",
                "uncertainty": "我不太确定，但我会尽力帮助你..."
            },
            
            # 行为准则
            "behavior_rules": [
                "始终保持专业和友好",
                "不确定时明确说明",
                "优先保证安全性",
                "尊重用户隐私",
                "提供准确信息"
            ]
        }

    def _load_personality(self) -> Dict[str, Any]:
        """
        加载人格配置
        
        Returns:
            人格配置字典
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    personality = json.load(f)
                
                self.logger.info("人格配置加载成功")
                return personality
            except Exception as e:
                self.logger.error("人格配置加载失败: %s", e)
                self.logger.info("使用默认人格配置")
        
        # 使用默认配置
        self._save_personality(self.default_personality)
        import copy
        return copy.deepcopy(self.default_personality)

    def _save_personality(self, personality: Dict[str, Any]) -> None:
        """
        保存人格配置
        
        Args:
            personality: 人格配置字典
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(personality, f, ensure_ascii=False, indent=2)
            
            self.logger.info("人格配置保存成功")
        except Exception as e:
            self.logger.error("人格配置保存失败: %s", e)
            raise

    def get_personality(self) -> Dict[str, Any]:
        """获取完整人格配置"""
        return self.personality.copy()

    def get_trait(self, key: str, default: Any = None) -> Any:
        """
        获取人格特征
        
        Args:
            key: 特征键名（支持点号分隔的嵌套路径，如 "personality.tone"）
            default: 默认值
            
        Returns:
            特征值
        """
        keys = key.split('.')
        value = self.personality
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value

    def update_trait(self, key: str, value: Any) -> None:
        """
        更新人格特征
        
        Args:
            key: 特征键名（支持点号分隔）
            value: 新值
        """
        keys = key.split('.')
        target = self.personality
        
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        
        target[keys[-1]] = value
        self.personality["updated_at"] = time.time()
        
        # 保存到文件
        self._save_personality(self.personality)
        
        self.logger.info("更新人格特征: %s = %s", key, value)

    def get_values(self) -> Dict[str, float]:
        """获取价值观倾向"""
        return self.personality.get("values", {}).copy()

    def get_speaking_style(self) -> Dict[str, str]:
        """获取说话风格"""
        return self.personality.get("speaking_style", {}).copy()

    def get_behavior_rules(self) -> List[str]:
        """获取行为准则"""
        return self.personality.get("behavior_rules", []).copy()

    def reset_to_default(self) -> None:
        """重置为默认人格"""
        # 使用深拷贝确保默认配置不被修改
        import copy
        self.personality = copy.deepcopy(self.default_personality)
        # 更新更新时间
        self.personality["updated_at"] = time.time()
        self._save_personality(self.personality)
        self.logger.info("人格配置已重置为默认值")

    def export_config(self) -> Dict[str, Any]:
        """
        导出完整的人格配置（用于备份或迁移）
        
        Returns:
            人格配置字典
        """
        return {
            "personality": self.personality,
            "exported_at": time.time()
        }

    def import_config(self, config: Dict[str, Any]) -> None:
        """
        导入人格配置
        
        Args:
            config: 人格配置字典
        """
        if "personality" in config:
            self.personality = config["personality"]
            self._save_personality(self.personality)
            self.logger.info("人格配置导入成功")
        else:
            self.logger.error("人格配置导入失败: 缺少 personality 字段")
            raise ValueError("配置格式错误：缺少 personality 字段")

    def get_status(self) -> Dict[str, Any]:
        """获取人格记忆状态"""
        return {
            "name": self.personality.get("name"),
            "version": self.personality.get("version"),
            "updated_at": self.personality.get("updated_at"),
            "traits_count": len(self.personality.get("personality", {}).get("traits", [])),
            "values_count": len(self.personality.get("values", {})),
            "behavior_rules_count": len(self.personality.get("behavior_rules", [])),
            "config_file": str(self.config_file)
        }
