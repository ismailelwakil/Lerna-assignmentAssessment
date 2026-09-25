"""Tiny structured logger for the module (JSON lines, no secrets)."""
from __future__ import annotations

import json
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"assessment.{name}")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            '{"ts":"%(asctime)s","logger":"%(name)s","msg":"%(message)s"}'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(name: str, **ctx) -> None:
    get_logger(name).info(json.dumps({"event": name, **ctx}, default=str,
                                      ensure_ascii=False))
