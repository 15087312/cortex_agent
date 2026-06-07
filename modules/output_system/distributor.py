"""
分发器 - 多渠道分发、流式输出
"""
from typing import Generator, Optional, Callable
from pathlib import Path
import sys
import time
from utils.logger import setup_logger

logger = setup_logger("distributor")

# Whitelist of allowed directories for file output
ALLOWED_OUTPUT_DIRS = [
    Path("data/output").resolve(),
    Path("logs").resolve(),
]


class OutputDistributor:
    def __init__(self):
        self.supported_channels = ["console", "file", "api", "voice"]
        self.streaming_speed = 0.01
        self.callbacks: list[Callable] = []

    def _validate_file_path(self, target: str) -> bool:
        """Q-2: Validate file path is within allowed directories"""
        try:
            target_path = Path(target).resolve()
            for allowed_dir in ALLOWED_OUTPUT_DIRS:
                # Use try/except for Python 3.8 compatibility (is_relative_to added in 3.9)
                try:
                    target_path.relative_to(allowed_dir)
                    return True
                except ValueError:
                    continue
            logger.error(f"[路径验证失败] 文件路径超出白名单目录: {target}")
            return False
        except (OSError, RuntimeError) as e:
            logger.error(f"[路径验证失败] 无效的文件路径: {target} - {e}")
            return False

    def register_callback(self, callback: Callable[[str], None]) -> None:
        self.callbacks.append(callback)

    def stream_output(self, content: str, channel: str = "console") -> Generator[str, None, None]:
        if channel == "console":
            for char in content:
                if char == '\n':
                    sys.stdout.write(char)
                else:
                    sys.stdout.write(char)
                sys.stdout.flush()
                if char not in ' \n\t':
                    time.sleep(self.streaming_speed)
                yield char
        else:
            yield content

    def distribute(self, content: str, channel: str = "console", target: Optional[str] = None) -> bool:
        try:
            if channel == "console":
                logger.info(f"输出内容: {content[:100]}")
                for cb in self.callbacks:
                    cb(content)
            elif channel == "file" and target:
                if not self._validate_file_path(target):
                    return False
                target_path = Path(target)
                # Ensure parent directory exists
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"已写入文件: {target}")
            elif channel == "api":
                logger.info(f"API分发: {content[:50]}...")
            elif channel == "voice":
                logger.info(f"语音分发: {content[:50]}...")
            return True
        except Exception as e:
            logger.error(f"分发失败: {e}")
            return False

    def distribute_stream(self, generator: Generator[str, None, None]) -> None:
        collected = []
        for chunk in generator:
            collected.append(chunk)
        content = "".join(collected)
        for cb in self.callbacks:
            cb(content)
