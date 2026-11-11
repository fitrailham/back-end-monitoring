"""Synchronize per-device realtime KPI into the normalized telemetry table."""
from collections import defaultdict
from datetime import datetime, timezone
import logging

from app import config
from app.repositories import normalized_repository, plant_repository, raw_repository, sync_repository
from app.services.device_kpi_mapper import device_identifier, map_device_realtime
from app.services.fusionsolar_client import extract_list

log = logging.getLogger(__name__)


def run(client) -> int:
    if not config.FUSIONSOLAR_DEVICE_REALTIME_ENABLED:
        log.info("[DEVICE REALTIME] Dilewati: sinkronisasi belum diaktifkan")
        return 0
    devices = [d for d in normalized_repository.latest_devices()
               if str(d.get("vendor_device_type_id") or "") in config.FUSIONSOLAR_DEVICE_REALTIME_TYPE_IDS]
    grouped = defaultdict(list)
    for device in devices:
        grouped[str(device["vendor_device_type_id"])].append(device)
    job_id = sync_repository.start_job("device_realtime_sync")
    count = 0
    try:
        for type_id, members in grouped.items():
            for offset in range(0, len(members), 100):
                batch = members[offset:offset + 100]
                collected_at = datetime.now(timezone.utc)
                payload = client.get_device_kpi(type_id, [d["vendor_device_id"] for d in batch])
                items = extract_list(payload)
                by_id = {device_identifier(item): item for item in items if device_identifier(item)}
                with plant_repository.open_db() as conn:
                    raw_row = raw_repository.save_raw(None, "getDevRealKpi", payload, conn, collected_at=collected_at)
                    per_plant = defaultdict(int)
                    requested = defaultdict(int)
                    for device in batch:
                        requested[device["plant_code"]] += 1
                        item = by_id.get(str(device["vendor_device_id"]))
                        if item is None:
                            continue
                        snapshot = map_device_realtime(item, device, raw_row["collected_at"])
                        normalized_repository.save_device_realtime(snapshot, raw_row["id"], conn)
                        per_plant[device["plant_code"]] += 1
                        count += 1
                    for plant_code, expected in requested.items():
                        actual = per_plant[plant_code]
                        normalized_repository.upsert_source_state(
                            plant_code, "getDevRealKpi", configured=True,
                            status="success" if actual == expected else "partial",
                            complete=actual == expected, count=actual,
                            attempted_at=collected_at, conn=conn,
                        )
        sync_repository.finish_job(job_id, True, count)
        return count
    except Exception as exc:
        for plant_code in {d["plant_code"] for d in devices}:
            try:
                normalized_repository.upsert_source_state(
                    plant_code, "getDevRealKpi", configured=True,
                    status="failed", complete=False, error=str(exc),
                )
            except Exception:
                log.exception("[DEVICE REALTIME] gagal menyimpan source state %s", plant_code)
        sync_repository.finish_job(job_id, False, count, str(exc))
        log.exception("[DEVICE REALTIME] gagal")
        raise
