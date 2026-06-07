"""外部身份配置加载器

从 data/identities/ 目录加载 YAML 文件，与 DEFAULT_IDENTITIES 合并。
YAML 中的配置优先于硬编码默认值。

配置文件结构示例:

    data/identities/
    ├── expert_code_writer.yaml    # 覆盖已有身份
    └── expert_translator.yaml     # 新增身份

YAML 格式:

    identity_key: expert_code_writer   # 必须与文件名一致
    model_id: expert_code_writer_001
    name: 代码编写专家
    tier: expert
    role: code_writer
    personality: "你是代码编写专家..."
    speaking_style: "简洁、技术化"
    expertise: [Python, JavaScript]
    weaknesses: [UI设计]
    max_tokens: 512
    temperature: 0.3
    model_name: deepseek-v4-flash     # 可选：覆盖 tier 默认模型
    api_key: sk-xxx                   # 可选：覆盖 tier 默认 API key
    api_url: https://api.xxx.com/v1   # 可选：覆盖 tier 默认 API URL
    memory_config:                    # 可选：覆盖 tier 默认记忆配置
      enable_personality: true
      enable_notebook: true
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

_IDENTITIES_DIR = os.path.join("data", "identities")

# 合法字段白名单（防止 YAML 中写入恶意字段）
_VALID_FIELDS = {
    "identity_key", "model_id", "name", "tier", "role",
    "personality", "speaking_style", "expertise", "weaknesses",
    "tool_whitelist", "model_name", "max_tokens", "temperature",
    "memory_config", "api_key", "api_url",
}


def load_yaml_identities(directory: str = None) -> Dict[str, dict]:
    """扫描目录中的 .yaml/.yml 文件，返回 {identity_key: template_dict}。

    Args:
        directory: YAML 文件目录，默认 data/identities/

    Returns:
        解析后的身份模板字典
    """
    dir_path = Path(directory or _IDENTITIES_DIR)
    if not dir_path.is_dir():
        return {}

    try:
        import yaml
    except ImportError:
        logger.warning("[IdentityLoader] PyYAML 未安装，跳过外部身份配置加载")
        return {}

    result: Dict[str, dict] = {}
    for fpath in sorted(dir_path.glob("*.yaml")) + sorted(dir_path.glob("*.yml")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                logger.warning(f"[IdentityLoader] {fpath.name}: 非字典格式，跳过")
                continue

            identity_key = data.get("identity_key", "")
            if not identity_key:
                # 从文件名推断
                identity_key = fpath.stem

            # 过滤非法字段
            filtered = {k: v for k, v in data.items() if k in _VALID_FIELDS}
            unknown = set(data.keys()) - _VALID_FIELDS
            if unknown:
                logger.warning(f"[IdentityLoader] {fpath.name}: 忽略未知字段 {unknown}")

            # 基本校验
            if not filtered.get("tier"):
                logger.warning(f"[IdentityLoader] {fpath.name}: 缺少 tier 字段，跳过")
                continue
            if filtered["tier"] not in ("large", "supervisor", "expert"):
                logger.warning(f"[IdentityLoader] {fpath.name}: tier={filtered['tier']} 非法，跳过")
                continue

            result[identity_key] = filtered
            logger.info(f"[IdentityLoader] 加载外部身份: {identity_key} (tier={filtered['tier']})")

        except Exception as e:
            logger.warning(f"[IdentityLoader] {fpath.name}: 解析失败: {e}")

    return result


def merge_identities(defaults: Dict[str, dict], overrides: Dict[str, dict]) -> Dict[str, dict]:
    """将外部配置合并到默认身份中。

    规则：
    - 已存在的 identity_key：用 overrides 中的非 None 值覆盖 defaults
    - 不存在的 identity_key：直接添加（新增身份）

    Args:
        defaults: DEFAULT_IDENTITIES（硬编码）
        overrides: YAML 加载的配置

    Returns:
        合并后的字典（不修改原字典）
    """
    import copy
    merged = copy.deepcopy(defaults)

    for key, override in overrides.items():
        if key in merged:
            # 合并：override 中的字段覆盖 defaults
            for field, value in override.items():
                if value is not None:
                    merged[key][field] = value
        else:
            # 新增身份：填充必需字段的默认值
            tier = override.get("tier", "expert")
            if "model_id" not in override:
                override["model_id"] = f"{key}_001"
            if "name" not in override:
                override["name"] = key
            if "role" not in override:
                override["role"] = key
            if "personality" not in override:
                override["personality"] = f"你是{override.get('name', key)}。"
            if "speaking_style" not in override:
                override["speaking_style"] = "自然"
            merged[key] = override

    return merged


def load_and_merge(defaults: Dict[str, dict] = None, directory: str = None) -> Dict[str, dict]:
    """加载外部配置并合并到默认身份。

    Args:
        defaults: 硬编码的默认身份，None 则从 identity.DEFAULT_IDENTITIES 读取
        directory: YAML 目录

    Returns:
        合并后的身份字典
    """
    if defaults is None:
        from modules.thinking.identity import DEFAULT_IDENTITIES
        defaults = DEFAULT_IDENTITIES

    overrides = load_yaml_identities(directory)
    if not overrides:
        return defaults

    return merge_identities(defaults, overrides)
