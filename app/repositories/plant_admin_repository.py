import psycopg
from psycopg.types.json import Jsonb

from app.database import open_db
from app.errors import ApiException


def insert_plant(payload):
    values = payload.model_dump(mode="json")
    metadata = {k: v for k, v in values.items() if k not in ("plant_code", "plant_name", "capacity_kwp", "address")}
    metadata.update(has_pv=True, location_verified=False, registration_source="local")
    try:
        with open_db() as conn:
            conn.execute("""
                INSERT INTO public.plants (plant_code, plant_name, capacity_kwp, address, is_demo, metadata)
                VALUES (%s, %s, %s, %s, FALSE, %s)
            """, (payload.plant_code, payload.plant_name, payload.capacity_kwp, payload.address, Jsonb(metadata)))
    except psycopg.errors.UniqueViolation:
        raise ApiException(409, "PLANT_ALREADY_EXISTS", "Kode PLTS sudah terdaftar.",
                           fields=[{"field": "plant_code", "message": "Kode PLTS harus unik."}]) from None
    except psycopg.errors.UndefinedColumn:
        raise ApiException(503, "MIGRATION_REQUIRED", "Jalankan migrasi metadata sebelum operasi tulis.") from None


def patch_plant(plant_code, payload):
    values = payload.model_dump(mode="json", exclude_unset=True)
    capacity = values.pop("capacity_kwp", None)
    if "latitude" in values and "location_verified" not in values:
        values["location_verified"] = False
    try:
        with open_db() as conn:
            row = conn.execute("""
                UPDATE public.plants SET metadata = COALESCE(metadata, '{}'::jsonb) || %s,
                    capacity_kwp = COALESCE(%s, capacity_kwp), updated_at = NOW()
                WHERE plant_code = %s RETURNING plant_code
            """, (Jsonb(values), capacity, plant_code)).fetchone()
            if row is None:
                raise ApiException(404, "PLANT_NOT_FOUND", "PLTS tidak ditemukan.")
    except psycopg.errors.UndefinedColumn:
        raise ApiException(503, "MIGRATION_REQUIRED", "Jalankan migrasi metadata sebelum operasi tulis.") from None
