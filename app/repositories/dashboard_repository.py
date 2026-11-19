"""Northbound measurements used by the Dashboard Home contract."""
from app.database import fetch_rows


def inverter_power(codes):
    if not codes:
        return []
    return fetch_rows("""
        WITH inventory AS (
            SELECT d.*, MAX(inventory_collected_at) OVER (PARTITION BY plant_code) AS latest_inventory
            FROM public.devices d WHERE d.plant_code = ANY(%s)
        )
        SELECT DISTINCT ON (
            d.plant_code, COALESCE(NULLIF(BTRIM(d.device_sn), ''),
                                   d.source_server || ':' || d.vendor_device_id)
        )
            d.plant_code, k.active_power_kw::float8, k.measured_at, k.measurement_time_verified
        FROM inventory d
        LEFT JOIN LATERAL (
            SELECT x.active_power_kw, x.measured_at, x.measurement_time_verified, x.collected_at
            FROM public.device_realtime x
            WHERE x.device_id = d.id AND x.plant_code = d.plant_code
              AND x.source_endpoint = 'getDevRealKpi'
              AND x.active_power_kw >= 0 AND x.active_power_kw::text NOT IN ('NaN', 'Infinity')
            ORDER BY x.measurement_time_verified DESC, x.collected_at DESC, x.id DESC LIMIT 1
        ) k ON TRUE
        WHERE d.device_type = 'inverter' AND d.inventory_collected_at = d.latest_inventory
        ORDER BY d.plant_code, COALESCE(NULLIF(BTRIM(d.device_sn), ''),
                                       d.source_server || ':' || d.vendor_device_id),
                 k.measurement_time_verified DESC NULLS LAST, k.collected_at DESC NULLS LAST, d.id DESC
    """, (codes,))


def station_yield(codes, start, end, now):
    if not codes:
        return []
    return fetch_rows("""
        WITH daily AS (
            SELECT DISTINCT ON (plant_code) plant_code,
                yield_today_kwh::float8 AS day_yield, recorded_at AS day_at,
                raw_data->>'sampling_time_source' = 'device' AS day_verified
            FROM public.plant_realtime
            WHERE plant_code = ANY(%s) AND recorded_at >= %s AND recorded_at < %s
              AND recorded_at <= %s AND data_source = 'FUSIONSOLAR'
              AND yield_today_kwh >= 0
              AND yield_today_kwh::text NOT IN ('NaN', 'Infinity')
            ORDER BY plant_code, (raw_data->>'sampling_time_source' = 'device') DESC NULLS LAST,
                     recorded_at DESC, id DESC
        ), lifetime AS (
            SELECT plant_code, recorded_at, total_yield_kwh AS value,
                LAG(total_yield_kwh) OVER timeline AS previous,
                COUNT(*) FILTER (WHERE total_yield_kwh = 0) OVER timeline AS counter_epoch
            FROM public.plant_realtime
            WHERE plant_code = ANY(%s) AND recorded_at <= %s
              AND data_source = 'FUSIONSOLAR' AND total_yield_kwh >= 0
              AND total_yield_kwh::text NOT IN ('NaN', 'Infinity')
              AND raw_data->>'sampling_time_source' = 'device'
            WINDOW timeline AS (PARTITION BY plant_code ORDER BY recorded_at, id)
        ), epochs AS (
            SELECT plant_code, MAX(value) AS value, MAX(recorded_at) AS latest_at,
                   BOOL_OR(value > 0 AND previous > value) AS uncertain_reset
            FROM lifetime GROUP BY plant_code, counter_epoch
        ), cumulative AS (
            SELECT plant_code, SUM(value)::float8 AS total_yield,
                   MAX(latest_at) AS total_at, COALESCE(BOOL_OR(uncertain_reset), FALSE) AS uncertain_reset
            FROM epochs GROUP BY plant_code
        )
        SELECT plant_code, daily.day_yield, daily.day_at, daily.day_verified,
               cumulative.total_yield, cumulative.total_at, cumulative.uncertain_reset
        FROM daily FULL JOIN cumulative USING (plant_code)
    """, (codes, start, end, now, codes, now))


def station_health(codes):
    if not codes:
        return []
    return fetch_rows("""
        SELECT DISTINCT ON (plant_code) plant_code, raw_data, collected_at
        FROM public.fusionsolar_raw
        WHERE plant_code = ANY(%s) AND api_endpoint = 'getStationRealKpi'
        ORDER BY plant_code, collected_at DESC, id DESC
    """, (codes,))
