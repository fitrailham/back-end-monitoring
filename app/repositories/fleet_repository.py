"""Bulk reads keep metadata, telemetry and inventory in one database snapshot."""
from app import config
from app.database import fetch_rows


def fleet(plant_code: str | None = None):
    return fetch_rows("""
        SELECT p.plant_code, p.plant_name, p.capacity_kwp::float8 AS capacity_kwp,
            p.address, p.is_demo, to_jsonb(p)->'metadata' AS metadata,
            r.recorded_at, r.pv_power_kw::float8 AS pv_power_kw,
            r.load_power_kw::float8 AS load_power_kw,
            r.grid_power_kw::float8 AS grid_power_kw,
            r.yield_today_kwh::float8 AS yield_today_kwh,
            r.total_yield_kwh::float8 AS total_yield_kwh,
            r.battery_power_kw::float8 AS battery_power_kw,
            r.battery_soc_percent::float8 AS battery_soc_percent,
            r.ess_charge_today_kwh::float8 AS ess_charge_today_kwh,
            r.ess_discharge_today_kwh::float8 AS ess_discharge_today_kwh,
            r.data_source, r.raw_data AS snapshot_raw,
            d.raw_data AS inventory_payload, d.collected_at AS inventory_collected_at,
            a.raw_data AS alarms_payload, a.collected_at AS alarms_collected_at,
            j.status AS alarm_job_status, j.started_at AS alarm_job_started_at
        FROM public.plants p
        LEFT JOIN LATERAL (
            SELECT x.* FROM public.plant_realtime x
            WHERE x.plant_code = p.plant_code
            ORDER BY x.recorded_at DESC, x.id DESC LIMIT 1
        ) r ON TRUE
        LEFT JOIN LATERAL (
            SELECT x.raw_data, x.collected_at FROM public.fusionsolar_raw x
            WHERE x.plant_code = p.plant_code AND x.api_endpoint = 'getDevList'
            ORDER BY x.collected_at DESC, x.id DESC LIMIT 1
        ) d ON TRUE
        LEFT JOIN LATERAL (
            SELECT x.raw_data, x.collected_at FROM public.fusionsolar_raw x
            WHERE x.plant_code = p.plant_code AND x.api_endpoint = %s
            ORDER BY x.collected_at DESC, x.id DESC LIMIT 1
        ) a ON TRUE
        LEFT JOIN LATERAL (
            SELECT x.status, x.started_at FROM public.sync_logs x
            WHERE x.job_name = 'alarm_sync'
              AND (x.plant_code IS NULL OR x.plant_code = p.plant_code)
            ORDER BY x.id DESC LIMIT 1
        ) j ON TRUE
        WHERE (%s::text IS NULL OR p.plant_code = %s)
        ORDER BY p.plant_name, p.plant_code
    """, (config.FUSIONSOLAR_ALARMS_PATH, plant_code, plant_code))


def counter_samples(codes: list[str], start, end):
    """No arbitrary row limit. Include one baseline before the requested range."""
    if not codes:
        return []
    return fetch_rows("""
        WITH samples AS (
            SELECT r.plant_code, r.recorded_at, r.total_yield_kwh::float8 AS total_yield_kwh,
                r.yield_today_kwh::float8 AS yield_today_kwh, r.data_source,
                r.raw_data->>'sampling_time_source' AS timestamp_source,
                r.id
            FROM public.plant_realtime r
            WHERE r.plant_code = ANY(%s) AND r.recorded_at >= %s AND r.recorded_at <= %s
        ), baseline AS (
            SELECT b.* FROM unnest(%s::text[]) AS c(plant_code)
            CROSS JOIN LATERAL (
                SELECT r.plant_code, r.recorded_at,
                    r.total_yield_kwh::float8 AS total_yield_kwh,
                    r.yield_today_kwh::float8 AS yield_today_kwh, r.data_source,
                    r.raw_data->>'sampling_time_source' AS timestamp_source, r.id
                FROM public.plant_realtime r
                WHERE r.plant_code = c.plant_code AND r.recorded_at < %s
                ORDER BY r.recorded_at DESC, r.id DESC LIMIT 1
            ) b
        )
        SELECT * FROM (SELECT * FROM baseline UNION ALL SELECT * FROM samples) all_samples
        ORDER BY plant_code, recorded_at, id
    """, (codes, start, end, codes, start))
