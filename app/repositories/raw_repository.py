from psycopg.types.json import Jsonb

from app.database import fetch_rows
from app.repositories.plant_repository import open_db


def save_raw(
    plant_code: str | None,
    endpoint: str,
    payload: dict,
    conn=None
):

    def write(db):
        db.execute("""
            INSERT INTO public.fusionsolar_raw (
                plant_code,
                api_endpoint,
                raw_data
            )
            VALUES (%s, %s, %s)
        """, (
            plant_code,
            endpoint,
            Jsonb(payload)
        ))

    if conn is not None:
        write(conn)
    else:
        with open_db() as db:
            write(db)


def latest_raw(plant_code: str, endpoint: str):
    rows = fetch_rows("""
        SELECT
            raw_data,
            collected_at
        FROM public.fusionsolar_raw

        WHERE plant_code = %s
          AND api_endpoint = %s

        ORDER BY collected_at DESC, id DESC
        LIMIT 1
    """, (plant_code, endpoint))

    return rows[0] if rows else None