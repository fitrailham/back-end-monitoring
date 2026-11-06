
from psycopg.types.json import Jsonb

from app.database import fetch_rows
from app.repositories.plant_repository import open_db


METRIC_FIELDS = (
    "pv_power_kw",
    "load_power_kw",
    "grid_power_kw",
    "yield_today_kwh",
    "total_yield_kwh",
    "battery_power_kw",
    "battery_soc_percent",
    "ess_charge_today_kwh",
    "ess_discharge_today_kwh",
)


def save_snapshot(snapshot: dict, conn=None):

    def write(db):
        db.execute("""
            INSERT INTO public.plant_realtime (
                plant_code,
                recorded_at,
                pv_power_kw,
                load_power_kw,
                grid_power_kw,
                yield_today_kwh,
                total_yield_kwh,
                battery_power_kw,
                battery_soc_percent,
                ess_charge_today_kwh,
                ess_discharge_today_kwh,
                data_source,
                raw_data
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )

            ON CONFLICT (plant_code, recorded_at)
            DO UPDATE SET
                pv_power_kw = EXCLUDED.pv_power_kw,
                load_power_kw = EXCLUDED.load_power_kw,
                grid_power_kw = EXCLUDED.grid_power_kw,
                yield_today_kwh = EXCLUDED.yield_today_kwh,
                total_yield_kwh = EXCLUDED.total_yield_kwh,
                battery_power_kw = EXCLUDED.battery_power_kw,
                battery_soc_percent =
                    EXCLUDED.battery_soc_percent,
                ess_charge_today_kwh =
                    EXCLUDED.ess_charge_today_kwh,
                ess_discharge_today_kwh =
                    EXCLUDED.ess_discharge_today_kwh,
                data_source = EXCLUDED.data_source,
                raw_data = EXCLUDED.raw_data
        """, (
            snapshot["plant_code"],
            snapshot["recorded_at"],
            *(snapshot.get(k) for k in METRIC_FIELDS),
            snapshot.get("data_source", "FUSIONSOLAR"),
            Jsonb(snapshot.get("raw_data", {}))
        ))

    if conn is not None:
        write(conn)
    else:
        with open_db() as db:
            write(db)


def latest_per_plant():
    return fetch_rows("""
        SELECT
            p.plant_code,
            p.plant_name,
            p.capacity_kwp::float8 AS capacity_kwp,
            p.is_demo,

            r.recorded_at,
            r.pv_power_kw::float8 AS pv_power_kw,
            r.load_power_kw::float8 AS load_power_kw,
            r.grid_power_kw::float8 AS grid_power_kw,

            r.yield_today_kwh::float8 AS yield_today_kwh,
            r.total_yield_kwh::float8 AS total_yield_kwh,

            r.battery_power_kw::float8 AS battery_power_kw,
            r.battery_soc_percent::float8
                AS battery_soc_percent,

            r.ess_charge_today_kwh::float8
                AS ess_charge_today_kwh,
            r.ess_discharge_today_kwh::float8
                AS ess_discharge_today_kwh,

            r.data_source,
            r.raw_data ->> 'sampling_time_source'
                AS timestamp_source

        FROM public.plants p

        LEFT JOIN LATERAL (
            SELECT *
            FROM public.plant_realtime x
            WHERE x.plant_code = p.plant_code
            ORDER BY x.recorded_at DESC, x.id DESC
            LIMIT 1
        ) r ON TRUE

        ORDER BY p.plant_name
    """)


def history(plant_code: str, limit: int = 100):
    return fetch_rows("""
        SELECT
            plant_code,
            recorded_at,
            pv_power_kw::float8 AS pv_power_kw,
            load_power_kw::float8 AS load_power_kw,
            grid_power_kw::float8 AS grid_power_kw,
            yield_today_kwh::float8 AS yield_today_kwh,
            battery_soc_percent::float8
                AS battery_soc_percent,
            data_source

        FROM public.plant_realtime
        WHERE plant_code = %s
        ORDER BY recorded_at DESC, id DESC
        LIMIT %s
    """, (plant_code, limit))[::-1]
