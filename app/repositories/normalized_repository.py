"""Persistence for the normalized Northbound tables.

Raw payloads remain in ``fusionsolar_raw`` for audit.  These functions only
write values that have an explicit source and never manufacture zero values.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json

from psycopg.types.json import Jsonb

from app import config
from app.database import fetch_rows
from app.repositories.plant_repository import open_db
from app.services.values import number


def source_server() -> str:
    return config.FUSIONSOLAR_SOURCE_SERVER


def upsert_source_state(
    plant_code: str,
    endpoint: str,
    *,
    configured: bool,
    status: str,
    complete: bool,
    count: int | None = None,
    error: str | None = None,
    attempted_at=None,
    expected_interval_seconds: int | None = None,
    stale_after_seconds: int | None = None,
    conn=None,
):
    attempted_at = attempted_at or datetime.now(timezone.utc)
    expected_interval_seconds = expected_interval_seconds or config.EXPECTED_INTERVAL_SECONDS
    stale_after_seconds = stale_after_seconds or config.STALE_AFTER_MINUTES * 60

    def write(db):
        # CASE otherwise infers text for ISO strings; explicitly cast its timestamp branches below.
        db.execute("""
            INSERT INTO public.plant_source_state (
                plant_code, source_server, api_endpoint, configured,
                expected_interval_seconds, stale_after_seconds,
                last_attempt_at, last_success_at, last_failure_at,
                last_result_status, last_result_complete, last_result_count,
                fail_code, last_error, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                CASE WHEN %s IN ('success', 'partial') THEN %s::timestamptz END,
                CASE WHEN %s = 'failed' THEN %s::timestamptz END,
                %s, %s, %s, NULL, %s, NOW()
            )
            ON CONFLICT (plant_code, source_server, api_endpoint) DO UPDATE SET
                configured = EXCLUDED.configured,
                expected_interval_seconds = EXCLUDED.expected_interval_seconds,
                stale_after_seconds = EXCLUDED.stale_after_seconds,
                last_attempt_at = EXCLUDED.last_attempt_at,
                last_success_at = COALESCE(EXCLUDED.last_success_at, plant_source_state.last_success_at),
                last_failure_at = COALESCE(EXCLUDED.last_failure_at, plant_source_state.last_failure_at),
                last_result_status = EXCLUDED.last_result_status,
                last_result_complete = EXCLUDED.last_result_complete,
                last_result_count = EXCLUDED.last_result_count,
                fail_code = EXCLUDED.fail_code,
                last_error = EXCLUDED.last_error,
                updated_at = NOW()
        """, (
            plant_code, source_server(), endpoint, configured,
            expected_interval_seconds, stale_after_seconds,
            attempted_at, status, attempted_at, status, attempted_at,
            status, complete, count, (error or "")[:1000] or None,
        ))

    if conn is not None:
        write(conn)
    else:
        with open_db() as db:
            write(db)


def upsert_devices(plant_code: str, items: list[dict], collected_at, raw_response_id: int | None,
                   complete: bool, conn):
    for item in items:
        vendor_id = item.get("devDn") or item.get("id") or item.get("device_id")
        if vendor_id is None:
            raise ValueError(f"Perangkat plant {plant_code} tidak memiliki ID vendor")
        kind = str(item.get("devTypeId", item.get("vendor_device_type_id", "")))
        device_type = config.DEVICE_TYPE_MAP.get(kind, item.get("device_type", "unknown"))
        if device_type not in ("inverter", "ess", "meter", "logger", "dongle", "emi", "unknown"):
            device_type = "unknown"
        latitude, longitude = number(item.get("latitude")), number(item.get("longitude"))
        if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            latitude = longitude = None
        optimizer = number(item.get("optimizerNumber", item.get("optimizer_quantity")))
        optimizer = int(optimizer) if optimizer is not None and optimizer >= 0 and optimizer.is_integer() else None
        conn.execute("""
            INSERT INTO public.devices (
                plant_code, source_server, vendor_device_id, vendor_device_type_id,
                device_sn, device_name, device_type, model, software_version,
                latitude, longitude, optimizer_quantity, rated_ac_power_kw,
                rated_energy_kwh, inventory_collected_at, inventory_complete,
                raw_response_id, raw_data, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (source_server, vendor_device_id) DO UPDATE SET
                plant_code = EXCLUDED.plant_code,
                vendor_device_type_id = EXCLUDED.vendor_device_type_id,
                device_sn = EXCLUDED.device_sn,
                device_name = EXCLUDED.device_name,
                device_type = EXCLUDED.device_type,
                model = EXCLUDED.model,
                software_version = EXCLUDED.software_version,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                optimizer_quantity = EXCLUDED.optimizer_quantity,
                rated_ac_power_kw = COALESCE(EXCLUDED.rated_ac_power_kw, devices.rated_ac_power_kw),
                rated_energy_kwh = COALESCE(EXCLUDED.rated_energy_kwh, devices.rated_energy_kwh),
                inventory_collected_at = EXCLUDED.inventory_collected_at,
                inventory_complete = EXCLUDED.inventory_complete,
                raw_response_id = EXCLUDED.raw_response_id,
                raw_data = EXCLUDED.raw_data,
                updated_at = NOW()
        """, (
            plant_code, source_server(), str(vendor_id), kind or None,
            str(item.get("esnCode", item.get("device_sn"))) if item.get("esnCode", item.get("device_sn")) is not None else None,
            item.get("devName", item.get("device_name")), device_type,
            str(item["model"]) if item.get("model") is not None else None,
            str(item["softwareVersion"]) if item.get("softwareVersion") is not None else None,
            latitude, longitude, optimizer,
            number(item.get("rated_ac_power_kw")), number(item.get("rated_energy_kwh")), collected_at,
            complete, raw_response_id, Jsonb(item),
        ))


def latest_devices(plant_code: str | None = None):
    return fetch_rows("""
        WITH latest AS (
            SELECT plant_code, source_server, MAX(inventory_collected_at) AS collected_at
            FROM public.devices
            WHERE source_server = %s AND (%s::text IS NULL OR plant_code = %s)
            GROUP BY plant_code, source_server
        )
        SELECT d.* FROM public.devices d
        JOIN latest l ON l.plant_code = d.plant_code
            AND l.source_server = d.source_server
            AND l.collected_at = d.inventory_collected_at
        ORDER BY d.plant_code, d.device_type, d.id
    """, (source_server(), plant_code, plant_code))


def upsert_alarms(plant_code: str, alarms: list[dict], collected_at, raw_response_id: int | None, conn):
    for alarm in alarms:
        raw = alarm.get("_raw") or alarm
        alarm_key = alarm.get("alarm_id")
        if not alarm_key:
            canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
            alarm_key = f"{plant_code}:raw:{sha256(canonical.encode()).hexdigest()[:24]}"
        vendor_occurrence = raw.get("occurrenceId", raw.get("vendor_occurrence_id"))
        device_id = alarm.get("device_id")
        device_sn = alarm.get("device_sn")
        device_row = conn.execute("""
            SELECT id FROM public.devices
            WHERE source_server = %s AND plant_code = %s
              AND ((%s IS NOT NULL AND vendor_device_id = %s)
                   OR (%s IS NOT NULL AND device_sn = %s))
            ORDER BY inventory_collected_at DESC, id DESC LIMIT 1
        """, (source_server(), plant_code, device_id, device_id, device_sn, device_sn)).fetchone()
        conn.execute("""
            INSERT INTO public.alarms (
                plant_code, device_id, source_server, alarm_key,
                vendor_alarm_id, vendor_occurrence_id, device_sn,
                severity, state, acknowledged, name, description,
                occurred_at, cleared_at, first_collected_at, last_collected_at,
                state_source, raw_response_id, raw_data, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, NOW()
            )
            ON CONFLICT (source_server, alarm_key) DO UPDATE SET
                plant_code = EXCLUDED.plant_code,
                device_id = COALESCE(EXCLUDED.device_id, alarms.device_id),
                vendor_alarm_id = EXCLUDED.vendor_alarm_id,
                vendor_occurrence_id = EXCLUDED.vendor_occurrence_id,
                device_sn = EXCLUDED.device_sn,
                severity = EXCLUDED.severity,
                state = CASE WHEN alarms.state = 'cleared' OR EXCLUDED.state = 'unknown'
                             THEN alarms.state ELSE EXCLUDED.state END,
                acknowledged = COALESCE(EXCLUDED.acknowledged, alarms.acknowledged),
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                occurred_at = COALESCE(EXCLUDED.occurred_at, alarms.occurred_at),
                cleared_at = COALESCE(EXCLUDED.cleared_at, alarms.cleared_at),
                last_collected_at = EXCLUDED.last_collected_at,
                state_source = CASE WHEN alarms.state = 'cleared' OR EXCLUDED.state = 'unknown'
                                    THEN alarms.state_source ELSE EXCLUDED.state_source END,
                raw_response_id = EXCLUDED.raw_response_id,
                raw_data = EXCLUDED.raw_data,
                updated_at = NOW()
        """, (
            plant_code, device_row["id"] if device_row else None, source_server(), str(alarm_key),
            alarm.get("vendor_alarm_id"), str(vendor_occurrence) if vendor_occurrence is not None else None,
            device_sn, alarm.get("severity", "unclassified"), alarm.get("state") or "unknown",
            alarm.get("acknowledged"), alarm.get("name"), alarm.get("description"),
            alarm.get("occurred_at"), alarm.get("cleared_at"), collected_at, collected_at,
            "cleared_at" if alarm.get("cleared_at") else "vendor_status" if raw.get("status") is not None else "unknown",
            raw_response_id, Jsonb(raw),
        ))


def save_device_realtime(snapshot: dict, raw_response_id: int | None, conn):
    fields = (
        "active_power_kw", "pv_input_power_kw", "load_power_kw", "grid_power_kw",
        "battery_power_kw", "battery_soc_percent", "yield_today_kwh", "total_yield_kwh",
        "ess_charge_today_kwh", "ess_discharge_today_kwh", "irradiance_w_m2",
    )
    conn.execute("""
        INSERT INTO public.device_realtime (
            device_id, plant_code, source_endpoint, measured_at, collected_at,
            measurement_time_verified, expected_interval_seconds,
            active_power_kw, pv_input_power_kw, load_power_kw, grid_power_kw,
            battery_power_kw, battery_soc_percent, yield_today_kwh, total_yield_kwh,
            ess_charge_today_kwh, ess_discharge_today_kwh, irradiance_w_m2,
            yield_business_date, power_direction_convention, status, status_reason,
            field_quality, raw_response_id, raw_data
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (device_id, source_endpoint, measured_at) DO UPDATE SET
            collected_at = EXCLUDED.collected_at,
            measurement_time_verified = EXCLUDED.measurement_time_verified,
            expected_interval_seconds = EXCLUDED.expected_interval_seconds,
            active_power_kw = EXCLUDED.active_power_kw,
            pv_input_power_kw = EXCLUDED.pv_input_power_kw,
            load_power_kw = EXCLUDED.load_power_kw,
            grid_power_kw = EXCLUDED.grid_power_kw,
            battery_power_kw = EXCLUDED.battery_power_kw,
            battery_soc_percent = EXCLUDED.battery_soc_percent,
            yield_today_kwh = EXCLUDED.yield_today_kwh,
            total_yield_kwh = EXCLUDED.total_yield_kwh,
            ess_charge_today_kwh = EXCLUDED.ess_charge_today_kwh,
            ess_discharge_today_kwh = EXCLUDED.ess_discharge_today_kwh,
            irradiance_w_m2 = EXCLUDED.irradiance_w_m2,
            yield_business_date = EXCLUDED.yield_business_date,
            power_direction_convention = EXCLUDED.power_direction_convention,
            status = EXCLUDED.status,
            status_reason = EXCLUDED.status_reason,
            field_quality = EXCLUDED.field_quality,
            raw_response_id = EXCLUDED.raw_response_id,
            raw_data = EXCLUDED.raw_data
    """, (
        snapshot["device_id"], snapshot["plant_code"], snapshot["source_endpoint"],
        snapshot["measured_at"], snapshot["collected_at"], snapshot["measurement_time_verified"],
        snapshot.get("expected_interval_seconds"), *(snapshot.get(name) for name in fields),
        snapshot.get("yield_business_date"), snapshot["power_direction_convention"],
        snapshot["status"], snapshot.get("status_reason"), Jsonb(snapshot.get("field_quality", {})),
        raw_response_id, Jsonb(snapshot.get("raw_data", {})),
    ))
