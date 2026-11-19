import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from app import config
from app.services.sync_diagnostics import failure_details
from app.scheduler.terminal_logging import configure_scheduler_logging
from app.services.fusionsolar_client import (
    FusionSolarClient, NorthboundRateLimitError
)
from app.scheduler.worker_lock import SchedulerAlreadyRunning, scheduler_lock

from app.jobs import (
    plant_sync,
    realtime_sync,
    alarm_sync,
    inventory_sync,
    device_realtime_sync,
)


load_dotenv()

log = logging.getLogger(__name__)


def main():
    try:
        with scheduler_lock(Path(__file__).resolve().parents[2] / ".scheduler.lock"):
            _run()
    except SchedulerAlreadyRunning:
        log.error(
            "Scheduler proyek ini sudah aktif.",
            extra={"event": "scheduler.duplicate", "details": {
                "diagnostic": {"code": "WORKER_DUPLICATE", "solution": "Gunakan satu instance scheduler per proyek/akun."},
                "impact": "Instance baru dihentikan sebelum login agar sesi/kuota tidak berbenturan.",
            }},
        )


def _run():

    enabled = os.getenv(
        "FUSIONSOLAR_ENABLED",
        "false"
    ).lower()

    if enabled != "true":
        log.warning(
            "Worker disabled. "
            "Set FUSIONSOLAR_ENABLED=true when ready."
        )
        return

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

        while True:

            stage = "plant_sync"
            wait_seconds = minutes * 60
            try:

                if first:

                    log.info(
                        "[SCHEDULER] Syncing plants..."
                    )

                    plant_sync.run(client)

                    first = False

                if time.monotonic() >= next_inventory:
                    stage = "inventory_sync"
                    try:
                        inventory_sync.run(client)
                    except NorthboundRateLimitError:
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
                realtime_sync.run(client)

                stage = "device_realtime_sync"
                device_realtime_sync.run(client)

                log.info(
                    "[SCHEDULER] Checking alarms..."
                )

                stage = "alarm_sync"
                alarm_sync.run(client)

                log.info(
                    "[SCHEDULER] Cycle completed"
                )

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

            try:
                log.info(
                    "[SCHEDULER] Next cycle in %s minutes",
                    wait_seconds / 60
                )

                time.sleep(wait_seconds)

            except KeyboardInterrupt:
                log.info(
                    "[SCHEDULER] Dihentikan"
                )
                break


if __name__ == "__main__":
    configure_scheduler_logging()
    main()
