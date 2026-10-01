from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import APP_NAME, LOG_PATH

_configured = False


def setup(verbose: bool = False) -> logging.Logger:
    global _configured
    logger = logging.getLogger(APP_NAME)
    if _configured:
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(message)s")
        )
        logger.addHandler(handler)
    except Exception as exc:
        # never let logging setup kill the app, but make the failure visible
        print(f"[WordGrab] log file unavailable at {LOG_PATH}: {exc}", flush=True)
    if verbose or sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        stream.setLevel(logging.DEBUG if verbose else logging.WARNING)
        logger.addHandler(stream)
    _configured = True
    return logger


def get_logger(verbose: bool = False) -> logging.Logger:
    return setup(verbose)


def log_exception(context: str) -> None:
    get_logger().exception(context)
