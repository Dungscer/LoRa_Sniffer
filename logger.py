import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import config


def _make_handler(path: Path) -> TimedRotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        path,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def get_sniff_logger(log_dir: Path = config.LOG_DIR) -> logging.Logger:
    logger = logging.getLogger("sniff")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.addHandler(_make_handler(log_dir / "sniff.log"))
        logger.propagate = False
    return logger


def get_audit_logger(log_dir: Path = config.LOG_DIR) -> logging.Logger:
    logger = logging.getLogger("audit")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.addHandler(_make_handler(log_dir / "audit.log"))
        logger.propagate = False
    return logger


def log_frame(
    timestamp: str,
    frequency_mhz: float,
    rssi_dbm: float,
    raw_hex: str,
    log_dir: Path = config.LOG_DIR,
) -> None:
    get_sniff_logger(log_dir).info(
        f"{timestamp} [FRAME] freq={frequency_mhz:.3f}MHz rssi={rssi_dbm:.1f} hex={raw_hex}"
    )


def log_web(
    timestamp: str,
    ip: str,
    method: str,
    path: str,
    status: int,
    log_dir: Path = config.LOG_DIR,
) -> None:
    get_audit_logger(log_dir).info(f"{timestamp} [WEB] {ip} {method} {path} {status}")
