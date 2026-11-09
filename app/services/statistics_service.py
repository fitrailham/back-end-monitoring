"""Interval energy from verified lifetime counters; no interpolation or daily sums."""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math

from app import config
from app.errors import ApiException
from app.repositories import fleet_repository
from app.services.values import aware_time, number, utc_now, valid_zone


def boundaries(start, end, grain, zone):
    local = start.astimezone(zone)
    if grain == "hour":
        cursor = local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    elif grain == "day":
        cursor = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif grain == "month":
        cursor = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        cursor = local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    result = [start]
    while True:
        if grain == "hour":
            cursor += timedelta(hours=1)  # UTC stepping preserves repeated DST hours.
        elif grain == "day":
            cursor += timedelta(days=1)
        elif grain == "month":
            cursor = cursor.replace(year=cursor.year + cursor.month // 12, month=cursor.month % 12 + 1)
        else:
            cursor = cursor.replace(year=cursor.year + 1)
        point = cursor.astimezone(timezone.utc)
        if point >= end:
            break
        if point > result[-1]:
            result.append(point)
        if len(result) > 1000:
            raise ApiException(422, "RANGE_TOO_LARGE", "Rentang melebihi 1000 bucket. Gunakan grain yang lebih besar.")
    result.append(end)
    return result


def validate_range(start, end, grain, time_zone):
    start, end = aware_time(start), aware_time(end)
    zone = valid_zone(time_zone)
    if start is None or end is None or end <= start or zone is None:
        raise ApiException(422, "INVALID_TIME_RANGE", "Gunakan timestamp dengan offset, timezone IANA valid, dan to lebih besar dari from.")
    if start.year < 1970 or end.year > 2200:
        raise ApiException(422, "INVALID_TIME_RANGE", "Tahun laporan harus antara 1970 dan 2200.")
    max_days = {"hour": 31, "day": 366, "month": 3660, "year": 36600}[grain]
    if end - start > timedelta(days=max_days):
        raise ApiException(422, "RANGE_TOO_LARGE", "Rentang terlalu besar untuk grain yang dipilih.")
    return start, end, zone


def interval_statistics(codes, start, end, grain, time_zone, samples=None, now=None, plant_code=None):
    start, end, zone = validate_range(start, end, grain, time_zone)
    now = now or utc_now()
    points = boundaries(start, end, grain, zone)
    samples = fleet_repository.counter_samples(codes, start, end) if samples is None else samples
    by_plant = defaultdict(dict)
    unverified = set()
    for sample in samples:
        code = sample["plant_code"]
        if code not in codes:
            continue
        measured = aware_time(sample.get("recorded_at"))
        if sample.get("timestamp_source") != "device" or sample.get("data_source") == "DEMO":
            unverified.add(code)
            continue
        if measured is None or measured > now or measured > end:
            continue
        value = number(sample.get("total_yield_kwh"))
        if value is not None and value >= 0:
            by_plant[code][measured] = value  # Last id wins for duplicate timestamps.
    counters = [defaultdict(lambda: {"energy": 0.0, "seconds": 0.0, "received": 0, "issues": set()}) for _ in points[:-1]]
    for code, readings in by_plant.items():
        readings = sorted(readings.items())
        for measured, value in readings:
            index = bisect_right(points, measured) - 1
            if 0 <= index < len(counters):
                counters[index][code]["received"] += 1
        for (before, baseline), (after, value) in zip(readings, readings[1:]):
            index = bisect_right(points, before) - 1
            if not 0 <= index < len(counters):
                continue
            record = counters[index][code]
            if after > points[index + 1]:
                record["issues"].add("BUCKET_BOUNDARY_NOT_MEASURED")
                continue
            if value < baseline:
                record["issues"].add("COUNTER_RESET")
                continue
            seconds = (after - before).total_seconds()
            if seconds > config.EXPECTED_INTERVAL_SECONDS * 1.5:
                record["issues"].add("SAMPLE_GAP")
                continue
            record["energy"] += value - baseline
            record["seconds"] += seconds
    buckets = []
    for index, (left, right) in enumerate(zip(points, points[1:])):
        local = left.astimezone(zone)
        label = local.strftime({"hour": "%H:%M", "day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[grain])
        duration = max(0.0, (min(right, now) - left).total_seconds())
        expected = math.ceil(duration / config.EXPECTED_INTERVAL_SECONDS) * len(codes)
        values, received, reasons = [], 0, set()
        coverage = dict(expected=len(codes), valid=0, missing=0, stale=0, unverified=0, not_applicable=0)
        if left >= now:
            buckets.append({"start_at": left, "end_at": right,
                            "label": label, "label_at": local.isoformat(), "yield_kwh": None,
                            "quality": "pending", "received_samples": 0, "expected_samples": 0,
                            "reason": None, "coverage": {**coverage, "expected": 0, "partial": False}})
            continue
        for code in codes:
            record = counters[index].get(code)
            if record:
                received += record["received"]
                reasons.update(record["issues"])
            if record and record["seconds"] > 0:
                values.append(record["energy"])
                if record["seconds"] >= duration and not record["issues"]:
                    coverage["valid"] += 1
                else:
                    coverage["missing"] += 1
                    reasons.add("INCOMPLETE_INTERVAL")
            elif code in unverified:
                coverage["unverified"] += 1
                reasons.add("TIME_UNVERIFIED")
            else:
                coverage["missing"] += 1
                reasons.add("NO_VERIFIED_COUNTER_BASELINE")
        pending = left >= now
        complete = bool(codes) and coverage["valid"] == len(codes) and right <= now
        coverage["partial"] = not complete and not pending
        quality = "pending" if pending else "derived" if complete else "partial" if values else "missing"
        buckets.append({"start_at": left, "end_at": right,
                        "label": label, "label_at": local.isoformat(),
                        "yield_kwh": sum(values) if values and not pending else None,
                        "quality": quality, "received_samples": received,
                        "expected_samples": expected, "reason": ";".join(sorted(reasons)) if reasons and not pending else None,
                        "coverage": coverage})
    known_energy = [b["yield_kwh"] for b in buckets if b["yield_kwh"] is not None]
    return {"aggregation": "interval_energy", "unit": "kWh",
            "scope": "plant" if plant_code is not None else "portfolio", "plant_code": plant_code,
            "metric": "pv_generation_energy", "total_yield_kwh": sum(known_energy) if known_energy else None,
            "method": "verified_total_counter_difference", "grain": grain, "time_zone": time_zone,
            "from_at": start, "to_at": end, "complete": bool(buckets) and all(b["quality"] in ("derived", "pending") for b in buckets),
            "truncated": False, "buckets": buckets}
