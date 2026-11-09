from datetime import timedelta
from hashlib import sha256
import json
import math

from app import config
from app.errors import ApiException
from app.repositories import fleet_repository
from app.repositories.realtime_repository import METRIC_FIELDS
from app.services import inventory_service
from app.services.fusionsolar_client import extract_list
from app.services.values import aware_time, number, utc_now, valid_zone

SORT_FIELDS = ("plant_name", "capacity_kwp", "pv_power_kw", "yield_today_kwh", "total_yield_kwh", "specific_energy_kwh_per_kwp")


def response_meta(rows, now=None, **extra):
    # Hash content, not wall-clock generation time; identical snapshots keep the same ID.
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in ("age_seconds", "generated_at", "snapshot_id")}
        if isinstance(value, list):
            return [stable(v) for v in value]
        return value
    digest = sha256(json.dumps(stable(rows), sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()[:24]
    return {"generated_at": now or utc_now(), "snapshot_id": "snapshot-" + digest,
            "is_partial": False, **extra}


def require_plant(rows):
    if not rows:
        raise ApiException(404, "PLANT_NOT_FOUND", "PLTS tidak ditemukan.")
    return rows[0]


def measurement(row):
    raw = row.get("snapshot_raw") or {}
    source = raw.get("sampling_time_source") or row.get("timestamp_source")
    recorded = aware_time(row.get("recorded_at"))
    measured = aware_time(raw.get("measured_at"))
    if measured is None and source == "device":
        measured = recorded
    collected = aware_time(raw.get("collected_at"))
    if collected is None and source == "collector":
        collected = recorded
    return source, recorded, measured, collected


def normalize(row, now=None):
    now = now or utc_now()
    result = inventory_service.metadata(row)
    source, recorded, measured, collected = measurement(row)
    verified = measured is not None and measured <= now + timedelta(minutes=1)
    age = max(0.0, (now - measured).total_seconds()) if verified else None
    metrics = {k: number(row.get(k)) for k in METRIC_FIELDS}
    for key in ("pv_power_kw", "load_power_kw", "yield_today_kwh", "total_yield_kwh", "ess_charge_today_kwh", "ess_discharge_today_kwh"):
        if metrics[key] is not None and metrics[key] < 0:
            metrics[key] = None
    soc = metrics["battery_soc_percent"]
    if soc is not None and not 0 <= soc <= 100:
        metrics["battery_soc_percent"] = None
    field_quality = {}
    for key, value in metrics.items():
        if key.startswith(("battery_", "ess_")) and result["has_ess"] is False:
            field_quality[key] = "NOT_INSTALLED"
        elif value is None:
            field_quality[key] = "SOURCE_UNAVAILABLE"
        elif result["is_demo"]:
            field_quality[key] = "DEMO_DATA"
        elif not verified:
            field_quality[key] = "TIME_UNVERIFIED"
        elif age > config.STALE_AFTER_MINUTES * 60:
            field_quality[key] = "STALE"
        else:
            field_quality[key] = "VALID"
    if not any(value is not None for value in metrics.values()):
        state = "missing"
    elif not verified or result["is_demo"]:
        state = "unverified"
    elif age > config.STALE_AFTER_MINUTES * 60:
        state = "stale"
    elif any(v == "SOURCE_UNAVAILABLE" for v in field_quality.values()):
        state = "partial"
    else:
        state = "fresh"
    raw = row.get("snapshot_raw") or {}
    # Old snapshots embed a batch response; select this plant's health only.
    items = extract_list(raw.get("response") or {})
    item = next((i for i in items if str(i.get("stationCode", "")) == row["plant_code"]), {})
    health = (item.get("dataItemMap") or item.get("kpis") or {}).get("real_health_state")
    health = raw.get("real_health_state", health)
    status = "unknown"
    reason = "STATUS_SOURCE_UNAVAILABLE"
    status_source = None
    if health is not None:
        status_source = "fusionsolar.real_health_state"
        if not verified or result["is_demo"]:
            reason = "STATUS_TIME_UNVERIFIED"
        elif age > config.STALE_AFTER_MINUTES * 60:
            reason = "STATUS_STALE"
        else:
            status = {"1": "disconnected", "2": "faulty", "3": "normal"}.get(str(health), "unknown")
            reason = "SOURCE_HEALTH_STATE" if status != "unknown" else "UNRECOGNIZED_HEALTH_STATE"
    capacity = result["capacity_kwp"]
    specific = metrics["yield_today_kwh"] / capacity if capacity is not None and capacity > 0 and metrics["yield_today_kwh"] is not None else None
    zone = valid_zone(result["time_zone"])
    return {**result, **metrics,
            "recorded_at": recorded, "timestamp_source": source,
            "measured_at": measured if verified else None, "collected_at": collected,
            "measurement_time_verified": verified, "data_source": row.get("data_source"),
            "status": status, "status_source": status_source, "status_reason": reason,
            "last_seen_at": measured if verified else None,
            "yield_business_date": measured.astimezone(zone).date() if verified and zone else None,
            "specific_energy_kwh_per_kwp": specific, "performance_ratio_percent": None,
            "poa_irradiation_today_kwh_m2": None, "revenue_today_idr": None,
            "power_direction_convention": "source_native_unverified",
            "data_quality": {"state": state, "measured_at": measured if verified else None,
                             "collected_at": collected, "measurement_time_verified": verified,
                             "age_seconds": age, "stale_after_seconds": config.STALE_AFTER_MINUTES * 60,
                             "expected_interval_seconds": config.EXPECTED_INTERVAL_SECONDS,
                             "missing_fields": [k for k, v in metrics.items() if v is None and field_quality[k] != "NOT_INSTALLED"],
                             "not_applicable_fields": [k for k, v in field_quality.items() if v == "NOT_INSTALLED"],
                             "field_quality": field_quality}}


def get_rows(plant_code=None):
    return fleet_repository.fleet(plant_code)


def filter_rows(rows, search=None, country_code=None, region=None, device_type=None, device_sn=None, status=None):
    result = []
    for row in rows:
        if search and search.casefold() not in (row["plant_name"] + " " + row["plant_code"]).casefold():
            continue
        if country_code and row.get("country_code") != country_code:
            continue
        if region and row.get("region") != region:
            continue
        if device_type and device_type not in row.get("device_types", []):
            continue
        if status and row.get("status") != status:
            continue
        if device_sn and not any(device_sn.casefold() in (d.get("device_sn") or "").casefold() for d in row.get("_devices", [])):
            continue
        result.append(row)
    return result


def normalized_rows(raw_rows=None, **filters):
    raw_rows = get_rows() if raw_rows is None else raw_rows
    now = utc_now()
    rows = []
    for raw in raw_rows:
        row = normalize(raw, now)
        row["_devices"] = inventory_service.devices(raw.get("inventory_payload"), row["plant_code"])
        rows.append(row)
    return filter_rows(rows, **filters)


def overview(page=None, page_size=None, sort="plant_name", order="asc", **filters):
    rows = normalized_rows(**filters)
    rows.sort(key=lambda r: r["plant_code"])
    present = [r for r in rows if r.get(sort) is not None]
    missing = [r for r in rows if r.get(sort) is None]
    present.sort(key=lambda r: r[sort].casefold() if isinstance(r[sort], str) else r[sort], reverse=order == "desc")
    rows = present + missing  # Missing values are always last.
    meta = response_meta([{k: v for k, v in r.items() if k != "_devices"} for r in rows],
                         is_partial=any(r["data_quality"]["state"] != "fresh" for r in rows))
    total = len(rows)
    meta["filters_applied"] = {key: value for key, value in filters.items() if value is not None}
    meta["scope"] = "all_configured_plants_filtered" if meta["filters_applied"] else "all_configured_plants"
    zones = {r["time_zone"] for r in rows}
    meta["time_zone"] = next(iter(zones)) if len(zones) == 1 else None
    meta["coverage"] = {field: metric_total(rows, field)[1] for field in (*METRIC_FIELDS, "capacity_kwp")}
    if page is not None or page_size is not None:
        page, page_size = page or 1, page_size or 10
        meta.update(page=page, page_size=page_size, total_pages=math.ceil(total / page_size))
        rows = rows[(page - 1) * page_size:page * page_size]
    meta["returned"] = len(rows)
    return {"success": True, "source": "PostgreSQL", "total": total,
            "data": [{k: v for k, v in r.items() if k != "_devices"} for r in rows], "meta": meta}


def metric_total(rows, field, business_date=None, time_zone=None):
    counts = dict(expected=len(rows), valid=0, missing=0, stale=0, unverified=0, not_applicable=0)
    values = []
    for row in rows:
        value = number(row.get(field))
        if field.startswith("ess_") and row.get("has_ess") is False:
            counts["not_applicable"] += 1
            continue
        if field in ("revenue_today_idr",):
            counts["missing"] += 1
            continue
        telemetry = field in METRIC_FIELDS
        if value is None or field == "capacity_kwp" and value <= 0:
            counts["missing"] += 1
        elif telemetry and row["data_quality"]["field_quality"].get(field) in ("TIME_UNVERIFIED", "DEMO_DATA"):
            counts["unverified"] += 1
        elif telemetry and row["data_quality"]["field_quality"].get(field) == "STALE":
            counts["stale"] += 1
        elif field == "yield_today_kwh" and row.get("yield_business_date") is None:
            counts["unverified"] += 1
        elif business_date is not None and field == "yield_today_kwh" and (
            row.get("time_zone") != time_zone or row.get("yield_business_date") != business_date
        ):
            # A plant-local daily counter cannot be rebased to another timezone.
            counts["unverified"] += 1
        else:
            counts["valid"] += 1
            values.append(value)
    counts["partial"] = bool(counts["missing"] or counts["stale"] or counts["unverified"])
    return sum(values) if values else None, counts
