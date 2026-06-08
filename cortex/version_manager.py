#!/usr/bin/env python3
"""
版本号管理工具 — 管理 VERSION 文件和版本信息
"""

import sys
import re
from pathlib import Path
from typing import Tuple, Optional


class VersionManager:
    """版本号管理器"""

    VERSION_FILE = Path(__file__).parent.parent / "VERSION"  # 项目根目录的 VERSION 文件

    @classmethod
    def get_current(cls) -> str:
        """获取当前版本号"""
        if cls.VERSION_FILE.exists():
            return cls.VERSION_FILE.read_text().strip()
        return "0.0.0-unknown"

    @classmethod
    def set_version(cls, version: str) -> bool:
        """设置版本号"""
        if not cls._validate_version(version):
            print(f"❌ 无效的版本格式: {version}")
            print(f"   期望格式: x.y.z 或 x.y.z-suffix (例如: 0.2.0-control)")
            return False

        cls.VERSION_FILE.write_text(version)
        print(f"✅ 版本号已更新: {version}")
        return True

    @classmethod
    def increment_major(cls) -> bool:
        """主版本号 +1（x+1.0.0）"""
        parsed = cls._parse_version(cls.get_current())
        if parsed is None:
            print(f"❌ 无法解析当前版本: {cls.get_current()}")
            return False
        x, y, z, suffix = parsed
        new_version = f"{x + 1}.0.0"
        return cls.set_version(new_version)

    @classmethod
    def increment_minor(cls) -> bool:
        """次版本号 +1（x.y+1.0）"""
        parsed = cls._parse_version(cls.get_current())
        if parsed is None:
            print(f"❌ 无法解析当前版本: {cls.get_current()}")
            return False
        x, y, z, suffix = parsed
        new_version = f"{x}.{y + 1}.0"
        return cls.set_version(new_version)

    @classmethod
    def increment_patch(cls) -> bool:
        """修订版本号 +1（x.y.z+1）"""
        parsed = cls._parse_version(cls.get_current())
        if parsed is None:
            print(f"❌ 无法解析当前版本: {cls.get_current()}")
            return False
        x, y, z, suffix = parsed
        new_version = f"{x}.{y}.{z + 1}"
        return cls.set_version(new_version)

    @classmethod
    def set_suffix(cls, suffix: str) -> bool:
        """设置版本后缀（-control, -beta, -alpha 等）"""
        parsed = cls._parse_version(cls.get_current())
        if parsed is None:
            print(f"❌ 无法解析当前版本: {cls.get_current()}")
            return False
        x, y, z, _ = parsed
        new_version = f"{x}.{y}.{z}-{suffix}"
        return cls.set_version(new_version)

    @classmethod
    def remove_suffix(cls) -> bool:
        """移除版本后缀"""
        parsed = cls._parse_version(cls.get_current())
        if parsed is None:
            print(f"❌ 无法解析当前版本: {cls.get_current()}")
            return False
        x, y, z, _ = parsed
        new_version = f"{x}.{y}.{z}"
        return cls.set_version(new_version)

    @classmethod
    def show_info(cls) -> None:
        """显示版本信息"""
        current = cls.get_current()
        parsed = cls._parse_version(current)
        if parsed is None:
            print(f"\n❌ 无法解析版本: {current}")
            return
        x, y, z, suffix = parsed

        print("\n📋 版本信息")
        print("─" * 50)
        print(f"当前版本：    v{current}")
        print(f"主版本：      {x}")
        print(f"次版本：      {y}")
        print(f"修订版本：    {z}")
        if suffix:
            print(f"后缀：        {suffix}")
        print(f"版本文件：    {cls.VERSION_FILE}")
        print("─" * 50)

    @staticmethod
    def _parse_version(version: str) -> Optional[Tuple[int, int, int, Optional[str]]]:
        """解析版本号，失败返回 None"""
        match = re.match(r"(\d+)\.(\d+)\.(\d+)(?:-(.+))?", version)
        if match:
            x, y, z, suffix = match.groups()
            return int(x), int(y), int(z), suffix
        return None

    @staticmethod
    def _validate_version(version: str) -> bool:
        """验证版本格式"""
        return bool(re.match(r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9][a-zA-Z0-9-]*)?$", version))


def main():
    """命令行接口"""
    if len(sys.argv) < 2:
        print("版本号管理工具")
        print()
        print("用法:")
        print("  python -m cortex.version_manager show          # 显示版本信息")
        print("  python -m cortex.version_manager get           # 获取当前版本号")
        print("  python -m cortex.version_manager set <ver>     # 设置版本号 (例如: 0.3.0)")
        print("  python -m cortex.version_manager major         # 主版本号 +1")
        print("  python -m cortex.version_manager minor         # 次版本号 +1")
        print("  python -m cortex.version_manager patch         # 修订版本号 +1")
        print("  python -m cortex.version_manager suffix <suf>  # 设置后缀 (例如: -beta)")
        print("  python -m cortex.version_manager remove-suffix # 移除后缀")
        print()
        print("示例:")
        print("  python -m cortex.version_manager set 0.3.0")
        print("  python -m cortex.version_manager minor")
        print("  python -m cortex.version_manager suffix control")
        return

    cmd = sys.argv[1].lower()

    if cmd == "show":
        VersionManager.show_info()
    elif cmd == "get":
        print(VersionManager.get_current())
    elif cmd == "set" and len(sys.argv) >= 3:
        VersionManager.set_version(sys.argv[2])
    elif cmd == "major":
        VersionManager.increment_major()
    elif cmd == "minor":
        VersionManager.increment_minor()
    elif cmd == "patch":
        VersionManager.increment_patch()
    elif cmd == "suffix" and len(sys.argv) >= 3:
        VersionManager.set_suffix(sys.argv[2])
    elif cmd == "remove-suffix":
        VersionManager.remove_suffix()
    else:
        print(f"❌ 未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
