"""
One JSON object per log line (production), so CloudWatch can search fields and turn
them into metrics. Extra fields passed with `extra={...}` are included. No request
bodies, tokens or personal data are ever logged by the app; this formatter adds none.
"""

from __future__ import annotations

import json
import logging

_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                body[key] = value if isinstance(value, (int, float, str, bool, type(None))) else str(value)
        if record.exc_info:
            body["exception"] = self.formatException(record.exc_info)
        return json.dumps(body, default=str)
