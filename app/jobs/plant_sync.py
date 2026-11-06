import logging

from app.repositories import (
    sync_repository,
    plant_repository,
    raw_repository
)

from app.services.fusionsolar_client import (
    FusionSolarClient,
    extract_list
)

from app.services.kpi_mapper import map_station


log = logging.getLogger(__name__)


def run(client: FusionSolarClient) -> int:

    job_id = sync_repository.start_job(
        "plant_sync"
    )

    count = 0

    try:
        stations = extract_list(
            client.get_stations()
        )

        if not stations:
            raise ValueError(
                "Daftar plant kosong. "
                "Periksa hak akses atau pagination Northbound."
            )

        for item in stations:

            plant = map_station(item)

            plant_repository.upsert_plant(
                plant
            )

            count += 1

        sync_repository.finish_job(
            job_id,
            True,
            count
        )

        log.info(
            "[PLANT SYNC] %s plant tersimpan",
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
            "[PLANT SYNC] gagal"
        )

        raise


def sync_devices(
    client: FusionSolarClient,
    plant_code: str
):

    raw = client.get_devices(
        plant_code
    )

    raw_repository.save_raw(
        plant_code,
        "getDevList",
        raw
    )

    return len(
        extract_list(raw)
    )