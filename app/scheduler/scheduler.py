import logging
import os
import sys
import time

from dotenv import load_dotenv

from app.services.fusionsolar_client import (
    FusionSolarClient
)

from app.jobs import (
    plant_sync,
    realtime_sync,
    alarm_sync,
    inventory_sync
)


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    )
)

log = logging.getLogger(__name__)


def main():

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

    with FusionSolarClient() as client:

        first = True
        next_inventory = 0.0

        while True:

            try:

                if first:

                    log.info(
                        "[SCHEDULER] Syncing plants..."
                    )

                    plant_sync.run(client)

                    first = False

                if time.monotonic() >= next_inventory:
                    try:
                        inventory_sync.run(client)
                    except Exception:
                        log.exception("[SCHEDULER] Inventory unavailable; continuing telemetry cycle")
                    next_inventory = time.monotonic() + 24 * 60 * 60

                log.info(
                    "[SCHEDULER] Syncing realtime KPI..."
                )

                realtime_sync.run(client)

                log.info(
                    "[SCHEDULER] Checking alarms..."
                )

                alarm_sync.run(client)

                log.info(
                    "[SCHEDULER] Cycle completed"
                )

            except Exception:
                log.exception(
                    "[SCHEDULER] Siklus gagal. "
                    "Mencoba kembali pada jadwal berikutnya."
                )

            try:
                log.info(
                    "[SCHEDULER] Next cycle in %s minutes",
                    minutes
                )

                time.sleep(minutes * 60)

            except KeyboardInterrupt:
                log.info(
                    "[SCHEDULER] Dihentikan"
                )
                break


if __name__ == "__main__":
    main()
