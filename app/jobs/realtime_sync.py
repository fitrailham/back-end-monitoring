import logging

from app.repositories import (
    plant_repository,
    realtime_repository,
    raw_repository,
    sync_repository
)

from app.services.kpi_mapper import map_realtime


log = logging.getLogger(__name__)


def run(client, plant_code: str | None = None) -> int:

    if plant_code:
        codes = [plant_code]
    else:
        codes = [
            p["plant_code"]
            for p in plant_repository.list_plants()
            if not p.get("is_demo")
        ]

    job_id = sync_repository.start_job(
        "realtime_sync",
        plant_code
    )

    count = 0

    try:

        if not codes:
            raise ValueError(
                "Belum ada plant. "
                "Jalankan plant_sync lebih dulu."
            )

        # Batch up to 100 plants to avoid exhausting the Northbound quota.
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            response = client.get_station_kpi(
                ",".join(batch)
            )
            snapshots = [map_realtime(response, code) for code in batch]

            with plant_repository.open_db() as conn:

                for snapshot in snapshots:
                    raw_repository.save_raw(
                        snapshot["plant_code"],
                        "getStationRealKpi",
                        response,
                        conn
                    )
                    realtime_repository.save_snapshot(snapshot, conn)

            count += len(snapshots)

            log.info(
                "[REALTIME SYNC] %s plant tersimpan",
                len(snapshots)
            )

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
            "[REALTIME SYNC] gagal"
        )

        raise
