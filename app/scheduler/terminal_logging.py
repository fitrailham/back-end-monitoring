"""Rich terminal output and JSON Lines audit for the FusionSolar scheduler."""
from datetime import datetime
import logging
import re
import sys
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.text import Text

from app import config
from app.logging_config import JsonLogFormatter, log_settings
from app.services.sync_diagnostics import failure_details


AUDIT_PATH = config.PROJECT_ROOT / "logs" / "scheduler.audit.jsonl"
JOBS = {
    "plant_sync": ("PLANT", "getStationList"),
    "inventory_sync": ("INVENTORY", "getDevList"),
    "realtime_sync": ("REALTIME", "getStationRealKpi"),
    "device_realtime_sync": ("DEVICE_REALTIME", "getDevRealKpi"),
    "alarm_sync": ("ALARM", "getAlarmList"),
}
LEGACY_PREFIXES = {
    "SCHEDULER": "SCHEDULER",
    "PLANT SYNC": "PLANT",
    "REALTIME SYNC": "REALTIME",
    "INVENTORY SYNC": "INVENTORY",
    "DEVICE REALTIME": "DEVICE_REALTIME",
    "ALARM SYNC": "ALARM",
}
STYLES = {"SUCCESS": "green", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "red"}


def _single_line(value):
    return " ".join(str(value).split())


def _diagnostic(record, event, details, message):
    if record.exc_info and record.exc_info[1] is not None:
        diagnostic = failure_details(record.exc_info[1])
    elif event == "realtime.summary" and details.get("status") == "partial":
        diagnostic = {
            "code": "KPI_PARTIAL",
            "reason": "KPI valid belum tersedia untuk sebagian plant.",
            "solution": "Lihat warning per plant atau log audit.",
        }
    elif event == "scheduler.duplicate":
        diagnostic = {"reason": "Worker lain memegang kunci scheduler."}
    elif message.startswith("Worker disabled."):
        diagnostic = {
            "code": "WORKER_DISABLED",
            "reason": "FUSIONSOLAR_ENABLED belum aktif.",
            "solution": "Set FUSIONSOLAR_ENABLED=true jika scheduler siap dijalankan.",
        }
    else:
        diagnostic = {}
    supplied = details.get("diagnostic")
    if isinstance(supplied, dict):
        diagnostic.update({key: value for key, value in supplied.items()
                           if key in ("code", "reason", "solution") and value})
    return diagnostic


class SmartLogHandler(logging.Handler):
    def __init__(self, console, time_zone):
        super().__init__()
        self.console = console
        self.time_zone = ZoneInfo(time_zone)

    def render(self, record):
        event = getattr(record, "event", "")
        details = getattr(record, "details", {})
        if not isinstance(details, dict):
            details = {}
        logger = record.name.rsplit(".", 1)[-1]
        job = details.get("job") or (logger if logger in JOBS else None)
        component, endpoint = JOBS.get(job, (logger.upper(), None))
        if logger in ("__main__", "scheduler"):
            component = "SCHEDULER"
        message = _single_line(record.getMessage())
        prefix = re.match(r"^\[([^\]]+)\]\s*", message)
        if prefix and prefix[1] in LEGACY_PREFIXES:
            component = LEGACY_PREFIXES[prefix[1]]
            message = message[prefix.end():]
        level = record.levelname
        if record.levelno == logging.INFO and (
            (event == "realtime.batch_saved" and not details.get("skipped")) or details.get("status") == "success"
            or (component == "SCHEDULER" and message == "Cycle completed")
            or (component == "PLANT" and message.endswith(" plant tersimpan"))
        ):
            level = "SUCCESS"
        if component == "SCHEDULER" and message.startswith("Runtime: pid="):
            message = message.partition(" python=")[0]
        if event in {"realtime.batch_saved", "realtime.summary"}:
            counts = ("batch", "saved", "skipped")
            summary = " ".join(f"{key}={details[key]}" for key in counts if key in details)
            if summary:
                message += " " + summary

        if record.levelno >= logging.WARNING:
            diagnostic = _diagnostic(record, event, details, message)
            if diagnostic.get("code") == "WORKER_DISABLED":
                message = "Scheduler nonaktif."
            exception = record.exc_info[1] if record.exc_info else None
            endpoint = details.get("endpoint") or getattr(exception, "endpoint", None) or endpoint
            plant = details.get("plant_code") or getattr(exception, "plant_code", None)
            skipped_plants = details.get("skipped_plants")
            if not plant and skipped_plants:
                plant = ", ".join(map(str, skipped_plants[:3]))
                if len(skipped_plants) > 3:
                    plant += f" (+{len(skipped_plants) - 3})"
            code = diagnostic.get("code") or ("SYNC_ERROR" if record.levelno >= logging.ERROR else "SYNC_WARNING")
            if details.get("fail_code") is not None:
                code = f"{code}/{details['fail_code']}"
            context = []
            if record.levelno >= logging.ERROR:
                context.append(f"lokasi={job or f'{record.module}:{record.lineno}'}")
            context.extend((f"plant={plant or '-'}", f"endpoint={endpoint or '-'}", f"kode={code}"))
            reason = diagnostic.get("reason") or "Lihat detail event di log audit."
            action = diagnostic.get("solution") or "Periksa log audit sebelum mencoba lagi."
            action_label = "solusi" if record.levelno >= logging.ERROR else "tindakan"
            sections = [message, " ".join(context), f"penyebab={reason}", f"{action_label}={action}"]
            if details.get("impact") and event == "realtime.snapshot_skipped":
                sections.append(details["impact"])
            message = " | ".join(_single_line(section) for section in sections)

        timestamp = datetime.fromtimestamp(record.created, self.time_zone).strftime("%H:%M:%S")
        text = Text(f"[{timestamp}] ")
        text.append(level, style=STYLES.get(level))
        text.append(f" {component} {message}")
        return text

    def emit(self, record):
        try:
            self.console.print(self.render(record), soft_wrap=True)
        except Exception:
            self.handleError(record)


def configure_scheduler_logging():
    level, time_zone = log_settings()
    root = logging.getLogger()
    handlers = {handler.get_name(): handler for handler in root.handlers}
    audit = handlers.get("icongreen_scheduler_audit")
    if audit is None:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        audit = logging.FileHandler(AUDIT_PATH, encoding="utf-8")
        audit.set_name("icongreen_scheduler_audit")
        root.addHandler(audit)
    audit.setFormatter(JsonLogFormatter(pretty=False, time_zone=time_zone))
    audit.setLevel(level)

    terminal = handlers.get("icongreen_scheduler_terminal")
    if terminal is not None:
        root.removeHandler(terminal)
        terminal.close()
    console = Console(file=sys.stdout, force_terminal=None if sys.stdout.isatty() else False,
                      highlight=False, markup=False, emoji=False)
    terminal = SmartLogHandler(console, time_zone)
    terminal.set_name("icongreen_scheduler_terminal")
    terminal.setLevel(level)
    root.addHandler(terminal)
    if legacy := handlers.get("icongreen_json"):
        root.removeHandler(legacy)
        legacy.close()
    root.setLevel(level)
