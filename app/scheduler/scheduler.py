import logging
import os
import time

from dotenv import load_dotenv

from app.services.fusionsolar_client import (
    FusionSolarClient
)

from app.jobs import (
    plant_sync,
    realtime_sync,
    alarm_sync
)


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
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
        15,
        int(
            os.getenv(
                "SYNC_INTERVAL_MINUTES",
                "15"
            )
        )
    )

    log.info(
        "[SCHEDULER] Starting every %s minutes",
        minutes
    )

    with FusionSolarClient() as client:

        first = True

        while True:

            try:

                if first:

                    log.info(
                        "[SCHEDULER] Syncing plants..."
                    )

                    plant_sync.run(client)

                    first = False

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
                time.sleep(minutes * 60)

            except KeyboardInterrupt:
                log.info(
                    "[SCHEDULER] Dihentikan"
                )
                break


if __name__ == "__main__":
    main()