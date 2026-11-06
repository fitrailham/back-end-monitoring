from app.database import fetch_rows
from app.repositories.plant_repository import open_db


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

    with open_db() as conn:
        conn.execute("""
            UPDATE public.sync_logs
            SET
                finished_at = NOW(),
                status = %s,
                records_inserted = %s,
                error_message = %s
            WHERE id = %s
        """, (
            "SUCCESS" if success else "FAILED",
            count,
            error[:1000] or None,
            job_id
        ))


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