import logging
import os
import signal
import sys
import time
from pathlib import Path
from threading import Event

import psycopg
from dotenv import load_dotenv

from app import config
from app.repositories import sync_repository
from app.services.sync_diagnostics import failure_details
from app.scheduler.terminal_logging import configure_scheduler_logging
from app.services.fusionsolar_client import (
    FusionSolarClient, NorthboundRateLimitError
)
from app.scheduler.worker_lock import SchedulerAlreadyRunning, postgres_scheduler_lock, scheduler_lock
from app.sync_runtime import ShutdownRequested, WorkerConnectionLost, job_budget

from app.jobs import (
    plant_sync,
    realtime_sync,
    alarm_sync,
    inventory_sync,
    device_realtime_sync,
)


load_dotenv()

log = logging.getLogger(__name__)


def _enabled():
    return os.getenv("FUSIONSOLAR_ENABLED", "false").lower() == "true"


def _timeout(name, default):
    seconds = int(os.getenv(name, default))
    if seconds <= 0:
        raise ValueError(f"{name} harus lebih dari nol.")
    return seconds


def main(stop=None):
    stop = stop if stop is not None else Event()
    try:
        with scheduler_lock(Path(__file__).resolve().parents[2] / ".scheduler.lock"):
            if not _enabled():
                _run(stop)
                return
            job_timeout = _timeout("SCHEDULER_JOB_TIMEOUT_SECONDS", "600")
            statement_timeout = _timeout("SCHEDULER_DB_TIMEOUT_SECONDS", "30")
            while not stop.is_set():
                try:
                    with postgres_scheduler_lock(statement_timeout):
                        _run(stop, before_cycle=lambda: sync_repository.recover_interrupted_jobs(job_timeout))
                    return
                except (WorkerConnectionLost, psycopg.OperationalError) as exc:
                    log.exception("Sesi PostgreSQL scheduler gagal.",
                                  extra={"event": "scheduler.database_unavailable", "details": {
                                      "diagnostic": failure_details(exc),
                                      "retry_after_seconds": config.EXPECTED_INTERVAL_SECONDS,
                                  }})
                    stop.wait(config.EXPECTED_INTERVAL_SECONDS)
    except SchedulerAlreadyRunning:
        log.error(
            "Scheduler proyek ini sudah aktif.",
            extra={"event": "scheduler.duplicate", "details": {
                "diagnostic": {"code": "WORKER_DUPLICATE", "solution": "Gunakan satu instance scheduler per proyek/akun."},
                "impact": "Instance baru dihentikan sebelum login agar sesi/kuota tidak berbenturan.",
            }},
        )


def _run(stop=None, *, before_cycle=None):

    if not _enabled():
        log.warning(
            "Worker disabled. "
            "Set FUSIONSOLAR_ENABLED=true when ready."
        )
        return
    stop = stop if stop is not None else Event()
    job_timeout = _timeout("SCHEDULER_JOB_TIMEOUT_SECONDS", "600")

    minutes = max(
        10,
        int(
            os.getenv(
                "SYNC_INTERVAL_MINUTES",
                "10"
            )
        )
    )

    log.info(
        "[SCHEDULER] Starting every %s minutes",
        minutes
    )
    log.info(
        "[SCHEDULER] Runtime: pid=%s python=%s realtime_module=%s",
        os.getpid(), sys.executable, realtime_sync.__file__,
    )

    with FusionSolarClient() as client:

        first = True
        next_inventory = 0.0

        while not stop.is_set():
            if before_cycle is not None:
                before_cycle()

            stage = "plant_sync"
            wait_seconds = minutes * 60
            try:

                if first:

                    log.info(
                        "[SCHEDULER] Syncing plants..."
                    )

                    with job_budget(stop, job_timeout):
                        plant_sync.run(client)

                    first = False

                if time.monotonic() >= next_inventory:
                    stage = "inventory_sync"
                    try:
                        with job_budget(stop, job_timeout):
                            inventory_sync.run(client)
                    except (NorthboundRateLimitError, ShutdownRequested, WorkerConnectionLost):
                        # Keep next_inventory due so it can retry after the cooldown, not after 24 hours.
                        raise
                    except Exception as exc:
                        log.exception(
                            "Inventory gagal; siklus telemetry dilanjutkan.",
                            extra={"event": "scheduler.inventory_unavailable", "details": {
                                "job": stage, "diagnostic": failure_details(exc),
                            }},
                        )
                    next_inventory = time.monotonic() + config.INVENTORY_SYNC_INTERVAL_HOURS * 60 * 60

                log.info(
                    "[SCHEDULER] Syncing realtime KPI..."
                )

                stage = "realtime_sync"
                with job_budget(stop, job_timeout):
                    realtime_sync.run(client)

                stage = "device_realtime_sync"
                with job_budget(stop, job_timeout):
                    device_realtime_sync.run(client)

                log.info(
                    "[SCHEDULER] Checking alarms..."
                )

                stage = "alarm_sync"
                with job_budget(stop, job_timeout):
                    alarm_sync.run(client)

                log.info(
                    "[SCHEDULER] Cycle completed"
                )

            except ShutdownRequested:
                break
            except WorkerConnectionLost:
                raise
            except NorthboundRateLimitError as exc:
                wait_seconds = max(wait_seconds, exc.retry_after_seconds)
                log.warning(
                    "Siklus ditunda karena kuota Northbound.",
                    extra={"event": "scheduler.quota_wait", "details": {
                        "job": stage, "endpoint": exc.endpoint, "fail_code": 407,
                        "wait_seconds": wait_seconds, "diagnostic": failure_details(exc),
                        "impact": "Request berikutnya pada siklus ini dilewati; tidak login ulang.",
                    }},
                )
            except Exception as exc:
                log.exception(
                    "Siklus scheduler gagal.",
                    extra={"event": "scheduler.failed", "details": {
                        "job": stage, "diagnostic": failure_details(exc), "retry_after_seconds": wait_seconds,
                        "impact": "Job sesudah tahap ini dilewati pada siklus ini; dicoba pada jadwal berikutnya.",
                    }},
                )

            if stop.is_set():
                break
            log.info("[SCHEDULER] Next cycle in %s minutes", wait_seconds / 60)
            if stop.wait(wait_seconds):
                break

    log.info("[SCHEDULER] Dihentikan")


def run_cli():
    configure_scheduler_logging()
    stop = Event()

    def request_shutdown(_signum, _frame):
        stop.set()

    signals = (signal.SIGINT, signal.SIGTERM)
    if hasattr(signal, "SIGBREAK"):
        signals += (signal.SIGBREAK,)
    handlers = {sig: signal.signal(sig, request_shutdown) for sig in signals}
    try:
        main(stop)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    run_cli()
