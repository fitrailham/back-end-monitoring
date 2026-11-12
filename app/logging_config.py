"""JSON application logs: pretty output for terminals, compact JSON for collectors."""
from datetime import date, datetime
import json
import logging
import math
import os
import sys
from zoneinfo import ZoneInfo


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class JsonLogFormatter(logging.Formatter):
    def __init__(self, pretty=True, time_zone="Asia/Jakarta"):
        super().__init__()
        self.pretty = pretty
        self.time_zone = ZoneInfo(time_zone)

    def format(self, record):
        document = {
            "timestamp": datetime.fromtimestamp(record.created, self.time_zone).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "application.log"),
            "message": record.getMessage(),
        }
        details = getattr(record, "details", {})
        if isinstance(details, dict):
            document.update({key: value for key, value in details.items() if key not in document and key not in {"exception", "stack"}})
        if record.exc_info:
            document["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info).splitlines(),
            }
        if record.stack_info:
            document["stack"] = record.stack_info.splitlines()
        return json.dumps(_json_value(document), ensure_ascii=False, allow_nan=False,
                          indent=2 if self.pretty else None, default=str)


def configure_logging():
    log_format = os.getenv("LOG_FORMAT", "pretty_json").strip().lower()
    if log_format not in {"pretty_json", "json"}:
        raise ValueError("LOG_FORMAT harus pretty_json atau json")
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").strip().upper(), None)
    if not isinstance(level, int):
        raise ValueError("LOG_LEVEL tidak valid")
    formatter = JsonLogFormatter(pretty=log_format == "pretty_json", time_zone=os.getenv("LOG_TIME_ZONE", "Asia/Jakarta"))
    root = logging.getLogger()
    # Keep other owners' handlers (for example pytest caplog); manage only our own.
    handler = next((h for h in root.handlers if h.get_name() == "icongreen_json"), None)
    if handler is None:
        handler = logging.StreamHandler(sys.__stdout__ or sys.stdout)
        handler.set_name("icongreen_json")
        root.addHandler(handler)
    handler.setFormatter(formatter)
    handler.setLevel(level)
    root.setLevel(level)
