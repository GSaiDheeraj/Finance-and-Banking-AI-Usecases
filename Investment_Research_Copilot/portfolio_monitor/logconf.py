"""
Logging configuration for the portfolio-monitoring pipeline.

A single named logger (`portfolio_monitor`) writes human-readable lines to stderr, and —
when `PM_LOG_FILE` is set — also appends to that file. This is where the reasoning trace is
recorded: what market data was fetched, which sleeves drifted, which limits breached, what
the constructor selected and why, and where guardrails corrected an LLM proposal.

Env vars
--------
* `PM_LOG_LEVEL` — DEBUG | INFO | WARNING | ERROR (default INFO).
* `PM_LOG_FILE`  — optional path; logs are appended here as well as stderr.
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_LOGGER_NAME = "portfolio_monitor"


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """Return the package logger, configuring handlers once."""
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    if not _CONFIGURED:
        level_name = (os.getenv("PM_LOG_LEVEL", "INFO") or "INFO").upper()
        logger.setLevel(getattr(logging, level_name, logging.INFO))

        fmt = logging.Formatter(
            "%(asctime)s [portfolio_monitor] %(levelname)-7s %(message)s", "%H:%M:%S"
        )
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        log_file = os.getenv("PM_LOG_FILE")
        if log_file:
            try:
                fh = logging.FileHandler(log_file, encoding="utf-8")
                fh.setFormatter(fmt)
                logger.addHandler(fh)
            except Exception:
                pass

        logger.propagate = False
        _CONFIGURED = True
    return logger
