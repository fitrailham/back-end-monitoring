"""Map device KPI payloads without inventing timestamps, units, or signs."""
from datetime import datetime, timezone

from app import config
from app.services.kpi_mapper import _first


def _time(value):
    try:
        value = float(value)
        if value <= 0:
            return None
        return datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _scaled(mapping, scale, *keys):
    value = _first(mapping, *keys)
    return value * scale if value is not None else None


def device_identifier(row):
    value = row.get("devId", row.get("devDn", row.get("id", row.get("device_id"))))
    return str(value) if value is not None else None


def map_device_realtime(row: dict, device: dict, collected_at: datetime) -> dict:
    values = row.get("dataItemMap") or row.get("kpis") or {}
    if not isinstance(values, dict):
        raise ValueError("Device KPI dataItemMap bukan object")
    kind = str(device.get("vendor_device_type_id") or "")
    dtype = device.get("device_type", "unknown")
    convention = config.FUSIONSOLAR_POWER_DIRECTION_CONVENTION

    active = _first(values, "active_power_kw")
    if active is None:
        active = _scaled(values, 1.0 if dtype == "inverter" else 0.001, "active_power")
    pv_input = _first(values, "pv_input_power_kw")
    if pv_input is None and dtype == "inverter":
        pv_input = _scaled(values, 1.0, "mppt_power")

    grid = _first(values, "grid_power_kw")
    battery = _first(values, "battery_power_kw")
    if convention == "grid_import_battery_discharge_positive":
        if grid is None and dtype == "meter":
            grid = _scaled(values, 0.001, "active_power")
        if battery is None and dtype == "ess":
            battery = _scaled(values, 0.001, "ch_discharge_power")
    else:
        grid = None
        battery = None

    measured_at = _time(row.get("collectTime", values.get("collectTime")))
    verified = measured_at is not None
    if measured_at is None:
        measured_at = _time(row.get("currentTime", values.get("currentTime"))) or collected_at

    metrics = {
        "active_power_kw": active,
        "pv_input_power_kw": pv_input,
        "load_power_kw": _first(values, "load_power_kw"),
        "grid_power_kw": grid,
        "battery_power_kw": battery,
        "battery_soc_percent": _first(values, "battery_soc_percent", "battery_soc"),
        "yield_today_kwh": _first(values, "yield_today_kwh", "day_cap") if dtype == "inverter" else None,
        "total_yield_kwh": _first(values, "total_yield_kwh", "total_cap") if dtype == "inverter" else None,
        "ess_charge_today_kwh": _first(values, "ess_charge_today_kwh", "charge_cap") if dtype == "ess" else None,
        "ess_discharge_today_kwh": _first(values, "ess_discharge_today_kwh", "discharge_cap") if dtype == "ess" else None,
        "irradiance_w_m2": _first(values, "irradiance_w_m2", "radiation_intensity") if dtype == "emi" else None,
    }
    soc = metrics["battery_soc_percent"]
    if soc is not None and not 0 <= soc <= 100:
        metrics["battery_soc_percent"] = None
    status = str(values.get("status", row.get("status", "unknown"))).lower()
    if status not in ("normal", "faulty", "disconnected"):
        status = "unknown"
    quality = {name: ("VALID" if verified else "TIME_UNVERIFIED")
               for name, value in metrics.items() if value is not None}
    return {
        "device_id": device["id"], "plant_code": device["plant_code"],
        "source_endpoint": "getDevRealKpi", "measured_at": measured_at,
        "collected_at": collected_at, "measurement_time_verified": verified,
        "expected_interval_seconds": config.EXPECTED_INTERVAL_SECONDS,
        **metrics, "yield_business_date": None,
        "power_direction_convention": convention,
        "status": status, "status_reason": "VENDOR_STATUS" if status != "unknown" else "STATUS_SOURCE_UNAVAILABLE",
        "field_quality": quality, "raw_data": row,
        "vendor_device_type_id": kind,
    }
