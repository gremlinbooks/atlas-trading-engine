from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "fields": fields,
        }
        return json.dumps(payload, ensure_ascii=True)


def get_logger(name: str, path: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path)
    handler.setFormatter(JsonLineFormatter())

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    logger.info(message, extra={"fields": fields})
