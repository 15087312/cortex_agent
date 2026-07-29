"""
Structured logging via loguru.
"""
import sys
from loguru import logger as _loguru_logger


_loguru_logger.remove()

_loguru_logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[name]}</cyan> | {message}",
    level="DEBUG",
    colorize=True,
)

_loguru_logger.add(
    "logs/cortex_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="14 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[name]} | {message}",
    level="DEBUG",
)


def setup_logger(name: str):
    """Create a named logger instance."""
    return _loguru_logger.bind(name=name)


def get_logger(name: str):
    """Alias for setup_logger."""
    return setup_logger(name)
