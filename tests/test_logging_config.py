from datetime import datetime, timezone
from io import StringIO
import json
import logging
import re
import sys

import pytest
from rich.console import Console

from app.logging_config import JsonLogFormatter, configure_logging
from app.scheduler import terminal_logging


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


def test_scheduler_smart_output_and_json_audit_are_separate(monkeypatch, tmp_path):
    terminal = StringIO()
    audit_file = tmp_path / "scheduler.audit.jsonl"
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(terminal_logging, "AUDIT_PATH", audit_file)
    monkeypatch.setenv("LOG_FORMAT", "pretty_json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("TTY_COMPATIBLE", raising=False)
    root = logging.getLogger()
    previous_level, previous_handlers = root.level, root.handlers[:]
    log = logging.getLogger("app.scheduler.scheduler")
    try:
        terminal_logging.configure_scheduler_logging()
        terminal_logging.configure_scheduler_logging()
        log.info("[SCHEDULER] Cycle completed")
        logging.getLogger("app.jobs.realtime_sync").warning(
            "Snapshot KPI dilewati.",
            extra={"event": "realtime.snapshot_skipped", "details": {
                "plant_code": "NE=001", "endpoint": "getStationRealKpi",
                "diagnostic": {"code": "KPI_EMPTY", "reason": "dataItemMap kosong",
                               "solution": "Periksa izin Northbound."},
                "impact": "Snapshot lama dipertahankan.",
                "evidence": {"raw_id": 17},
            }},
        )
        try:
            raise ValueError("debug-only exception contents")
        except ValueError:
            log.exception("Siklus scheduler gagal.", extra={"details": {"job": "plant_sync"}})
    finally:
        for handler in root.handlers[:]:
            if handler not in previous_handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in previous_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(previous_level)

    success, warning, error = terminal.getvalue().splitlines()
    assert re.fullmatch(r"\[\d{2}:\d{2}:\d{2}\] SUCCESS SCHEDULER Cycle completed", success)
    assert "WARNING REALTIME Snapshot KPI dilewati." in warning
    assert "plant=NE=001 endpoint=getStationRealKpi kode=KPI_EMPTY" in warning
    assert "penyebab=dataItemMap kosong" in warning
    assert "tindakan=Periksa izin Northbound." in warning
    assert "Snapshot lama dipertahankan." in warning
    assert "ERROR SCHEDULER" in error and "lokasi=plant_sync" in error
    assert "kode=DATA_FORMAT" in error and "solusi=" in error
    assert "\x1b" not in terminal.getvalue()
    assert "debug-only exception contents" not in error
    assert "raw_id" not in warning

    records = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert [record["level"] for record in records] == ["INFO", "WARNING", "ERROR"]
    assert records[1]["evidence"] == {"raw_id": 17}
    assert records[2]["exception"]["message"] == "debug-only exception contents"
    assert records[2]["exception"]["traceback"]


def test_scheduler_smart_colors_are_limited_to_terminal_output():
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system="standard",
                      no_color=False, legacy_windows=False)
    handler = terminal_logging.SmartLogHandler(console, "Asia/Jakarta")
    for level, message in ((logging.INFO, "[SCHEDULER] Cycle completed"),
                           (logging.WARNING, "Jadwal ditunda."),
                           (logging.ERROR, "Siklus gagal.")):
        handler.handle(logging.LogRecord("scheduler", level, __file__, 1, message, (), None))
    rendered = output.getvalue()
    assert "\x1b[32mSUCCESS\x1b[0m" in rendered
    assert "\x1b[33mWARNING\x1b[0m" in rendered
    assert "\x1b[31mERROR\x1b[0m" in rendered
