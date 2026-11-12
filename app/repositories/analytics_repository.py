"""Range reads for FE analytics. These queries never contact Northbound."""
from app import config
from app.database import fetch_rows

MAX_SOURCE_ROWS = 200_000


def plant_power_samples(plant_code, start, end):
    return fetch_rows("""
        SELECT id, plant_code, recorded_at, pv_power_kw::float8, load_power_kw::float8,
               grid_power_kw::float8, battery_power_kw::float8, data_source,
               raw_data->>'sampling_time_source' AS timestamp_source,
               raw_data->>'collected_at' AS collected_at,
               raw_data->>'power_direction_convention' AS power_direction_convention
        FROM public.plant_realtime
        WHERE plant_code = %s AND recorded_at >= %s AND recorded_at < %s
        ORDER BY recorded_at, id LIMIT %s
    """, (plant_code, start, end, MAX_SOURCE_ROWS + 1))


def inverters(plant_codes):
    if not plant_codes:
        return []
    return fetch_rows("""
        SELECT d.id, d.id::text AS device_key, d.vendor_device_id AS device_id,
               d.device_name, d.device_sn, d.plant_code, d.model, d.software_version,
               d.rated_ac_power_kw::float8, d.inventory_collected_at, d.inventory_complete
        FROM public.devices d
        WHERE d.plant_code = ANY(%s) AND d.device_type = 'inverter' AND d.source_server = %s
        ORDER BY d.plant_code, d.id
    """, (plant_codes, config.FUSIONSOLAR_SOURCE_SERVER))


def device(plant_code, vendor_device_id):
    rows = fetch_rows("""
        SELECT id, id::text AS device_key, vendor_device_id AS device_id,
               device_name, device_sn, plant_code, device_type, software_version
        FROM public.devices
        WHERE plant_code = %s AND vendor_device_id = %s AND source_server = %s
    """, (plant_code, vendor_device_id, config.FUSIONSOLAR_SOURCE_SERVER))
    return rows[0] if rows else None


def device_counter_samples(device_ids, start, end):
    if not device_ids:
        return []
    # Include a baseline before from and a sample exactly at to for counter differences.
    return fetch_rows("""
        WITH samples AS (
            SELECT id, device_id, measured_at AS recorded_at, total_yield_kwh::float8,
                   measurement_time_verified, field_quality
            FROM public.device_realtime
            WHERE device_id = ANY(%s) AND measured_at >= %s AND measured_at <= %s
        ), baselines AS (
            SELECT b.* FROM unnest(%s::bigint[]) AS d(device_id)
            CROSS JOIN LATERAL (
                SELECT id, device_id, measured_at AS recorded_at, total_yield_kwh::float8,
                       measurement_time_verified, field_quality
                FROM public.device_realtime
                WHERE device_id = d.device_id AND measured_at < %s
                ORDER BY measured_at DESC, collected_at DESC, id DESC LIMIT 1
            ) b
        )
        SELECT * FROM (SELECT * FROM baselines UNION ALL SELECT * FROM samples) s
        ORDER BY device_id, recorded_at, id LIMIT %s
    """, (device_ids, start, end, device_ids, start, MAX_SOURCE_ROWS + 1))


def device_history(device_id, start, end, page, page_size):
    params = (device_id, start, end)
    rows = fetch_rows("""
        SELECT id::text AS sample_id, measured_at, collected_at, measurement_time_verified,
               active_power_kw::float8, pv_input_power_kw::float8, load_power_kw::float8,
               grid_power_kw::float8, battery_power_kw::float8, battery_soc_percent::float8,
               total_yield_kwh::float8, yield_today_kwh::float8, field_quality,
               power_direction_convention, status, count(*) OVER () AS total
        FROM public.device_realtime
        WHERE device_id = %s AND measured_at >= %s AND measured_at < %s
        ORDER BY measured_at, id LIMIT %s OFFSET %s
    """, (*params, page_size, (page - 1) * page_size))
    total = rows[0]["total"] if rows else fetch_rows("""
        SELECT count(*) AS total FROM public.device_realtime
        WHERE device_id = %s AND measured_at >= %s AND measured_at < %s
    """, params)[0]["total"]
    return rows, total
