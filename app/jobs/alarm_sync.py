import os
import logging

from app.repositories import (
    plant_repository,
    raw_repository,
    sync_repository
)


log = logging.getLogger(__name__)


def run(client) -> int:

    path = os.getenv(
        "FUSIONSOLAR_ALARMS_PATH", ""
    ).strip()

    if not path:
        log.info(
            "[ALARM SYNC] Dilewati: "
            "endpoint alarm belum dikonfirmasi"
        )
        return 0

    job_id = sync_repository.start_job(
        "alarm_sync"
    )

    count = 0

    try:

        for plant in plant_repository.list_plants():

            raw = client.get_alarms(
                plant["plant_code"]
            )

            raw_repository.save_raw(
                plant["plant_code"],
                path,
                raw
            )

            count += 1

        sync_repository.finish_job(
            job_id,
            True,
            count
        )

        return count

    except Exception as exc:

        sync_repository.finish_job(
            job_id,
            False,
            count,
            str(exc)
        )

        log.exception(
            "[ALARM SYNC] gagal"
        )

        raise