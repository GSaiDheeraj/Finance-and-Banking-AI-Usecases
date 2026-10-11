"""Logging configuration for the credit-risk pipeline.

A single named logger (`credit_risk`) writes plain string lines to stderr, and —
when `CREDIT_RISK_LOG_FILE` is set — also appends them to that file. Every line
carries the code place (`module:line funcName`) so a message like "LLM
initialized" is directly traceable to the file/function that emitted it.

Env vars
--------
* `CREDIT_RISK_LOG_LEVEL` — DEBUG | INFO | WARNING | ERROR (default INFO).
* `CREDIT_RISK_LOG_FILE`  — optional path; logs are appended here as well as stderr.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_LOGGER_NAME = "credit_risk"

# Plain string format with code place: module, line number and function name.
_PLAIN_FORMAT = "%(asctime)s %(levelname)s [%(name)s %(module)s:%(lineno)d %(funcName)s] %(message)s"


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """Return the package logger, configuring handlers once."""
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        level_name = (os.getenv("CREDIT_RISK_LOG_LEVEL", "INFO") or "INFO").upper()
        logger.setLevel(getattr(logging, level_name, logging.INFO))

        formatter = logging.Formatter(fmt=_PLAIN_FORMAT)
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        log_file = os.getenv("CREDIT_RISK_LOG_FILE")
        if log_file:
            try:
                file_handler = logging.FileHandler(log_file, encoding="utf-8")
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            except OSError as exc:
                logger.warning("could not open log file %r: %s", log_file, exc)

        logger.propagate = False
        _CONFIGURED = True
    return logger
