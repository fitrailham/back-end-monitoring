"""Refresh raw and normalized device inventory in plant batches."""
from datetime import datetime, timezone
import logging

from app import config
from app.repositories import normalized_repository, plant_repository, raw_repository, sync_repository
from app.services.fusionsolar_client import complete_list, extract_list

log = logging.getLogger(__name__)


def run(client):
    codes = [p["plant_code"] for p in plant_repository.list_plants() if not p.get("is_demo")]
    job_id = sync_repository.start_job("inventory_sync")
    count = 0
    try:
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            attempted_at = datetime.now(timezone.utc)
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
                    projected_items = [i for i in items if str(i.get("stationCode")) == code]
                    projected = {**payload, "data": projected_items}
                    raw_row = raw_repository.save_raw(
                        code, "getDevList", projected, conn, collected_at=attempted_at
                    )
                    normalized_repository.upsert_devices(
                        code, projected_items, raw_row["collected_at"], raw_row["id"], True, conn
                    )
                    normalized_repository.upsert_source_state(
                        code, "getDevList", configured=True, status="success",
                        complete=True, count=len(projected_items), attempted_at=attempted_at, conn=conn,
                        expected_interval_seconds=config.INVENTORY_SYNC_INTERVAL_HOURS * 3600,
                        stale_after_seconds=config.INVENTORY_STALE_AFTER_SECONDS,
                    )
            count += len(batch)
        sync_repository.finish_job(job_id, True, count)
        return count
    except Exception as exc:
        for code in codes:
            try:
                normalized_repository.upsert_source_state(
                    code, "getDevList", configured=True, status="failed",
                    complete=False, error=str(exc),
                    expected_interval_seconds=config.INVENTORY_SYNC_INTERVAL_HOURS * 3600,
                    stale_after_seconds=config.INVENTORY_STALE_AFTER_SECONDS,
                )
            except Exception:
                log.exception("[INVENTORY SYNC] Failed to update source state for %s", code)
        sync_repository.finish_job(job_id, False, count, str(exc))
        log.exception("[INVENTORY SYNC] Failed")
        raise
