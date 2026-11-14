"""Structured JSON logging with contextual run_id tracing for operational observability."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """Custom logging formatter outputting structured JSON lines."""

    def __init__(self, run_id: Optional[str] = None):
        super().__init__()
        self.run_id = run_id or "SYSTEM"

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "run_id": getattr(record, "run_id", self.run_id),
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logger(name: str = "ingestion_framework", run_id: Optional[str] = None, log_level: str = "INFO") -> logging.Logger:
    """Configures and returns a contextual JSON logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter(run_id=run_id))
        logger.addHandler(handler)
        logger.propagate = False

    return logger
