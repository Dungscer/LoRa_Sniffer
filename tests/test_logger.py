import logging
from logging.handlers import TimedRotatingFileHandler
import logger


def test_sniff_logger_has_file_handler(tmp_path):
    # Reset so fresh handlers are created for tmp_path
    log = logging.getLogger("sniff")
    log.handlers.clear()
    result = logger.get_sniff_logger(tmp_path)
    assert any(isinstance(h, TimedRotatingFileHandler) for h in result.handlers)


def test_audit_logger_has_file_handler(tmp_path):
    log = logging.getLogger("audit")
    log.handlers.clear()
    result = logger.get_audit_logger(tmp_path)
    assert any(isinstance(h, TimedRotatingFileHandler) for h in result.handlers)


def test_sniff_logger_writes_utf8(tmp_path):
    log = logging.getLogger("sniff")
    log.handlers.clear()
    logger.get_sniff_logger(tmp_path)
    logger.log_frame("2025-05-22T14:00:00Z", 868.097, -87.3, "abcd", log_dir=tmp_path)
    content = (tmp_path / "sniff.log").read_text(encoding="utf-8")
    assert "868.097" in content
    assert "abcd" in content


def test_log_files_created_in_correct_dir(tmp_path):
    for lg in (logging.getLogger("sniff"), logging.getLogger("audit")):
        lg.handlers.clear()
    logger.get_sniff_logger(tmp_path)
    logger.get_audit_logger(tmp_path)
    logger.log_frame("2025-05-22T14:00:00Z", 868.1, -80.0, "ff", log_dir=tmp_path)
    logger.log_web(
        "2025-05-22T14:00:01Z", "127.0.0.1", "GET", "/api/frames", 200, log_dir=tmp_path
    )
    assert (tmp_path / "sniff.log").exists()
    assert (tmp_path / "audit.log").exists()
