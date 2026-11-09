"""Refresh device inventory in plant batches; telemetry freshness is independent."""
import logging

from app.repositories import plant_repository, raw_repository, sync_repository
from app.services.fusionsolar_client import complete_list, extract_list

log = logging.getLogger(__name__)


def run(client):
    codes = [p["plant_code"] for p in plant_repository.list_plants() if not p.get("is_demo")]
    job_id = sync_repository.start_job("inventory_sync")
    count = 0
    try:
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            payload = client.get_devices(",".join(batch))
            items = extract_list(payload)
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("total"), (int, float)) and data["total"] > len(items):
                raise ValueError("Inventory paginated: complete collection is required before replacing cache")
            if not complete_list(payload):
                raise ValueError("Inventory missing or malformed; previous cache must be preserved")
            if any(not item.get("stationCode") for item in items):
                raise ValueError("Inventory device has no plant association")
            with plant_repository.open_db() as conn:
                for code in batch:
                    projected = {**payload, "data": [i for i in items if str(i.get("stationCode")) == code]}
                    raw_repository.save_raw(code, "getDevList", projected, conn)
            count += len(batch)
        sync_repository.finish_job(job_id, True, count)
        return count
    except Exception as exc:
        sync_repository.finish_job(job_id, False, count, str(exc))
        log.exception("[INVENTORY SYNC] Failed")
        raise
