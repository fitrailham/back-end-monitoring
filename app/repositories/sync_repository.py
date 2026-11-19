from datetime import timedelta

from app import config
from app.database import fetch_rows
from app.repositories.plant_repository import open_db
from app.sync_runtime import allow_job_audit


SCHEDULER_JOBS = ("plant_sync", "inventory_sync", "realtime_sync", "device_realtime_sync", "alarm_sync")


def start_job(
    job_name: str,
    plant_code: str | None = None
) -> int:

    with open_db() as conn:
        row = conn.execute("""
            INSERT INTO public.sync_logs (
                job_name,
                plant_code,
                status
            )
            VALUES (%s, %s, 'RUNNING')
            RETURNING id
        """, (job_name, plant_code)).fetchone()

        return row["id"]


def finish_job(
    job_id: int,
    success: bool,
    count: int = 0,
    error: str = ""
):

    with allow_job_audit(), open_db() as conn:
        conn.execute("""
            UPDATE public.sync_logs
            SET
                finished_at = NOW(),
                status = %s,
                records_inserted = %s,
                error_message = %s
            WHERE id = %s AND status = 'RUNNING'
        """, (
            "SUCCESS" if success else "FAILED",
            count,
            error[:1000] or None,
            job_id
        ))


def recover_interrupted_jobs(job_timeout_seconds):
    with open_db() as conn:
        return conn.execute("""
            UPDATE public.sync_logs
            SET finished_at = NOW(), status = 'FAILED',
                error_message = 'WORKER_INTERRUPTED: Job tidak selesai sebelum worker sebelumnya berhenti.'
            WHERE status = 'RUNNING' AND job_name = ANY(%s)
              AND started_at < NOW() - %s
        """, (list(SCHEDULER_JOBS), timedelta(seconds=job_timeout_seconds))).rowcount


def recent_jobs(limit: int = 20):
    return fetch_rows("""
        SELECT
            id,
            job_name,
            plant_code,
            status,
            started_at,
            finished_at,
            records_inserted,
            error_message
        FROM public.sync_logs
        ORDER BY id DESC
        LIMIT %s
    """, (limit,))


def source_freshness():
    stale_after = config.STALE_AFTER_MINUTES * 60
    return fetch_rows("""
        WITH sources (api_endpoint, configured, stale_after_seconds) AS (
            VALUES ('getStationRealKpi', TRUE, %s),
                   ('getDevList', TRUE, %s),
                   ('getDevRealKpi', %s, %s),
                   (%s, %s, %s)
        ), expected AS (
            SELECT p.plant_code, src.api_endpoint,
                state.last_attempt_at, state.last_success_at,
                CASE
                    WHEN NOT src.configured THEN 'unconfigured'
                    WHEN state.last_result_status = 'failed' THEN 'failed'
                    WHEN state.last_success_at IS NULL THEN 'missing'
                    WHEN state.last_success_at < NOW() - make_interval(secs =>
                         COALESCE(state.stale_after_seconds, src.stale_after_seconds)) THEN 'stale'
                    WHEN state.last_result_complete IS NOT TRUE THEN 'partial'
                    ELSE 'fresh'
                END AS freshness
            FROM sources src CROSS JOIN public.plants p
            LEFT JOIN public.plant_source_state state
                ON state.plant_code = p.plant_code AND state.api_endpoint = src.api_endpoint
               AND state.source_server = %s
            WHERE p.is_demo IS NOT TRUE AND (
                src.api_endpoint <> 'getDevRealKpi' OR EXISTS (
                    SELECT 1 FROM public.devices d
                    WHERE d.plant_code = p.plant_code AND d.source_server = %s
                      AND d.vendor_device_type_id = ANY(%s)
                      AND d.inventory_collected_at = (
                          SELECT MAX(x.inventory_collected_at) FROM public.devices x
                          WHERE x.plant_code = d.plant_code AND x.source_server = d.source_server
                      )
                )
            )
        )
        SELECT src.api_endpoint, src.configured, COUNT(e.plant_code) AS expected_plants,
               COUNT(*) FILTER (WHERE freshness = 'fresh') AS fresh,
               COUNT(*) FILTER (WHERE freshness = 'stale') AS stale,
               COUNT(*) FILTER (WHERE freshness = 'partial') AS partial,
               COUNT(*) FILTER (WHERE freshness = 'failed') AS failed,
               COUNT(*) FILTER (WHERE freshness = 'missing') AS missing,
               COUNT(*) FILTER (WHERE freshness = 'unconfigured') AS unconfigured,
               MAX(e.last_attempt_at) AS last_attempt_at, MAX(e.last_success_at) AS last_success_at,
               NOW() AS checked_at
        FROM sources src LEFT JOIN expected e ON e.api_endpoint = src.api_endpoint
        GROUP BY src.api_endpoint, src.configured ORDER BY src.api_endpoint
    """, (
        stale_after, config.INVENTORY_STALE_AFTER_SECONDS,
        config.FUSIONSOLAR_DEVICE_REALTIME_ENABLED, stale_after,
        config.FUSIONSOLAR_ALARMS_PATH or "getAlarmList", bool(config.FUSIONSOLAR_ALARMS_PATH), stale_after,
        config.FUSIONSOLAR_SOURCE_SERVER, config.FUSIONSOLAR_SOURCE_SERVER,
        list(config.FUSIONSOLAR_DEVICE_REALTIME_TYPE_IDS),
    ))
