"""
版本信息 — 项目版本定义（从 VERSION 文件读取）
"""

from pathlib import Path
import re
import datetime


def _read_version_file() -> str:
    """从 VERSION 文件读取版本号"""
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        with open(version_file, "r") as f:
            return f.read().strip()
    return "0.0.0-unknown"


_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9][a-zA-Z0-9-]*)?$")


def _get_version_name(version: str) -> str:
    """根据版本号推导版本名称"""
    if "unknown" in version:
        return "Unknown"
    elif "control" in version:
        return "Control Mode"
    elif "beta" in version:
        return "Beta"
    elif "alpha" in version:
        return "Alpha"
    else:
        return "Release"


# 从 VERSION 文件读取
__version__ = _read_version_file()
__version_name__ = _get_version_name(__version__)
import datetime
__build_date__ = datetime.date.today().isoformat()

# 版本组件解析
_version_parts = __version__.split("-", 1)
__version_core__ = _version_parts[0]  # x.y.z
__version_suffix__ = _version_parts[1] if len(_version_parts) > 1 else None


def get_version_string() -> str:
    """获取完整版本字符串"""
    return f"v{__version__} ({__version_name__})"


def get_version_info() -> dict:
    """获取版本信息字典"""
    return {
        "version": __version__,
        "core": __version_core__,
        "suffix": __version_suffix__,
        "name": __version_name__,
        "build_date": __build_date__,
        "full": get_version_string(),
    }


def update_version(new_version: str) -> bool:
    """更新版本号（写入 VERSION 文件）

    Args:
        new_version: 新版本号（格式：x.y.z-suffix）

    Returns:
        是否更新成功
    """
    if not _VERSION_PATTERN.match(new_version):
        print(f"更新版本号失败: 无效格式 '{new_version}'，期望 x.y.z 或 x.y.z-suffix")
        return False
    try:
        version_file = Path(__file__).parent.parent / "VERSION"
        with open(version_file, "w") as f:
            f.write(new_version)
        return True
    except Exception as e:
        print(f"更新版本号失败: {e}")
        return False

