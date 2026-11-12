"""Bulk reads keep metadata, telemetry and inventory in one database snapshot."""
from app import config
from app.database import fetch_rows


def fleet(plant_code: str | None = None):
    return fetch_rows("""
        SELECT p.plant_code, p.plant_name, p.capacity_kwp::float8 AS capacity_kwp,
            p.address, p.is_demo,
            COALESCE(to_jsonb(pm) - 'plant_code' - 'created_at' - 'updated_at', '{}'::jsonb)
                || jsonb_strip_nulls(jsonb_build_object(
                    'latitude', p.latitude::float8,
                    'longitude', p.longitude::float8
                )) AS metadata,
            COALESCE(dr.recorded_at, r.recorded_at) AS recorded_at,
            COALESCE(dr.pv_power_kw, r.pv_power_kw::float8) AS pv_power_kw,
            COALESCE(dr.load_power_kw, r.load_power_kw::float8) AS load_power_kw,
            COALESCE(dr.grid_power_kw, r.grid_power_kw::float8) AS grid_power_kw,
            COALESCE(dr.yield_today_kwh, r.yield_today_kwh::float8) AS yield_today_kwh,
            COALESCE(dr.total_yield_kwh, r.total_yield_kwh::float8) AS total_yield_kwh,
            COALESCE(dr.battery_power_kw, r.battery_power_kw::float8) AS battery_power_kw,
            COALESCE(dr.battery_soc_percent, r.battery_soc_percent::float8) AS battery_soc_percent,
            COALESCE(dr.ess_charge_today_kwh, r.ess_charge_today_kwh::float8) AS ess_charge_today_kwh,
            COALESCE(dr.ess_discharge_today_kwh, r.ess_discharge_today_kwh::float8) AS ess_discharge_today_kwh,
            CASE WHEN dr.has_metrics THEN 'FUSIONSOLAR_DEVICE' ELSE r.data_source END AS data_source,
            r.raw_data AS snapshot_raw,
            dr.measured_at AS normalized_measured_at,
            dr.collected_at AS normalized_collected_at,
            dr.measurement_time_verified AS normalized_time_verified,
            dr.expected_interval_seconds AS normalized_expected_interval_seconds,
            dr.power_direction_convention AS normalized_power_direction_convention,
            dr.status AS normalized_status,
            dr.status_reason AS normalized_status_reason,
            dr.metric_fields AS normalized_metric_fields,
            COALESCE(nd.inventory_payload, d.raw_data) AS inventory_payload,
            COALESCE(nd.inventory_collected_at, d.collected_at) AS inventory_collected_at,
            nd.inventory_complete AS normalized_inventory_complete,
            iss.last_result_status AS inventory_source_status,
            iss.last_result_complete AS inventory_source_complete,
            iss.stale_after_seconds AS inventory_stale_after_seconds,
            na.items AS normalized_alarms,
            ass.configured AS alarm_configured,
            ass.last_success_at AS alarm_last_success_at,
            ass.last_attempt_at AS alarm_last_attempt_at,
            ass.last_result_status AS alarm_source_status,
            ass.last_result_complete AS alarm_source_complete,
            ass.last_result_count AS alarm_source_count,
            a.raw_data AS alarms_payload, a.collected_at AS alarms_collected_at,
            j.status AS alarm_job_status, j.started_at AS alarm_job_started_at
        FROM public.plants p
        LEFT JOIN public.plant_metadata pm ON pm.plant_code = p.plant_code
        LEFT JOIN LATERAL (
            SELECT x.* FROM public.plant_realtime x
            WHERE x.plant_code = p.plant_code
            ORDER BY x.recorded_at DESC, x.id DESC LIMIT 1
        ) r ON TRUE
        LEFT JOIN LATERAL (
            WITH latest AS (
                SELECT DISTINCT ON (x.device_id) x.*, dv.device_type
                FROM public.device_realtime x
                JOIN public.devices dv ON dv.id = x.device_id
                WHERE x.plant_code = p.plant_code
                  AND dv.inventory_collected_at = (
                      SELECT MAX(current_device.inventory_collected_at)
                      FROM public.devices current_device
                      WHERE current_device.plant_code = p.plant_code
                  )
                ORDER BY x.device_id, x.collected_at DESC, x.id DESC
            ), relevant AS (
                SELECT * FROM latest WHERE
                    active_power_kw IS NOT NULL OR pv_input_power_kw IS NOT NULL OR
                    load_power_kw IS NOT NULL OR grid_power_kw IS NOT NULL OR
                    battery_power_kw IS NOT NULL OR battery_soc_percent IS NOT NULL OR
                    yield_today_kwh IS NOT NULL OR total_yield_kwh IS NOT NULL OR
                    ess_charge_today_kwh IS NOT NULL OR ess_discharge_today_kwh IS NOT NULL
            )
            SELECT TRUE AS has_metrics,
                COALESCE(MIN(measured_at) FILTER (WHERE measurement_time_verified),
                         MAX(collected_at)) AS recorded_at,
                MIN(measured_at) FILTER (WHERE measurement_time_verified) AS measured_at,
                MIN(collected_at) AS collected_at,
                BOOL_AND(measurement_time_verified) AS measurement_time_verified,
                MAX(expected_interval_seconds) AS expected_interval_seconds,
                SUM(COALESCE(pv_input_power_kw, active_power_kw)::float8)
                    FILTER (WHERE device_type = 'inverter') AS pv_power_kw,
                SUM(load_power_kw::float8) FILTER (WHERE load_power_kw IS NOT NULL) AS load_power_kw,
                SUM(grid_power_kw::float8) FILTER (WHERE grid_power_kw IS NOT NULL) AS grid_power_kw,
                SUM(battery_power_kw::float8) FILTER (WHERE battery_power_kw IS NOT NULL) AS battery_power_kw,
                AVG(battery_soc_percent::float8) FILTER (WHERE battery_soc_percent IS NOT NULL) AS battery_soc_percent,
                SUM(yield_today_kwh::float8) FILTER (WHERE device_type = 'inverter') AS yield_today_kwh,
                SUM(total_yield_kwh::float8) FILTER (WHERE device_type = 'inverter') AS total_yield_kwh,
                SUM(ess_charge_today_kwh::float8) FILTER (WHERE ess_charge_today_kwh IS NOT NULL) AS ess_charge_today_kwh,
                SUM(ess_discharge_today_kwh::float8) FILTER (WHERE ess_discharge_today_kwh IS NOT NULL) AS ess_discharge_today_kwh,
                CASE WHEN BOOL_AND(power_direction_convention = 'grid_import_battery_discharge_positive')
                     THEN 'grid_import_battery_discharge_positive' ELSE 'unverified' END AS power_direction_convention,
                CASE WHEN BOOL_OR(status = 'faulty') THEN 'faulty'
                     WHEN BOOL_OR(status = 'disconnected') THEN 'disconnected'
                     WHEN BOOL_AND(status = 'normal') THEN 'normal' ELSE 'unknown' END AS status,
                'NORMALIZED_DEVICE_TELEMETRY' AS status_reason,
                jsonb_strip_nulls(jsonb_build_object(
                    'pv_power_kw', BOOL_OR(pv_input_power_kw IS NOT NULL OR (device_type = 'inverter' AND active_power_kw IS NOT NULL)),
                    'load_power_kw', BOOL_OR(load_power_kw IS NOT NULL),
                    'grid_power_kw', BOOL_OR(grid_power_kw IS NOT NULL),
                    'battery_power_kw', BOOL_OR(battery_power_kw IS NOT NULL),
                    'battery_soc_percent', BOOL_OR(battery_soc_percent IS NOT NULL),
                    'yield_today_kwh', BOOL_OR(device_type = 'inverter' AND yield_today_kwh IS NOT NULL),
                    'total_yield_kwh', BOOL_OR(device_type = 'inverter' AND total_yield_kwh IS NOT NULL),
                    'ess_charge_today_kwh', BOOL_OR(ess_charge_today_kwh IS NOT NULL),
                    'ess_discharge_today_kwh', BOOL_OR(ess_discharge_today_kwh IS NOT NULL)
                )) AS metric_fields
            FROM relevant
            HAVING COUNT(*) > 0
        ) dr ON TRUE
        LEFT JOIN LATERAL (
            SELECT jsonb_build_object(
                    'success', TRUE,
                    'normalized', TRUE,
                    'data', jsonb_agg(
                        COALESCE(x.raw_data, '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
                            'device_id', x.vendor_device_id,
                            'device_key', x.id::text,
                            'device_sn', x.device_sn,
                            'device_name', x.device_name,
                            'stationCode', x.plant_code,
                            'vendor_device_type_id', x.vendor_device_type_id,
                            'device_type', x.device_type,
                            'model', x.model,
                            'software_version', x.software_version,
                            'latitude', x.latitude::float8,
                            'longitude', x.longitude::float8,
                            'optimizer_quantity', x.optimizer_quantity,
                            'rated_ac_power_kw', x.rated_ac_power_kw::float8,
                            'rated_energy_kwh', x.rated_energy_kwh::float8,
                            'status', rt.status,
                            'last_seen_at', CASE WHEN rt.measurement_time_verified THEN rt.measured_at END
                        )) ORDER BY x.device_type, x.id
                    )
                ) AS inventory_payload,
                MAX(x.inventory_collected_at) AS inventory_collected_at,
                BOOL_AND(x.inventory_complete) AS inventory_complete
            FROM public.devices x
            LEFT JOIN LATERAL (
                SELECT z.status, z.measured_at, z.measurement_time_verified
                FROM public.device_realtime z
                WHERE z.device_id = x.id
                ORDER BY z.collected_at DESC, z.id DESC LIMIT 1
            ) rt ON TRUE
            WHERE x.plant_code = p.plant_code
              AND x.inventory_collected_at = (
                  SELECT MAX(z.inventory_collected_at) FROM public.devices z
                  WHERE z.plant_code = p.plant_code
              )
            HAVING COUNT(*) > 0
        ) nd ON TRUE
        LEFT JOIN LATERAL (
            SELECT x.raw_data, x.collected_at FROM public.fusionsolar_raw x
            WHERE x.plant_code = p.plant_code AND x.api_endpoint = 'getDevList'
            ORDER BY x.collected_at DESC, x.id DESC LIMIT 1
        ) d ON TRUE
        LEFT JOIN LATERAL (
            SELECT x.* FROM public.plant_source_state x
            WHERE x.plant_code = p.plant_code AND x.api_endpoint = 'getDevList'
            ORDER BY x.updated_at DESC LIMIT 1
        ) iss ON TRUE
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(
                COALESCE(x.raw_data, '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
                    'alarm_id', x.alarm_key,
                    'vendor_alarm_id', x.vendor_alarm_id,
                    'vendor_occurrence_id', x.vendor_occurrence_id,
                    'device_id', dv.vendor_device_id,
                    'device_sn', x.device_sn,
                    'severity', x.severity,
                    'state', x.state,
                    'acknowledged', x.acknowledged,
                    'name', x.name,
                    'description', x.description,
                    'occurred_at', x.occurred_at,
                    'cleared_at', x.cleared_at
                )) ORDER BY x.occurred_at DESC NULLS LAST, x.id DESC
            ) AS items
            FROM public.alarms x
            LEFT JOIN public.devices dv ON dv.id = x.device_id
            WHERE x.plant_code = p.plant_code
        ) na ON TRUE
        LEFT JOIN LATERAL (
            SELECT x.* FROM public.plant_source_state x
            WHERE x.plant_code = p.plant_code
              AND x.api_endpoint = %s
            ORDER BY x.updated_at DESC LIMIT 1
        ) ass ON TRUE
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
    """, (config.FUSIONSOLAR_ALARMS_PATH or "getAlarmList",
           config.FUSIONSOLAR_ALARMS_PATH, plant_code, plant_code))


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


def energy_statistics(codes: list[str], start, end, grain: str, time_zone: str):
    if not codes:
        return []
    return fetch_rows("""
        SELECT plant_code, grain, time_zone, start_at, end_at,
            yield_kwh::float8 AS yield_kwh, quality, complete,
            received_samples, expected_samples, method, reason, coverage,
            source_endpoint, collected_at
        FROM public.plant_energy_statistics
        WHERE plant_code = ANY(%s)
          AND grain = %s AND time_zone = %s
          AND start_at >= %s AND end_at <= %s
        ORDER BY start_at, plant_code, collected_at DESC, id DESC
    """, (codes, grain, time_zone, start, end))
