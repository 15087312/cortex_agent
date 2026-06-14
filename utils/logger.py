"""
全局日志工具 - 统一日志格式、日志级别
"""
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 全局开关，CLI 模式设为 False 可禁用所有日志
from config.settings import settings as _settings
LOGGING_ENABLED = _settings.LOGGING_ENABLED


class _JsonFormatter(logging.Formatter):
    """Outputs each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "filename": record.filename,
            "lineno": record.lineno,
        }
        return json.dumps(log_entry, ensure_ascii=False)


def _make_formatter(use_json: bool) -> logging.Formatter:
    """Return a JSON or text formatter."""
    if use_json:
        return _JsonFormatter(datefmt='%Y-%m-%d %H:%M:%S')
    return logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def _make_file_formatter(use_json: bool) -> logging.Formatter:
    """Return a JSON or text formatter (file variant includes filename:lineno)."""
    if use_json:
        return _JsonFormatter(datefmt='%Y-%m-%d %H:%M:%S')
    return logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


# 第三方库日志配置（只执行一次）
_third_party_configured = False

def _configure_third_party_loggers():
    """配置第三方库的日志级别（只执行一次）"""
    global _third_party_configured
    if _third_party_configured:
        return
    _third_party_configured = True
    
    # 第三方库降级为 WARNING
    noisy_loggers = [
        "diskcache",
        "sentence_transformers",
        "faiss",
        "urllib3",
        "httpx",
        "httpcore",
        "aiohttp",
        "asyncio",
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def setup_logger(
    name: str,
    log_level: str = "INFO",
    log_dir: str = None
) -> logging.Logger:
    """设置日志器"""

    if log_dir is None:
        log_dir = str(PROJECT_ROOT / "data" / "logs")

    # 如果全局禁用，直接返回静默 logger
    if not LOGGING_ENABLED:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    # 设置第三方库日志级别（减少噪音）
    _configure_third_party_loggers()

    # 正常创建日志目录
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    # 清除已有处理器
    logger.handlers.clear()

    # 判断是否使用 JSON 格式
    use_json = os.environ.get("LOG_FORMAT", "").lower() == "json"

    # 控制台处理器（级别与配置一致）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_handler.setFormatter(_make_formatter(use_json))
    logger.addHandler(console_handler)

    # 文件处理器（按天轮转，文件名带日期）
    log_file = os.path.join(log_dir, f"{name}.log")
    file_handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=14,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.suffix = '%Y%m%d.log'
    file_handler.setFormatter(_make_file_formatter(use_json))
    logger.addHandler(file_handler)
    logger.propagate = False  # 阻止传播到根 logger，防止双重输出

    return logger


# 全局默认日志器（延迟初始化，避免 import 时创建目录）
_default_logger = None


def get_default_logger():
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logger("humanoid_agi")
    return _default_logger