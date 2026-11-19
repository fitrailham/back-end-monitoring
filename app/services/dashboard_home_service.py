"""Build the three account-scoped Dashboard Home responses."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import NamedTuple

from app.errors import ApiException
from app.repositories import dashboard_repository
from app.services import alarm_service, fleet_service
from app.services.fusionsolar_client import extract_list
from app.services.values import aware_time, number, utc_now, valid_zone


STALE_AFTER = timedelta(minutes=10)
STATUSES = {1: "disconnected", 2: "faulty", 3: "normal"}


class Reading(NamedTuple):
    value: float | None = None
    state: str = "missing"
    measured_at: datetime | None = None
    cached: bool = False


def nonnegative(value):
    value = number(value)
    return value if value is not None and value >= 0 else None


def current_power(devices, plant, now):
    if not devices:
        return Reading(state="not_applicable" if plant["inventory_complete"]
                       and "inverter" not in plant["device_types"] else "missing")
    values = []
    for device in devices:
        value = nonnegative(device.get("active_power_kw"))
        measured = aware_time(device.get("measured_at"))
        if value is None:
            values.append(Reading())
        elif not device.get("measurement_time_verified") or measured is None or measured > now:
            values.append(Reading(state="unverified"))
        else:
            values.append(Reading(value, "stale" if now - measured >= STALE_AFTER else "valid", measured))
    fresh = [sample for sample in values if sample.state == "valid"]
    stale = [sample for sample in values if sample.state == "stale"]
    available = fresh or stale
    state = ("stale" if stale else
             "unverified" if any(sample.state == "unverified" for sample in values) else
             "missing" if len(fresh) != len(devices) or not plant["inventory_complete"] else "valid")
    return Reading(sum(sample.value for sample in available) if available else None, state,
                   max((sample.measured_at for sample in available), default=None), cached=not fresh)


def inventory_rating(plant, device_type, field):
    devices = [device for device in plant["_devices"] if device["device_type"] == device_type]
    if not devices:
        return Reading(state="not_applicable" if plant["inventory_complete"] else "missing")
    ids = {device["device_id"] for device in devices if device["device_id"]}
    known_ids = {device["device_id"] for device in plant["_devices"] if device["device_id"]}
    seen = set()
    values = []
    uncertain = False
    for device in devices:
        identifier = device["device_sn"] or device["device_id"]
        parent = device.get("parent_device_id")
        if device_type == "ess" and parent:
            if parent in ids and parent != device["device_id"]:
                continue  # The parent rating already represents the ESS.
            if parent not in known_ids or parent == device["device_id"]:
                uncertain = True
                continue
        if identifier is None:
            uncertain = True
            continue
        if identifier in seen:
            continue
        seen.add(identifier)
        values.append(nonnegative(device.get(field)))
    known = [value for value in values if value is not None]
    state = ("unverified" if uncertain or not values else
             "missing" if len(known) != len(values) or not plant["inventory_complete"] else "valid")
    return Reading(sum(known) if known else None, state, aware_time(plant.get("inventory_collected_at")))


def station_yield(plant, sample, time_zone, end, now):
    day = Reading()
    daily_value = nonnegative(sample.get("day_yield"))
    if daily_value is not None:
        measured = aware_time(sample.get("day_at"))
        if measured is None or sample.get("day_verified") is not True or plant.get("time_zone") != time_zone:
            day = Reading(state="unverified")
        else:
            state = "valid"
            if min(now, end) - measured >= STALE_AFTER:
                state = "stale" if now < end else "unverified"
            day = Reading(daily_value, state, measured)
    total = Reading()
    total_value = nonnegative(sample.get("total_yield"))
    updated_at = aware_time(sample.get("total_at"))
    if total_value is not None and updated_at is not None:
        state = ("stale" if now - updated_at >= STALE_AFTER else
                 "unverified" if sample.get("uncertain_reset") else "valid")
        total = Reading(total_value, state, updated_at)
    elif plant.get("total_yield_kwh") is not None:
        total = Reading(state="unverified")
    return day, total


def aggregate(readings):
    coverage = dict(expected=len(readings), valid=0, missing=0, stale=0,
                    unverified=0, not_applicable=0)
    for reading in readings:
        coverage[reading.state] += 1
    coverage["partial"] = bool(coverage["missing"] or coverage["stale"] or coverage["unverified"])
    known = [reading for reading in readings if reading.value is not None]
    selected = [reading for reading in known if not reading.cached] or known
    values = [reading.value for reading in selected]
    return sum(values) if values else None, coverage


def source_status(plant_code, station, snapshot, now):
    if station is None:
        data = snapshot or {}
        health = data.get("real_health_state")
        collected = aware_time(data.get("collected_at") or data.get("measured_at"))
        payload = data.get("response")
    else:
        health = None
        collected = aware_time(station.get("collected_at"))
        payload = station.get("raw_data")
    if health is None:
        item = next((item for item in extract_list(payload or {})
                     if str(item.get("stationCode")) == plant_code), {})
        health = (item.get("dataItemMap") or item.get("kpis") or {}).get("real_health_state")
    if health is None:
        return "unknown", "STATUS_SOURCE_UNAVAILABLE"
    if collected is None or collected > now:
        return "unknown", "STATUS_TIME_UNVERIFIED"
    if now - collected >= STALE_AFTER:
        return "unknown", "STATUS_STALE"
    status = STATUSES.get(number(health), "unknown")
    return status, "SOURCE_HEALTH_STATE" if status != "unknown" else "UNRECOGNIZED_HEALTH_STATE"


def scoped_plants(raw, now):
    snapshots = {row["plant_code"]: row.get("snapshot_raw") for row in raw}
    health = {row["plant_code"]: row for row in dashboard_repository.station_health(list(snapshots))}
    rows = fleet_service.normalized_rows(raw)
    for row in rows:
        code = row["plant_code"]
        row["status"], row["status_reason"] = source_status(
            code, health.get(code), snapshots[code], now
        ) if not row["is_demo"] else ("unknown", "DEMO_DATA")
        row["status_source"] = ("fusionsolar.real_health_state"
                                if row["status_reason"] not in ("STATUS_SOURCE_UNAVAILABLE", "DEMO_DATA") else None)
    return rows


def overview(business_date, time_zone):
    zone = valid_zone(time_zone)
    if zone is None:
        raise ApiException(422, "INVALID_TIME_ZONE", "Gunakan timezone IANA yang valid.")
    try:
        start = datetime.combine(business_date, time.min, zone)
        end = start + timedelta(days=1)
    except OverflowError:
        raise ApiException(422, "INVALID_DATE", "Tanggal bisnis berada di luar rentang yang didukung.") from None
    now = utc_now()
    live_day = now.astimezone(zone).date() == business_date
    rows = scoped_plants(fleet_service.get_rows(), now)
    codes = [row["plant_code"] for row in rows if not row["is_demo"]]
    power = defaultdict(list)
    for sample in dashboard_repository.inverter_power(codes):
        power[sample["plant_code"]].append(sample)
    energy = {sample["plant_code"]: sample for sample in dashboard_repository.station_yield(codes, start, end, now)}
    by_metric = {field: [] for field in (
        "current_power_kw", "revenue_today_idr", "ess_rated_capacity_kwh",
        "yield_today_kwh", "inverter_rated_power_kw", "total_yield_kwh",
    )}
    for row in rows:
        if row["is_demo"]:
            for readings in by_metric.values():
                readings.append(Reading(state="unverified"))
            continue
        code = row["plant_code"]
        daily, total = station_yield(row, energy.get(code, {}), time_zone, end, now)
        by_metric["current_power_kw"].append(current_power(power[code], row, now))
        by_metric["revenue_today_idr"].append(Reading())
        by_metric["ess_rated_capacity_kwh"].append(inventory_rating(row, "ess", "rated_energy_kwh"))
        by_metric["yield_today_kwh"].append(daily)
        by_metric["inverter_rated_power_kw"].append(inventory_rating(row, "inverter", "rated_ac_power_kw"))
        by_metric["total_yield_kwh"].append(total)

    kpis, coverage = {}, {}
    for field, readings in by_metric.items():
        kpis[field], coverage[field] = aggregate(readings)
    live = [*by_metric["current_power_kw"], *by_metric["total_yield_kwh"]]
    if live_day:
        live += by_metric["yield_today_kwh"]
    live_times = [reading.measured_at for reading in live if reading.measured_at is not None]
    updated_at = max(live_times, default=None)
    is_partial = any(counts["partial"] for counts in coverage.values())
    if updated_at is not None and now - updated_at >= STALE_AFTER:
        quality = "stale"
    elif all(value is None for value in kpis.values()):
        quality = "unverified" if any(counts["unverified"] for counts in coverage.values()) else "missing"
    else:
        quality = "partial" if is_partial else "fresh"
    data = {"total_plants": len(rows), "updated_at": updated_at, "quality": quality,
            "kpis": {**kpis, "currency": "IDR"}, "coverage": coverage,
            "plant_status": {status: sum(row["status"] == status for row in rows)
                             for status in (*STATUSES.values(), "unknown")},
            "revenue": {"status": "unconfigured"}}
    return {"success": True, "data": data,
            "meta": fleet_service.response_meta(data, now, date=business_date, time_zone=time_zone,
                                                is_partial=is_partial)}


def verify_snapshot(meta, snapshot_id):
    if snapshot_id is not None and snapshot_id != meta["snapshot_id"]:
        raise ApiException(409, "SNAPSHOT_CHANGED", "Snapshot berubah. Mulai kembali dari halaman pertama.", True)


def plants(page, page_size, snapshot_id):
    now = utc_now()
    rows = scoped_plants(fleet_service.get_rows(), now)
    rows.sort(key=lambda row: (row["plant_name"].casefold(), row["plant_code"]))
    for row in rows:
        row.pop("_devices")
    total = len(rows)
    data = rows[(page - 1) * page_size:page * page_size]
    meta = fleet_service.response_meta(
        rows, now, page=page, page_size=page_size, returned=len(data),
        total_pages=(total + page_size - 1) // page_size,
        is_partial=any(row["data_quality"]["state"] != "fresh" or row["status"] == "unknown" for row in rows),
    )
    verify_snapshot(meta, snapshot_id)
    return {"success": True, "total": total, "data": data, "meta": meta}


def active_alarms(page, page_size, snapshot_id):
    result = alarm_service.fleet_alarms(
        fleet_service.get_rows(), state="active", page=page, page_size=page_size, dashboard=True
    )
    verify_snapshot(result["meta"], snapshot_id)
    return result
