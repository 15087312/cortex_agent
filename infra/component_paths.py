"""用户级前置组件目录（下载的 pip 包 / ffmpeg 工具）。

Release 包是 PyInstaller onedir 构建：内置解释器没有可用的 ``python -m pip``，
安装目录也通常不可写。因此用户点「下载」安装的组件统一放到 ~/.cortex 下，
后端启动时把组件目录注入 ``sys.path``，开发态与打包态行为一致。
"""
import sys
from pathlib import Path


def cortex_home() -> Path:
    return Path.home() / ".cortex"


def components_dir() -> Path:
    """pip 组件安装目录（启动时注入 sys.path）。"""
    return cortex_home() / "components"


def tools_dir() -> Path:
    """系统工具目录（ffmpeg 等）。"""
    return cortex_home() / "tools"


def ensure_components_on_path() -> str:
    """创建组件目录并注入 sys.path，返回目录路径。幂等。"""
    d = components_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = str(d)
    if p not in sys.path:
        sys.path.insert(0, p)
    return p
