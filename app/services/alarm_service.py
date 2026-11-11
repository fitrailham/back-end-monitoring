from datetime import datetime, timezone

from app import config
from app.services.fusionsolar_client import complete_list, extract_list
from app.services.fleet_service import response_meta
from app.services.values import aware_time, finite_json, number, utc_now

SEVERITIES = ("critical", "major", "minor", "warning", "unclassified")


def alarm_time(value):
    parsed = aware_time(value)
    if parsed:
        return parsed
    value = number(value)
    if value is not None and value > 0:
        try:
            return datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    return None


def normalize_alarm(raw, plant_code):
    severity = str(raw.get("severity", raw.get("lev", ""))).lower()
    severity = {"1": "critical", "2": "major", "3": "minor", "4": "warning"}.get(severity, severity)
    if severity not in SEVERITIES:
        severity = "unclassified"
    state = raw.get("state")
    if state not in ("active", "cleared"):
        state = "active" if str(raw.get("status")) == "1" else None
    cleared_at = alarm_time(raw.get("cleared_at", raw.get("clearTime")))
    if cleared_at:
        state = "cleared"
    serial = raw.get("device_sn", raw.get("esnCode"))
    occurred_at = alarm_time(raw.get("occurred_at", raw.get("raiseTime")))
    vendor_id = raw.get("vendor_alarm_id", raw.get("alarmId"))
    device_id = raw.get("device_id", raw.get("devDn", raw.get("devId")))
    # Vendor alarmId is an alarm code, not necessarily a unique occurrence ID.
    alarm_id = raw.get("alarm_id")
    if alarm_id is None and vendor_id is not None:
        alarm_id = ":".join(str(v) for v in (plant_code, serial or device_id or "unknown", vendor_id, raw.get("causeId", ""), occurred_at or "unknown"))
    acknowledged = raw.get("acknowledged")
    if not isinstance(acknowledged, bool):
        acknowledged = None
    return {**finite_json(raw), "alarm_id": str(alarm_id) if alarm_id is not None else None,
            "vendor_alarm_id": str(vendor_id) if vendor_id is not None else None,
            "plant_code": plant_code, "device_id": str(device_id) if device_id is not None else None,
            "device_sn": str(serial) if serial is not None else None,
            "severity": severity, "state": state, "acknowledged": acknowledged,
            "name": raw.get("name", raw.get("alarmName")),
            "description": raw.get("description", raw.get("alarmCause")),
            "occurred_at": occurred_at, "cleared_at": cleared_at}


def fleet_alarms(raw_rows, severity=None, state=None, page=None, page_size=None, plant_code=None):
    now = utc_now()
    # An empty filtered scope still reports whether the source itself is configured.
    configured = bool(config.FUSIONSOLAR_ALARMS_PATH) if not raw_rows else False
    coverage = dict(expected_plants=len(raw_rows), configured_plants=0,
                    unconfigured_plants=0, failed_plants=0,
                    missing_plants=0, stale_plants=0, valid_plants=0, complete=False)
    alarms, times = [], []
    unknown_states = False
    for row in raw_rows:
        row_configured = bool(config.FUSIONSOLAR_ALARMS_PATH) or row.get("alarm_configured") is True
        if not row_configured:
            coverage["unconfigured_plants"] += 1
            continue
        configured = True
        coverage["configured_plants"] += 1
        normalized = row.get("normalized_alarms")
        if normalized is not None or row.get("alarm_source_status") is not None:
            items = normalized or []
            collected = aware_time(row.get("alarm_last_success_at"))
            failed = row.get("alarm_source_status") == "failed"
            source_complete = row.get("alarm_source_complete") is True
        else:
            payload = row.get("alarms_payload")
            items = extract_list(payload) if complete_list(payload) else None
            collected = aware_time(row.get("alarms_collected_at"))
            job_started = aware_time(row.get("alarm_job_started_at"))
            failed = row.get("alarm_job_status") == "FAILED" and (not collected or not job_started or job_started >= collected)
            source_complete = complete_list(payload)
        if failed:
            coverage["failed_plants"] += 1
        if not source_complete or collected is None or items is None:
            coverage["missing_plants"] += 1
            continue
        times.append(collected)
        stale = (now - collected).total_seconds() > config.STALE_AFTER_MINUTES * 60
        if stale:
            coverage["stale_plants"] += 1
        elif not failed:
            coverage["valid_plants"] += 1
        for raw in items:
            if str(raw.get("stationCode", row["plant_code"])) != row["plant_code"]:
                continue
            alarm = normalize_alarm(raw, row["plant_code"])
            alarm["plant_name"] = row.get("plant_name")
            unknown_states = unknown_states or alarm["state"] is None
            if severity and alarm["severity"] != severity or state and alarm["state"] != state:
                continue
            alarms.append(alarm)
    coverage["complete"] = configured and coverage["configured_plants"] == len(raw_rows) \
        and coverage["valid_plants"] == len(raw_rows) and not unknown_states
    quality = "missing" if not configured or not times else "partial" if not coverage["complete"] else "fresh"
    if times and coverage["stale_plants"] == len(raw_rows):
        quality = "stale"
    alarms.sort(key=lambda a: (str(a["occurred_at"] or ""), a["alarm_id"] or ""), reverse=True)
    # Deduplicate identical occurrences, preserving alarms with unknown identity.
    seen, unique = set(), []
    for alarm in alarms:
        key = alarm["alarm_id"]
        if key is None or key not in seen:
            unique.append(alarm)
        if key is not None:
            seen.add(key)
    alarms = unique
    total = len(alarms) if times else (0 if configured and not raw_rows else None)
    filters = {k: v for k, v in {"plant_code": plant_code, "severity": severity, "state": state}.items() if v is not None}
    meta = response_meta(alarms, now, is_partial=not coverage["complete"], coverage=coverage,
                         scope="plant" if plant_code else "all_configured_plants_filtered" if filters else "all_configured_plants",
                         filters_applied=filters, reason="ALARM_SOURCE_NOT_CONFIGURED" if not configured else None)
    if page is not None or page_size is not None:
        import math
        page, page_size = page or 1, page_size or 10
        meta.update(page=page, page_size=page_size, total_pages=math.ceil((total or 0) / page_size))
        alarms = alarms[(page - 1) * page_size:page * page_size]
    meta["returned"] = len(alarms)
    return {"success": True, "plantCode": plant_code, "plant_code": plant_code, "configured": configured,
            "collectedAt": min(times) if times else None, "collected_at": min(times) if times else None,
            "total": total, "data": alarms, "quality": quality, "coverage": coverage, "meta": meta}


def active_summary(raw_rows):
    response = fleet_alarms(raw_rows, state="active")
    known = response["total"] is not None
    return {"configured": response["configured"], "total": response["total"],
            "severity": {s: sum(a["severity"] == s for a in response["data"]) if known else None for s in SEVERITIES},
            "coverage": response["coverage"]}
