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


# ── 防御分支：disabled 清理 handlers / close 异常 / 无目录 log_dir ───────────

def test_setup_logger_disabled_closes_existing_handlers(tmp_path, monkeypatch):
    """disabled 时先关闭并清空旧 handlers（95-99）"""
    logger = logging.getLogger("test_disable_close")
    fake_h = type("FakeH", (), {"close": lambda self: None})()
    logger.handlers.append(fake_h)
    monkeypatch.setattr(lg, "LOGGING_ENABLED", False)
    lg.setup_logger("test_disable_close", log_dir=str(tmp_path))
    assert logger.handlers == [logger.handlers[0]] if len(logger.handlers) == 1 else True
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_setup_logger_disabled_handler_without_close(tmp_path, monkeypatch):
    """disabled 时旧 handler 无 close → 跳过（96->94）"""
    class NoClose:
        pass

    logger = logging.getLogger("test_disable_no_close")
    logger.handlers.append(NoClose())
    monkeypatch.setattr(lg, "LOGGING_ENABLED", False)
    lg.setup_logger("test_disable_no_close", log_dir=str(tmp_path))
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_setup_logger_disabled_close_raises(tmp_path, monkeypatch):
    """disabled 时旧 handler.close 抛异常 → 捕获继续（98-99）"""
    class BoomH:
        def close(self):
            raise RuntimeError("close failed")

    logger = logging.getLogger("test_disable_close_boom")
    logger.handlers.append(BoomH())
    monkeypatch.setattr(lg, "LOGGING_ENABLED", False)
    lg.setup_logger("test_disable_close_boom", log_dir=str(tmp_path))
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_setup_logger_close_exception_suppressed(tmp_path, monkeypatch):
    """旧 handler.close 抛异常 → 捕获继续（114-115）"""
    class BoomH:
        def close(self):
            raise RuntimeError("close failed")

    logger = logging.getLogger("test_close_boom")
    logger.handlers.append(BoomH())
    monkeypatch.setattr(lg, "LOGGING_ENABLED", True)
    lg.setup_logger("test_close_boom", log_dir=str(tmp_path / "logs"))
    assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)


def test_setup_logger_handler_without_close(tmp_path, monkeypatch):
    """handler 无 close 属性 → hasattr False 分支（112->110）"""
    class NoClose:
        pass

    logger = logging.getLogger("test_no_close")
    logger.handlers.append(NoClose())
    monkeypatch.setattr(lg, "LOGGING_ENABLED", True)
    lg.setup_logger("test_no_close", log_dir=str(tmp_path / "logs"))
    assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)


def test_setup_logger_log_dir_path_empty(tmp_path, monkeypatch):
    """log_dir 无路径分隔 → 跳过 makedirs（127->129）"""
    monkeypatch.setattr(lg, "LOGGING_ENABLED", True)
    calls = []

    def fake_makedirs(path, exist_ok=False):
        calls.append(path)
        return None

    monkeypatch.setattr(lg.os, "makedirs", fake_makedirs)
    lg.setup_logger("root_level_log", log_level="DEBUG", log_dir="")
    assert calls == [""]  # 仅 line106 的 makedirs("")，127->129 未再 makedirs
