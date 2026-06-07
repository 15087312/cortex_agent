"""
提示词注册表 - 集中管理所有提示词模板
支持文件加载和运行时更新
"""
import logging
from typing import Dict, Optional, Callable
import os
import hashlib

logger = logging.getLogger(__name__)


class PromptRegistry:
    """提示词注册表 - 单例模式"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._templates: Dict[str, str] = {}
            self._template_hashes: Dict[str, str] = {}
            self._file_paths: Dict[str, str] = {}
            self._dynamic_generators: Dict[str, Callable] = {}
            self._initialized = True

    def register(self, key: str, template: str, file_path: Optional[str] = None):
        """
        注册提示词模板

        Args:
            key: 模板标识符
            template: 模板内容
            file_path: 可选的源文件路径
        """
        self._templates[key] = template
        self._template_hashes[key] = self._hash(template)
        if file_path:
            self._file_paths[key] = file_path

    def register_generator(self, key: str, generator: Callable[[], str]):
        """
        注册动态生成器

        Args:
            key: 模板标识符
            generator: 生成函数 () -> str
        """
        self._dynamic_generators[key] = generator

    def get(self, key: str, use_dynamic: bool = True) -> Optional[str]:
        """
        获取提示词模板

        Args:
            key: 模板标识符
            use_dynamic: 是否使用动态生成器

        Returns:
            模板内容或 None
        """
        if use_dynamic and key in self._dynamic_generators:
            return self._dynamic_generators[key]()

        return self._templates.get(key)

    def load_from_file(self, key: str, file_path: str, encoding: str = "utf-8"):
        """
        从文件加载提示词

        Args:
            key: 模板标识符
            file_path: 文件路径
            encoding: 文件编码
        """
        if not os.path.exists(file_path):
            return False

        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read().strip()
            self.register(key, content, file_path)
            return True
        except Exception as e:
            logger.warning(f"加载提示词文件失败 ({file_path}): {e}")
            return False

    def load_from_directory(self, directory: str, suffix: str = ".txt", encoding: str = "utf-8"):
        """
        从目录加载所有提示词文件

        Args:
            directory: 目录路径
            suffix: 文件后缀
            encoding: 文件编码
        """
        if not os.path.exists(directory):
            return 0

        loaded = 0
        for filename in os.listdir(directory):
            if filename.endswith(suffix):
                key = filename[:-len(suffix)]
                file_path = os.path.join(directory, filename)
                if self.load_from_file(key, file_path, encoding):
                    loaded += 1
        return loaded

    def update(self, key: str, content: str) -> bool:
        """更新模板内容"""
        if key not in self._templates:
            return False
        self._templates[key] = content
        self._template_hashes[key] = self._hash(content)
        return True

    def has_changed(self, key: str) -> bool:
        """检查模板是否已变更"""
        if key not in self._templates or key not in self._template_hashes:
            return False
        return self._template_hashes[key] != self._hash(self._templates[key])

    def list_keys(self) -> list:
        """列出所有已注册的模板键"""
        return list(self._templates.keys())

    def reload(self, key: str) -> bool:
        """重新加载指定模板"""
        if key not in self._file_paths:
            return False
        return self.load_from_file(key, self._file_paths[key])

    def reload_all(self):
        """重新加载所有从文件加载的模板"""
        for key, file_path in self._file_paths.items():
            self.load_from_file(key, file_path)

    def _hash(self, content: str) -> str:
        """计算内容哈希"""
        return hashlib.md5(content.encode()).hexdigest()

    def clear(self):
        """清除所有注册"""
        self._templates.clear()
        self._template_hashes.clear()
        self._file_paths.clear()


prompt_registry = PromptRegistry()
