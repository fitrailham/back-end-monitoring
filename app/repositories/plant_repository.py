import psycopg
from psycopg.rows import dict_row

from app import config
from app.database import fetch_rows


def open_db():
    return psycopg.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        connect_timeout=10,
        row_factory=dict_row,
    )


def list_plants():
    return fetch_rows("""
        SELECT
            plant_code,
            plant_name,
            capacity_kwp::float8 AS capacity_kwp,
            address,
            is_demo
        FROM public.plants
        ORDER BY plant_name
    """)


def get_plant(plant_code: str):
    rows = fetch_rows("""
        SELECT
            plant_code,
            plant_name,
            capacity_kwp::float8 AS capacity_kwp,
            address,
            is_demo
        FROM public.plants
        WHERE plant_code = %s
    """, (plant_code,))

    return rows[0] if rows else None


def upsert_plant(plant: dict):
    with open_db() as conn:
        conn.execute("""
            INSERT INTO public.plants (
                plant_code,
                plant_name,
                capacity_kwp,
                address,
                is_demo
            )
            VALUES (%s, %s, %s, %s, FALSE)

            ON CONFLICT (plant_code)
            DO UPDATE SET
                plant_name = EXCLUDED.plant_name,
                capacity_kwp = COALESCE(
                    EXCLUDED.capacity_kwp,
                    plants.capacity_kwp
                ),
                address = COALESCE(
                    EXCLUDED.address,
                    plants.address
                ),
                is_demo = FALSE,
                updated_at = NOW()
        """, (
            plant["plant_code"],
            plant["plant_name"],
            plant.get("capacity_kwp"),
            plant.get("address"),
        ))