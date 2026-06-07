"""
版本更新检查 — 启动时检查是否有新版本（支持 GitHub 查询）
"""

import json
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from utils.logger import setup_logger

logger = setup_logger("version_check")


class VersionChecker:
    """版本检查器 — 检查更新并显示提示（支持 GitHub API）"""

    # 本地版本检查缓存文件
    CACHE_FILE = Path.home() / ".cache" / "ai_cli_version_check"

    # GitHub 项目信息（可自定义）
    GITHUB_OWNER = "anthropics"
    GITHUB_REPO = "humanoid-agi-backend"
    GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

    # 检查间隔（小时）
    CHECK_INTERVAL_HOURS = 24

    # API 请求超时（秒）
    API_TIMEOUT = 5

    @classmethod
    def should_check(cls) -> bool:
        """判断是否需要检查更新"""
        cache = cls._read_cache()
        if not cache:
            return True

        last_check = cache.get("last_check", 0)
        now = time.time()
        return (now - last_check) > (cls.CHECK_INTERVAL_HOURS * 3600)

    @classmethod
    def check_for_updates(cls) -> Optional[str]:
        """检查更新并返回提示信息（同步包装）"""
        try:
            loop = asyncio.get_event_loop()
            # 如果已有运行中的循环，创建新任务
            if loop.is_running():
                logger.debug("[版本检查] 当前在异步上下文中，跳过同步版本检查（可在异步上下文调用 _async_check_for_updates）")
                return None
            return loop.run_until_complete(cls._async_check_for_updates())
        except (RuntimeError, OSError):
            # 无事件循环，返回 None
            return None

    @classmethod
    async def _async_check_for_updates(cls) -> Optional[str]:
        """异步检查更新"""
        if not cls.should_check():
            return None

        cls._write_cache({"last_check": time.time()})

        from cortex.version import __version__, __version_core__

        # 尝试从 GitHub 获取最新版本
        latest_version = await cls._fetch_latest_version_from_github()

        if not latest_version:
            logger.debug("[版本检查] 无法从 GitHub 获取版本信息，跳过更新提示")
            return None

        # 只比较版本核心部分（x.y.z），忽略后缀
        if cls._compare_versions(__version_core__, latest_version) < 0:
            return cls._format_update_prompt(latest_version)

        return None

    @classmethod
    async def _fetch_latest_version_from_github(cls) -> Optional[str]:
        """从 GitHub 获取最新版本（使用 aiohttp）"""
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    cls.GITHUB_API_URL,
                    timeout=aiohttp.ClientTimeout(total=cls.API_TIMEOUT),
                    headers={"Accept": "application/vnd.github+json"},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "").lstrip("v")
                        if tag_name:
                            logger.debug(f"[版本检查] 从 GitHub 获取最新版本: {tag_name}")
                            return tag_name
                    else:
                        logger.debug(
                            f"[版本检查] GitHub API 返回状态码 {response.status}"
                        )
        except asyncio.TimeoutError:
            logger.debug("[版本检查] GitHub API 请求超时")
        except Exception as e:
            logger.debug(f"[版本检查] 从 GitHub 获取版本失败: {e}")

        return None

    @classmethod
    def _compare_versions(cls, v1: str, v2: str) -> int:
        """比较两个版本号。返回 -1 (v1<v2), 0 (v1==v2), 1 (v1>v2)"""
        try:
            # 简单版本比较：去掉后缀，比较 x.y.z 部分
            v1_parts = [int(x) for x in v1.split("-")[0].split(".")]
            v2_parts = [int(x) for x in v2.split("-")[0].split(".")]

            # 补齐长度
            max_len = max(len(v1_parts), len(v2_parts))
            v1_parts += [0] * (max_len - len(v1_parts))
            v2_parts += [0] * (max_len - len(v2_parts))

            for p1, p2 in zip(v1_parts, v2_parts):
                if p1 < p2:
                    return -1
                elif p1 > p2:
                    return 1
            return 0
        except (ValueError, IndexError):
            return 0

    @classmethod
    def _format_update_prompt(cls, latest_version: str) -> str:
        """格式化更新提示"""
        return (
            f"\n[bold yellow]ℹ️  发现新版本 v{latest_version}[/bold yellow]\n"
            f"[dim]运行以下命令更新:[/dim]\n"
            f"  pip install --upgrade ai-cli\n"
            f"[dim]或访问: https://github.com/{cls.GITHUB_OWNER}/{cls.GITHUB_REPO}/releases[/dim]\n"
        )

    @classmethod
    def _read_cache(cls) -> Optional[Dict]:
        """读取缓存"""
        try:
            if cls.CACHE_FILE.exists():
                with open(cls.CACHE_FILE, "r") as f:
                    return json.load(f)
        except json.JSONDecodeError:
            try:
                cls.CACHE_FILE.unlink()  # 删除损坏缓存文件
            except OSError:
                pass
        except Exception:
            pass
        return None

    @classmethod
    def _write_cache(cls, data: Dict) -> None:
        """写入缓存"""
        try:
            cls.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(cls.CACHE_FILE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass  # 缓存失败不影响功能


def get_update_prompt() -> Optional[str]:
    """获取更新提示（如果有新版本）"""
    return VersionChecker.check_for_updates()

