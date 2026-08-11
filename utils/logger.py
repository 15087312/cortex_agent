"""
全局日志工具 - 统一日志格式、日志级别

使用方式：
    logger = setup_logger("my_module")     # 第一选择 - 带 console + file handler
    logger = get_logger("my_module")       # 第二选择 - 防重复初始化（推荐）
"""

import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from config.settings import settings as _settings
LOGGING_ENABLED = _settings.LOGGING_ENABLED


# 已初始化的 logger 缓存，防止 setup_logger 重复清空 handlers
_logger_cache: dict[str, logging.Logger] = {}


class _JsonFormatter(logging.Formatter):
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
    if use_json:
        return _JsonFormatter(datefmt='%Y-%m-%d %H:%M:%S')
    return logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def _make_file_formatter(use_json: bool) -> logging.Formatter:
    if use_json:
        return _JsonFormatter(datefmt='%Y-%m-%d %H:%M:%S')
    return logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


_third_party_configured = False

def _configure_third_party_loggers():
    global _third_party_configured
    if _third_party_configured:
        return
    _third_party_configured = True
    noisy_loggers = [
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
    log_level: str = None,
    log_dir: str = None
) -> logging.Logger:
    """设置日志器 — 带 console + file handler。

    注意：同一 name 第二次调用会清空 handlers 重建。
    推荐使用 get_logger(name) 来自动缓存。
    """
    if log_level is None:
        # 设置页的 LOG_LEVEL 配置生效（此前硬编码 "INFO"，改动无效 = §22 摆设）
        log_level = getattr(_settings, "LOG_LEVEL", "") or "INFO"
    if log_dir is None:
        log_dir = str(PROJECT_ROOT / "data" / "logs")

    if not LOGGING_ENABLED:
        logger = logging.getLogger(name)
        for _h in list(logger.handlers):
            try:
                if hasattr(_h, "close"):
                    _h.close()
            except Exception:
                pass
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    _configure_third_party_loggers()
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))
    for _h in list(logger.handlers):
        try:
            if hasattr(_h, "close"):
                _h.close()
        except Exception:
            pass
    logger.handlers.clear()

    use_json = os.environ.get("LOG_FORMAT", "").lower() == "json"

    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_handler.setFormatter(_make_formatter(use_json))
    logger.addHandler(console_handler)

    log_file = os.path.join(log_dir, f"{name.replace('.', '/')}.log")
    log_dir_path = os.path.dirname(log_file)
    if log_dir_path:
        os.makedirs(log_dir_path, exist_ok=True)
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
    logger.propagate = False

    return logger


def get_logger(name: str, log_level: str = "INFO") -> logging.Logger:
    """获取日志器（带缓存，防止重复初始化）。"""
    if name in _logger_cache:
        return _logger_cache[name]
    logger = setup_logger(name, log_level=log_level)
    _logger_cache[name] = logger
    return logger


_default_logger = None

def get_default_logger():
    global _default_logger
    if _default_logger is None:
        _default_logger = get_logger("humanoid_agi")
    return _default_logger