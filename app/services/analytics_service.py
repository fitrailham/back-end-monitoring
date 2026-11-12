"""FE range analytics from persisted measurements, with explicit missing-data semantics."""
from collections import defaultdict
from datetime import timedelta
import math

from app import config
from app.errors import ApiException
from app.repositories import analytics_repository as repository
from app.services import fleet_service, statistics_service
from app.services.values import aware_time, finite_json, number, utc_now

POWER_FIELDS = ("pv_power_kw", "load_power_kw", "grid_power_kw", "battery_power_kw")
SIGNED_FIELDS = {"grid_power_kw", "battery_power_kw"}
VERIFIED_DIRECTION = "grid_import_battery_discharge_positive"


def check_source_limit(rows):
    if len(rows) > repository.MAX_SOURCE_ROWS:
        raise ApiException(422, "SOURCE_RANGE_TOO_DENSE", "Terlalu banyak sampel. Persempit rentang atau filter perangkat; data tidak dipotong diam-diam.")


def bucket_coverage(buckets):
    return {
        "expected_buckets": sum(b["quality"] != "pending" for b in buckets),
        "complete_buckets": sum(b["quality"] in {"measured", "derived"} for b in buckets),
        "partial_buckets": sum(b["quality"] == "partial" for b in buckets),
        "missing_buckets": sum(b["quality"] == "missing" for b in buckets),
        "pending_buckets": sum(b["quality"] == "pending" for b in buckets),
    }


def power_statistics(plant_code, start, end, time_zone, resolution_minutes, aggregation):
    start, end, zone = statistics_service.validate_range(start, end, "hour", time_zone)
    if math.ceil((end - start).total_seconds() / (resolution_minutes * 60)) > 5000:
        raise ApiException(422, "TOO_MANY_BUCKETS", "Maksimum 5000 bucket; gunakan resolusi lebih besar atau rentang lebih pendek.")
    fleet_service.require_plant(fleet_service.get_rows(plant_code))
    samples = repository.plant_power_samples(plant_code, start, end)
    check_source_limit(samples)
    now = utc_now()
    # Last database ID wins for duplicate timestamps; collector times never become measurements.
    latest = {}
    for sample in samples:
        measured = aware_time(sample.get("recorded_at"))
        if measured is not None and start <= measured < end and measured <= now:
            latest[measured] = sample
    groups = defaultdict(list)
    step = timedelta(minutes=resolution_minutes)
    for measured, sample in latest.items():
        groups[int((measured - start).total_seconds() // step.total_seconds())].append((measured, sample))
    buckets = []
    left = start
    while left < end:
        right = min(left + step, end)
        index = len(buckets)
        pending = left >= now
        duration = max(0, (min(right, now) - left).total_seconds())
        expected = math.ceil(duration / config.EXPECTED_INTERVAL_SECONDS)
        bucket = {"start_at": left, "end_at": right, "label_at": left.astimezone(zone).isoformat(), "coverage": {}}
        for field in POWER_FIELDS:
            values, slots, unverified, reasons = [], set(), 0, set()
            for measured, sample in sorted(groups[index], key=lambda pair: pair[0]):
                value = number(sample.get(field))
                if sample.get("timestamp_source") != "device" or sample.get("data_source") == "DEMO":
                    if value is not None:
                        unverified += 1
                        reasons.add("TIME_UNVERIFIED")
                    continue
                if field in SIGNED_FIELDS and sample.get("power_direction_convention") != VERIFIED_DIRECTION:
                    if value is not None:
                        unverified += 1
                        reasons.add("POWER_DIRECTION_UNVERIFIED")
                    continue
                if value is not None:
                    values.append(value)
                    slots.add(int((measured - left).total_seconds() // config.EXPECTED_INTERVAL_SECONDS))
            valid = min(expected, len(slots))
            quality = "pending" if pending else "measured" if expected > 0 and valid == expected else "partial" if values else "unverified" if unverified else "missing"
            if not values and not pending and not reasons:
                reasons.add("NO_VALID_POWER_SAMPLES")
            elif values and valid < expected:
                reasons.add("INCOMPLETE_SAMPLE_COVERAGE")
            if quality == "measured" and right > now:
                quality = "partial"
                reasons.add("OPEN_INTERVAL")
            bucket[field] = (values[-1] if aggregation == "last" else sum(v / len(values) for v in values)) if values and not pending else None
            bucket["coverage"][field] = {
                "expected_samples": expected, "valid_samples": valid,
                "missing_samples": max(0, expected - valid), "unverified_samples": unverified,
                "quality": quality, "reason": ";".join(sorted(reasons)) or None,
            }
        qualities = [coverage["quality"] for coverage in bucket["coverage"].values()]
        bucket["quality"] = "pending" if pending else "measured" if all(q == "measured" for q in qualities) else "partial" if any(bucket[f] is not None for f in POWER_FIELDS) else "missing"
        buckets.append(bucket)
        left = right
    complete = bool(buckets) and all(b["quality"] in {"measured", "pending"} for b in buckets)
    data = {"plant_code": plant_code, "from_at": start, "to_at": end, "time_zone": time_zone,
            "aggregation": aggregation, "resolution_minutes": resolution_minutes,
            "expected_interval_seconds": config.EXPECTED_INTERVAL_SECONDS,
            "complete": complete, "truncated": False, "buckets": buckets}
    return {"success": True, "data": data, "meta": fleet_service.response_meta(data,
            scope="plant", time_zone=time_zone, is_partial=not complete, returned=len(buckets),
            coverage=bucket_coverage(buckets), filters_applied={"plant_code": plant_code},
            warnings=["Daya bersumber dari plant_realtime; waktu collector dan tanda daya yang belum terverifikasi tidak dipakai. Rata-rata berbasis sampel, tanpa interpolasi."])}


def inverter_report(plant_code, device_id, start, end, grain, time_zone, page, page_size):
    start, end, zone = statistics_service.validate_range(start, end, grain, time_zone)
    if device_id is not None and plant_code is None:
        raise ApiException(422, "DEVICE_SCOPE_REQUIRED", "Filter device_id memerlukan plant_code.")
    plants = fleet_service.get_rows(plant_code)
    if plant_code is not None:
        fleet_service.require_plant(plants)
    codes = [p["plant_code"] for p in plants if not p.get("is_demo")]
    devices = repository.inverters(codes)
    if device_id is not None:
        devices = [d for d in devices if d["device_id"] == device_id]
        if not devices:
            raise ApiException(404, "INVERTER_NOT_FOUND", "Inverter tidak ditemukan pada PLTS tersebut.")
    points = statistics_service.boundaries(start, end, grain, zone)
    if len(devices) * (len(points) - 1) > 50_000:
        raise ApiException(422, "REPORT_TOO_LARGE", "Laporan melebihi 50000 baris; persempit rentang atau filter PLTS/perangkat.")
    raw = repository.device_counter_samples([d["id"] for d in devices], start, end)
    check_source_limit(raw)
    samples = defaultdict(list)
    for row in raw:
        quality = (row.get("field_quality") or {}).get("total_yield_kwh")
        samples[row["device_id"]].append({**row, "plant_code": str(row["device_id"]),
            "data_source": "FUSIONSOLAR_DEVICE",
            "total_yield_kwh": row.get("total_yield_kwh") if quality in {None, "VALID"} else None,
            "timestamp_source": "device" if row.get("measurement_time_verified") is True else "collector"})
    now, rows = utc_now(), []
    for device in devices:
        series = statistics_service.interval_statistics([str(device["id"])], start, end, grain, time_zone,
            samples=samples[device["id"]], now=now, plant_code=device["plant_code"])
        for bucket in series["buckets"]:
            rows.append({"plant_code": device["plant_code"], "device_key": device["device_key"],
                "device_id": device["device_id"], "device_name": device.get("device_name"),
                "device_sn": device.get("device_sn"), **{key: bucket[key] for key in
                ("start_at", "end_at", "label", "label_at", "quality", "reason", "received_samples", "expected_samples", "coverage")},
                "inverter_yield_kwh": bucket["yield_kwh"]})
    rows.sort(key=lambda row: (row["start_at"], row["plant_code"], row["device_key"]))
    coverage = bucket_coverage(rows)
    values = [r["inverter_yield_kwh"] for r in rows if r["inverter_yield_kwh"] is not None]
    complete = bool(devices) and coverage["missing_buckets"] == coverage["partial_buckets"] == 0
    totals = {"inverter_yield_kwh": sum(values) if values else None, "total_rows": len(rows),
        "complete_rows": coverage["complete_buckets"], "partial_rows": coverage["partial_buckets"],
        "missing_rows": coverage["missing_buckets"], "pending_rows": coverage["pending_buckets"]}
    offset = (page - 1) * page_size
    warnings = ["Energi berasal dari selisih counter total inverter terverifikasi; bukan yield PLTS atau integral daya. Gap/reset tidak diinterpolasi. Total mencakup seluruh hasil filter sebelum pagination."]
    if not raw:
        warnings.append("Riwayat telemetry inverter belum tersedia; inventory saja tidak membuktikan adanya pengukuran.")
    return {"success": True, "total": len(rows), "data": rows[offset:offset + page_size],
        "from_at": start, "to_at": end, "time_zone": time_zone, "grain": grain, "complete": complete, "totals": totals,
        "meta": fleet_service.response_meta(rows, is_partial=not complete, scope="plant" if plant_code else "all_configured_plants",
            page=page, page_size=page_size, total_pages=math.ceil(len(rows) / page_size),
            returned=len(rows[offset:offset + page_size]), time_zone=time_zone, coverage=coverage,
            reason="DEVICE_TELEMETRY_UNAVAILABLE" if not raw else None,
            filters_applied={k: v for k, v in {"plant_code": plant_code, "device_id": device_id}.items() if v is not None},
            warnings=warnings)}


def device_history(plant_code, device_id, start, end, time_zone, page, page_size):
    start, end, _ = statistics_service.validate_range(start, end, "hour", time_zone)
    fleet_service.require_plant(fleet_service.get_rows(plant_code))
    device = repository.device(plant_code, device_id)
    if device is None:
        raise ApiException(404, "DEVICE_NOT_FOUND", "Perangkat tidak ditemukan pada PLTS tersebut.")
    rows, total = repository.device_history(device["id"], start, end, page, page_size)
    now = utc_now()
    result = []
    for raw in rows:
        row = finite_json({k: v for k, v in raw.items() if k != "total"})
        measured = aware_time(row.get("measured_at"))
        verified = row.get("measurement_time_verified") is True and measured is not None and measured <= now
        row["measured_at"] = measured if verified else None
        row["measurement_time_verified"] = verified
        quality = dict(row.get("field_quality") or {})
        for field in (*POWER_FIELDS, "active_power_kw", "pv_input_power_kw", "battery_soc_percent", "total_yield_kwh", "yield_today_kwh"):
            value = number(row.get(field))
            if field in SIGNED_FIELDS and row.get("power_direction_convention") != VERIFIED_DIRECTION:
                if value is not None:
                    quality[field] = "POWER_DIRECTION_UNVERIFIED"
                value = None
            if not verified:
                quality[field] = "TIME_UNVERIFIED"
                value = None
            elif quality.get(field) not in {None, "VALID"}:
                value = None
            row[field] = value
        row["field_quality"] = quality
        row["power_direction_convention"] = row.get("power_direction_convention") or "unverified"
        row["status"] = row.get("status") or "unknown"
        result.append(row)
    verified = bool(result) and all(r["measurement_time_verified"] for r in result)
    return {"success": True, "total": total, "data": result, "plant_code": plant_code, "device_id": device_id,
        "device_key": device["device_key"], "from_at": start, "to_at": end, "time_zone": time_zone,
        "measurement_time_verified": verified,
        "units": {"power": "kW", "energy": "kWh", "battery_soc": "%"},
        "meta": fleet_service.response_meta(result, is_partial=not verified,
            page=page, page_size=page_size, total_pages=math.ceil(total / page_size), returned=len(result),
            scope="device", time_zone=time_zone, filters_applied={"plant_code": plant_code, "device_id": device_id},
            reason="DEVICE_TELEMETRY_UNAVAILABLE" if total == 0 else None,
            warnings=["Pagination eksplisit, urutan measured_at/id menaik; waktu collector tidak dianggap waktu pengukuran. Rentang pencarian memakai timestamp storage, yang dapat belum terverifikasi."])}
