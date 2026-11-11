import psycopg
from psycopg import sql

from app.database import open_db
from app.errors import ApiException


def insert_plant(payload):
    values = payload.model_dump(mode="json")
    try:
        with open_db() as conn:
            conn.execute("""
                INSERT INTO public.plants (
                    plant_code, plant_name, capacity_kwp, address,
                    latitude, longitude, is_demo
                ) VALUES (%s, %s, %s, %s, %s, %s, FALSE)
            """, (payload.plant_code, payload.plant_name, payload.capacity_kwp, payload.address,
                  payload.latitude, payload.longitude))
            conn.execute("""
                INSERT INTO public.plant_metadata (
                    plant_code, country_code, country_name, region, city, time_zone,
                    location_source, location_verified, grid_connected_on, has_pv,
                    capacity_source, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s, TRUE, 'local_registration', NOW())
            """, (payload.plant_code, payload.country_code, payload.country_name, payload.region,
                  payload.city, payload.time_zone, "local" if payload.latitude is not None else None,
                  payload.grid_connected_on))
    except psycopg.errors.UniqueViolation:
        raise ApiException(409, "PLANT_ALREADY_EXISTS", "Kode PLTS sudah terdaftar.",
                           fields=[{"field": "plant_code", "message": "Kode PLTS harus unik."}]) from None
    except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable):
        raise ApiException(503, "SCHEMA_REQUIRED", "Tabel plant_metadata belum tersedia.") from None


def patch_plant(plant_code, payload):
    values = payload.model_dump(mode="json", exclude_unset=True)
    capacity = values.pop("capacity_kwp", None)
    latitude = values.pop("latitude", None) if "latitude" in values else ...
    longitude = values.pop("longitude", None) if "longitude" in values else ...
    coordinates_changed = latitude is not ... or longitude is not ...
    if coordinates_changed and "location_verified" not in values:
        values["location_verified"] = False
    if coordinates_changed:
        values["location_source"] = "local" if latitude is not None else None
    try:
        with open_db() as conn:
            assignments = []
            params = []
            if capacity is not None:
                assignments.append(sql.SQL("capacity_kwp = %s"))
                params.append(capacity)
            if coordinates_changed:
                assignments.extend((sql.SQL("latitude = %s"), sql.SQL("longitude = %s")))
                params.extend((latitude, longitude))
            assignments.append(sql.SQL("updated_at = NOW()"))
            query = sql.SQL("UPDATE public.plants SET {} WHERE plant_code = %s RETURNING plant_code").format(
                sql.SQL(", ").join(assignments)
            )
            row = conn.execute(query, (*params, plant_code)).fetchone()
            if row is None:
                raise ApiException(404, "PLANT_NOT_FOUND", "PLTS tidak ditemukan.")
            if values:
                columns = list(values)
                insert_columns = sql.SQL(", ").join(map(sql.Identifier, ("plant_code", *columns)))
                placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in range(len(columns) + 1))
                updates = sql.SQL(", ").join(
                    sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name))
                    for name in columns
                )
                statement = sql.SQL("""
                    INSERT INTO public.plant_metadata ({}) VALUES ({})
                    ON CONFLICT (plant_code) DO UPDATE SET {}, updated_at = NOW()
                """).format(insert_columns, placeholders, updates)
                conn.execute(statement, (plant_code, *(values[name] for name in columns)))
    except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable):
        raise ApiException(503, "SCHEMA_REQUIRED", "Tabel plant_metadata belum tersedia.") from None
