from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from app.errors import ApiException
from app.repositories import fleet_repository
from app.services import alarm_service, fleet_service, statistics_service
from app.services.values import utc_now, valid_zone


def summary(business_date=None, time_zone="Asia/Jakarta", **filters):
    if valid_zone(time_zone) is None:
        raise ApiException(422, "INVALID_TIME_ZONE", "Gunakan timezone IANA yang valid.")
    raw = fleet_service.get_rows()
    rows = fleet_service.normalized_rows(raw, **filters)
    codes = {r["plant_code"] for r in rows}
    raw = [r for r in raw if r["plant_code"] in codes]
    mapping = {"current_power_kw": "pv_power_kw", "yield_today_kwh": "yield_today_kwh",
               "total_yield_kwh": "total_yield_kwh", "installed_pv_capacity_kwp": "capacity_kwp",
               "inverter_rated_power_kw": "inverter_rated_power_kw", "ess_rated_capacity_kwh": "ess_rated_capacity_kwh",
               "revenue_today_idr": "revenue_today_idr"}
    kpis, coverage = {}, {}
    for output, field in mapping.items():
        kpis[output], coverage[output] = fleet_service.metric_total(rows, field, business_date, time_zone)
        coverage[field] = coverage[output]
    kpis["currency"] = "IDR"
    alarms = alarm_service.active_summary(raw)
    statuses = {status: sum(r["status"] == status for r in rows) for status in ("normal", "faulty", "disconnected", "unknown")}
    data = {"total_plants": len(rows), "kpis": kpis, "plant_status": statuses,
            "active_alarms": alarms, "coverage": coverage,
            "environmental_benefits": {"status": "unconfigured"}, "revenue": {"status": "unconfigured"}}
    meta = fleet_service.response_meta(data,
        is_partial=any(c["partial"] for c in coverage.values()) or not alarms["coverage"]["complete"],
        date=business_date, time_zone=time_zone,
        scope="all_configured_plants_filtered" if any(v is not None for v in filters.values()) else "all_configured_plants",
        filters_applied={k: v for k, v in filters.items() if v is not None},
        date_basis="same_plant_and_request_timezone_only" if business_date else "sum_of_verified_plant_local_days",
        warnings=["Revenue, environmental factors and PR are not configured.",
                  "Daily counters cannot be rebased across timezones; mismatching dates/zones are excluded when date is supplied."])
    return {"success": True, "data": data, "meta": meta}


def rankings(business_date, time_zone, limit, metric):
    zone = valid_zone(time_zone)
    if zone is None:
        raise ApiException(422, "INVALID_TIME_ZONE", "Gunakan timezone IANA yang valid.")
    now = utc_now()
    start = datetime.combine(business_date, time.min, zone)
    end = start + timedelta(days=1)
    rows = fleet_service.normalized_rows()
    codes = [r["plant_code"] for r in rows]
    samples = fleet_repository.counter_samples(codes, start, min(end, now)) if start < now else []
    grouped = defaultdict(list)
    for sample in samples:
        grouped[sample["plant_code"]].append(sample)
    eligible, reasons = [], Counter()
    for row in rows:
        capacity = row["capacity_kwp"]
        if row["is_demo"]:
            reasons["DEMO_DATA"] += 1
            continue
        if capacity is None or capacity <= 0:
            reasons["CAPACITY_MISSING_OR_NON_POSITIVE"] += 1
            continue
        if start >= now:
            reasons["PERIOD_PENDING"] += 1
            continue
        data = statistics_service.interval_statistics([row["plant_code"]], start, min(end, now), "day", time_zone,
                                                       grouped[row["plant_code"]], now)
        bucket = data["buckets"][0]
        if bucket["quality"] != "derived" or bucket["yield_kwh"] is None:
            reason = "TIME_UNVERIFIED" if bucket["coverage"]["unverified"] else "INCOMPLETE_PERIOD"
            reasons[reason] += 1
            continue
        energy = bucket["yield_kwh"]
        eligible.append({"plant_code": row["plant_code"], "plant_name": row["plant_name"],
                         "yield_kwh": energy, "capacity_kwp": capacity,
                         "specific_energy_kwh_per_kwp": energy / capacity, "quality": "derived"})
    field = "specific_energy_kwh_per_kwp" if metric == "specific_energy" else "yield_kwh"
    eligible.sort(key=lambda r: (-r[field], r["plant_code"]))
    for index, row in enumerate(eligible, 1):
        row["rank"] = index
    frontend_reasons = {"missing_capacity": reasons["CAPACITY_MISSING_OR_NON_POSITIVE"],
                        "missing_yield": reasons["INCOMPLETE_PERIOD"], "stale_data": reasons["STALE_DATA"],
                        "unverified_data": reasons["TIME_UNVERIFIED"], "demo_data": reasons["DEMO_DATA"],
                        "period_pending": reasons["PERIOD_PENDING"]}
    return {"success": True, "total": len(eligible), "data": eligible[:limit],
            "eligible_plants": len(eligible), "excluded_plants": sum(reasons.values()),
            "exclusion_reasons": dict(reasons),
            "meta": fleet_service.response_meta(eligible, date=business_date, time_zone=time_zone,
                ranking_date=business_date, eligible_plants=len(eligible), excluded_plants=sum(reasons.values()),
                excluded_reasons=frontend_reasons, returned=len(eligible[:limit]),
                date_basis="verified_interval_energy", is_partial=bool(reasons),
                warnings=["Only complete, verified intervals are ranked; no capacity estimates or interpolation."])}
