from datetime import datetime, timezone
import json
import logging
import sys

import pytest

from app.logging_config import JsonLogFormatter, configure_logging


@pytest.mark.parametrize("pretty", [True, False])
def test_json_record_retains_native_fields_and_safely_serializes_exceptions(pretty):
    try:
        raise ValueError('contoh "error"\nbaris kedua')
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "Job %s gagal", (85,), sys.exc_info())
    record.event = "realtime.failed"
    record.details = {"job_id": 85, "received_fields": ["real_health_state", "total_income"],
        "invalid_value": float("nan"), "measured_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "message": "must not replace message"}

    rendered = JsonLogFormatter(pretty=pretty).format(record)
    document = json.loads(rendered)
    assert document["message"] == "Job 85 gagal"
    assert document["job_id"] == 85
    assert document["received_fields"] == ["real_health_state", "total_income"]
    assert document["invalid_value"] is None
    assert document["measured_at"] == "2026-01-01T00:00:00+00:00"
    assert document["timestamp"].endswith("+07:00")
    assert document["exception"]["type"] == "ValueError"
    assert document["exception"]["traceback"]
    assert (len(rendered.splitlines()) > 1) is pretty


def test_logging_configuration_is_idempotent_and_preserves_other_handlers(monkeypatch):
    root = logging.getLogger()
    previous_level = root.level
    sentinel = logging.NullHandler()
    root.addHandler(sentinel)
    existing = next((handler for handler in root.handlers if handler.get_name() == "icongreen_json"), None)
    previous_formatter = existing.formatter if existing else None
    previous_handler_level = existing.level if existing else None
    try:
        monkeypatch.setenv("LOG_FORMAT", "json")
        configure_logging()
        configure_logging()
        owned = [handler for handler in root.handlers if handler.get_name() == "icongreen_json"]
        assert len(owned) == 1
        assert sentinel in root.handlers
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "done", (), None)
        assert len(owned[0].format(record).splitlines()) == 1
    finally:
        root.removeHandler(sentinel)
        root.setLevel(previous_level)
        if existing:
            existing.setFormatter(previous_formatter)
            existing.setLevel(previous_handler_level)
        else:
            for handler in list(root.handlers):
                if handler.get_name() == "icongreen_json":
                    root.removeHandler(handler)
                    handler.close()
