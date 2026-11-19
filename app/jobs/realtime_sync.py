import logging
from datetime import datetime, timezone

from app.repositories import (
    normalized_repository,
    plant_repository,
    realtime_repository,
    raw_repository,
    sync_repository
)

from app.services.kpi_mapper import NoUsableKpiError, map_realtime
from app.services.sync_diagnostics import failure_details
from app.sync_runtime import JobInterrupted


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
    processed = set()
    unavailable = {}

    try:

        if not codes:
            raise ValueError(
                "Belum ada plant. "
                "Jalankan plant_sync lebih dulu."
            )

        # Batch up to 100 plants to avoid exhausting the Northbound quota.
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            attempted_at = datetime.now(timezone.utc)
            response = client.get_station_kpi(
                ",".join(batch)
            )
            snapshots = {}
            batch_errors = {}
            skipped_raw_ids = {}
            for code in batch:
                try:
                    snapshots[code] = map_realtime(response, code)
                except NoUsableKpiError as exc:
                    batch_errors[code] = exc

            with plant_repository.open_db() as conn:

                for code in batch:
                    raw_row = raw_repository.save_raw(
                        code,
                        "getStationRealKpi",
                        response,
                        conn
                    )
                    if code in batch_errors:
                        skipped_raw_ids[code] = raw_row["id"]
                        normalized_repository.upsert_source_state(
                            code, "getStationRealKpi", configured=True,
                            status="failed", complete=False, count=0,
                            error=str(batch_errors[code]), attempted_at=attempted_at, conn=conn,
                        )
                        continue

                    snapshot = snapshots[code]
                    realtime_repository.save_snapshot(snapshot, conn)
                    normalized_repository.upsert_source_state(
                        snapshot["plant_code"], "getStationRealKpi",
                        configured=True, status="success", complete=True,
                        count=1, attempted_at=snapshot["raw_data"].get("collected_at"), conn=conn,
                    )

            count += len(snapshots)
            processed.update(batch)
            unavailable.update(batch_errors)
            # Report raw evidence only after the transaction has committed.
            for code, error in batch_errors.items():
                log.warning(
                    "Snapshot KPI dilewati.",
                    extra={"event": "realtime.snapshot_skipped", "details": {
                        "job": "realtime_sync", "job_id": job_id,
                        "endpoint": "getStationRealKpi", "plant_code": code,
                        "diagnostic": error.details(),
                        "impact": "Snapshot lama dipertahankan; plant lain tetap diproses.",
                        "evidence": {"table": "public.fusionsolar_raw", "raw_id": skipped_raw_ids[code],
                                     "source_state_table": "public.plant_source_state"},
                    }},
                )

            log.info(
                "Batch realtime tersimpan.",
                extra={"event": "realtime.batch_saved", "details": {
                    "job_id": job_id, "batch": offset // 100 + 1,
                    "saved": len(snapshots), "skipped": len(batch_errors),
                }},
            )

        if unavailable:
            summary = (
                f"{len(unavailable)} dari {len(codes)} plant tanpa KPI valid; "
                f"{count} snapshot tersimpan. " + "; ".join(str(error) for error in unavailable.values())
            )
            sync_repository.finish_job(job_id, False, count, summary)
            # Details are already logged per plant; keep the final summary concise.
            log.warning("Sinkronisasi realtime belum lengkap.",
                extra={"event": "realtime.summary", "details": {
                    "job": "realtime_sync", "job_id": job_id, "status": "partial",
                    "job_status": "FAILED", "total_plants": len(codes), "saved": count,
                    "skipped": len(unavailable), "skipped_plants": list(unavailable),
                    "note": "Detail penyebab tersedia pada event realtime.snapshot_skipped dan sync_logs.",
                }})
        else:
            sync_repository.finish_job(job_id, True, count)
            log.info("Sinkronisasi realtime selesai.",
                extra={"event": "realtime.summary", "details": {
                    "job": "realtime_sync", "job_id": job_id, "status": "success",
                    "job_status": "SUCCESS", "total_plants": len(codes), "saved": count, "skipped": 0,
                }})

        return count

    except Exception as exc:

        pending = () if isinstance(exc, JobInterrupted) else codes
        for code in pending:
            if code in processed:
                continue
            try:
                normalized_repository.upsert_source_state(
                    code, "getStationRealKpi", configured=True,
                    status="failed", complete=False, error=str(exc),
                )
            except Exception:
                log.exception("[REALTIME SYNC] gagal menyimpan source state %s", code)

        sync_repository.finish_job(
            job_id,
            False,
            count,
            str(exc)
        )

        log.exception(
            "Sinkronisasi realtime gagal.",
            extra={"event": "realtime.failed", "details": {
                "job": "realtime_sync", "job_id": job_id, "endpoint": "getStationRealKpi",
                "saved": count, "unprocessed": len(set(codes) - processed),
                "diagnostic": failure_details(exc),
                "impact": "Sinkronisasi dihentikan; hanya batch yang sudah commit yang tersimpan.",
                "evidence": {"table": "public.sync_logs", "job_id": job_id},
            }},
        )

        raise
