import os
import logging
from datetime import datetime, timedelta, timezone

from app import config
from app.repositories import (
    normalized_repository,
    plant_repository,
    raw_repository,
    sync_repository
)
from app.services.alarm_service import normalize_alarm
from app.services.fusionsolar_client import complete_list, extract_list
from app.sync_runtime import JobInterrupted


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
        for plant in plant_repository.list_plants():
            normalized_repository.upsert_source_state(
                plant["plant_code"], "getAlarmList", configured=False,
                status="unknown", complete=False,
            )
        return 0

    job_id = sync_repository.start_job(
        "alarm_sync"
    )

    count = 0

    try:

        for plant in plant_repository.list_plants():
            attempted_at = datetime.now(timezone.utc)
            begin_at = attempted_at - timedelta(hours=config.FUSIONSOLAR_ALARM_LOOKBACK_HOURS)

            raw = client.get_alarms(
                plant["plant_code"], begin_at, attempted_at
            )
            if not complete_list(raw):
                raise ValueError(f"Daftar alarm {plant['plant_code']} tidak lengkap atau malformed")
            items = extract_list(raw)
            normalized = []
            for item in items:
                alarm = normalize_alarm(item, plant["plant_code"])
                alarm["_raw"] = item
                normalized.append(alarm)

            with plant_repository.open_db() as conn:
                raw_row = raw_repository.save_raw(
                    plant["plant_code"], path, raw, conn, collected_at=attempted_at
                )
                normalized_repository.upsert_alarms(
                    plant["plant_code"], normalized, raw_row["collected_at"], raw_row["id"], conn
                )
                normalized_repository.upsert_source_state(
                    plant["plant_code"], path, configured=True, status="success",
                    complete=True, count=len(items), attempted_at=attempted_at, conn=conn,
                )

            count += len(items)

        sync_repository.finish_job(
            job_id,
            True,
            count
        )

        return count

    except Exception as exc:

        failed_code = plant.get("plant_code") if "plant" in locals() else None
        if failed_code and not isinstance(exc, JobInterrupted):
            try:
                normalized_repository.upsert_source_state(
                    failed_code, path, configured=True, status="failed",
                    complete=False, error=str(exc),
                )
            except Exception:
                log.exception("[ALARM SYNC] gagal menyimpan source state")

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
