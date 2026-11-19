"""Synchronize per-device realtime KPI into the normalized telemetry table."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import logging

from app import config
from app.repositories import normalized_repository, plant_repository, raw_repository, sync_repository
from app.services.device_kpi_mapper import device_identifier, map_device_realtime
from app.services.fusionsolar_client import extract_list
from app.sync_runtime import JobInterrupted

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
    expected_by_plant = Counter(device["plant_code"] for device in devices)
    saved_by_plant = Counter()
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
                    per_plant = Counter()
                    requested = {device["plant_code"] for device in batch}
                    for device in batch:
                        item = by_id.get(str(device["vendor_device_id"]))
                        if item is None:
                            continue
                        snapshot = map_device_realtime(item, device, raw_row["collected_at"])
                        if not snapshot["field_quality"]:
                            continue
                        normalized_repository.save_device_realtime(snapshot, raw_row["id"], conn)
                        per_plant[device["plant_code"]] += 1
                    for plant_code in requested:
                        actual = saved_by_plant[plant_code] + per_plant[plant_code]
                        complete = actual == expected_by_plant[plant_code]
                        normalized_repository.upsert_source_state(
                            plant_code, "getDevRealKpi", configured=True,
                            status="success" if complete else "partial",
                            complete=complete, count=actual,
                            attempted_at=collected_at, conn=conn,
                        )
                saved_by_plant.update(per_plant)
                count += sum(per_plant.values())
        sync_repository.finish_job(job_id, True, count)
        return count
    except Exception as exc:
        pending = () if isinstance(exc, JobInterrupted) else {d["plant_code"] for d in devices}
        for plant_code in pending:
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
