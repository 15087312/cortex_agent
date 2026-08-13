"""utils/logger.py — 统一日志工具"""
import json
import logging
import os

import pytest

import utils.logger as lg


def test_json_formatter_outputs_json():
    fmt = lg._JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    record = logging.LogRecord(
        name="test_mod", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    out = json.loads(fmt.format(record))
    assert out["level"] == "INFO"
    assert out["logger"] == "test_mod"
    assert out["message"] == "hello world"
    assert "filename" in out and "lineno" in out


def test_make_formatters():
    assert isinstance(lg._make_formatter(True), lg._JsonFormatter)
    assert isinstance(lg._make_formatter(False), logging.Formatter)
    assert isinstance(lg._make_file_formatter(True), lg._JsonFormatter)
    assert isinstance(lg._make_file_formatter(False), logging.Formatter)


def test_configure_third_party_loggers():
    lg._third_party_configured = False
    lg._configure_third_party_loggers()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING
    # 幂等：再次调用不重复处理
    lg._configure_third_party_loggers()
    assert logging.getLogger("aiohttp").level == logging.WARNING


def test_setup_logger_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "LOGGING_ENABLED", False)
    logger = lg.setup_logger("test_disabled", log_dir=str(tmp_path))
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_setup_logger_enabled_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "LOGGING_ENABLED", True)
    name = "test_module_xyz"
    log_dir = str(tmp_path / "logs")
    logger = lg.setup_logger(name, log_level="INFO", log_dir=log_dir)
    assert logger.level == logging.INFO
    types = {type(h).__name__ for h in logger.handlers}
    assert "StreamHandler" in types
    assert "TimedRotatingFileHandler" in types
    # 写入并落盘
    logger.info("write me")
    for h in logger.handlers:
        h.flush()
    log_file = os.path.join(log_dir, name + ".log")
    assert os.path.exists(log_file)


def test_get_logger_cached():
    a = lg.get_logger("cache_test_mod")
    b = lg.get_logger("cache_test_mod")
    assert a is b


def test_get_default_logger():
    logger = lg.get_default_logger()
    assert logger.name == "humanoid_agi"
    assert lg.get_default_logger() is logger
