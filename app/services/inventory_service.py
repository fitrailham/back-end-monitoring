"""Normalize vendor inventory without guessing ratings from model names."""
from app import config
from app.services.fusionsolar_client import complete_list, extract_list
from app.services.values import aware_time, finite_json, number


def coordinate_pair(latitude, longitude):
    lat, lon = number(latitude), number(longitude)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None  # Vendor sentinel is never an automatically accepted location.
    return lat, lon


def whole_number(value):
    n = number(value)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def devices(payload, plant_code):
    result = []
    for raw in extract_list(payload or {}):
        if str(raw.get("stationCode", plant_code)) != plant_code:
            continue
        kind = str(raw.get("devTypeId", ""))
        pair = coordinate_pair(raw.get("latitude"), raw.get("longitude"))
        device_id = raw.get("devDn") or raw.get("id") or raw.get("device_id")
        serial = raw.get("esnCode", raw.get("device_sn"))
        dtype = config.DEVICE_TYPE_MAP.get(kind, "unknown")
        if dtype not in ("inverter", "ess", "meter", "logger", "dongle", "emi", "unknown"):
            dtype = "unknown"
        # Keep original vendor aliases for existing consumers.
        legacy = finite_json(dict(raw))
        for key in ("id", "devDn", "esnCode", "stationCode"):
            if key in legacy and legacy[key] is not None:
                legacy[key] = str(legacy[key])
        result.append({
            **legacy,
            "device_id": str(device_id) if device_id is not None else None,
            "device_sn": str(serial) if serial is not None else None,
            "device_name": raw.get("devName", raw.get("device_name")),
            "plant_code": plant_code,
            "vendor_device_type_id": int(kind) if kind.isdigit() else kind or None,
            "model": str(raw["model"]) if raw.get("model") is not None else None,
            "device_type": dtype,
            "optimizer_quantity": whole_number(raw.get("optimizerNumber")) if dtype == "inverter" else None,
            "latitude": pair[0] if pair else None,
            "longitude": pair[1] if pair else None,
            # Only explicitly unit-qualified normalized fields are accepted.
            "rated_ac_power_kw": number(raw.get("rated_ac_power_kw")),
            "rated_energy_kwh": number(raw.get("rated_energy_kwh")),
            "status": "unknown", "last_seen_at": None,
        })
    return result


def metadata(row):
    master = row.get("metadata") or {}
    inventory = devices(row.get("inventory_payload"), row["plant_code"])
    payload = row.get("inventory_payload")
    complete = complete_list(payload)
    # Pagination/explicit incompleteness must never prove equipment absence.
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        total = number(data.get("total"))
        if total is not None and total > len(inventory):
            complete = False
    complete = complete and not any(d["device_type"] == "unknown" for d in inventory)
    inverters = [d for d in inventory if d["device_type"] == "inverter"]
    ess = [d for d in inventory if d["device_type"] == "ess"]
    pairs = {(d["latitude"], d["longitude"]) for d in inventory if d["latitude"] is not None}
    explicit = coordinate_pair(master.get("latitude"), master.get("longitude"))
    verified = explicit is not None and master.get("location_verified") is True
    selected = explicit if verified or explicit else next(iter(pairs)) if len(pairs) == 1 else None
    conflict = not verified and len(pairs) > 1
    if conflict and explicit is None:
        selected = None
    capacity = number(row.get("capacity_kwp"))
    issues = []
    if capacity is None or capacity <= 0:
        issues.append("CAPACITY_MISSING_OR_NON_POSITIVE")
    if conflict:
        issues.append("LOCATION_CONFLICT")
    if not master.get("time_zone"):
        issues.append("TIME_ZONE_UNCONFIGURED")

    def rating(items, field):
        values = [d[field] for d in items]
        if complete and not items:
            return 0.0
        return sum(values) if values and complete and all(v is not None and v >= 0 for v in values) else None

    optimizers = [d["optimizer_quantity"] for d in inverters]
    optimizer_complete = complete and bool(inverters) and all(v is not None for v in optimizers)
    return {
        "plant_code": row["plant_code"], "plant_name": row["plant_name"],
        "capacity_kwp": capacity, "address": row.get("address"), "is_demo": bool(row.get("is_demo")),
        **{k: master.get(k) for k in ("country_code", "country_name", "region", "city", "time_zone",
                                     "image_url", "grid_connected_on")},
        "latitude": selected[0] if selected else None, "longitude": selected[1] if selected else None,
        "location_source": "plant_metadata" if explicit else "device_inventory" if selected else None,
        "location_verified": verified, "location_conflict": conflict,
        "has_pv": master.get("has_pv", True if capacity is not None and capacity > 0 or inverters else False if complete else None),
        "has_ess": master.get("has_ess", True if ess else False if complete else None),
        "ess_quantity": len(ess) if ess or complete else None,
        "ess_rated_capacity_kwh": number(master.get("ess_rated_capacity_kwh")) if master.get("ess_rated_capacity_kwh") is not None else rating(ess, "rated_energy_kwh"),
        "inverter_rated_power_kw": number(master.get("inverter_rated_power_kw")) if master.get("inverter_rated_power_kw") is not None else rating(inverters, "rated_ac_power_kw"),
        "optimizer_quantity": sum(v for v in optimizers if v is not None) if optimizer_complete else None,
        "optimizer_quantity_complete": optimizer_complete,
        "device_types": sorted({d["device_type"] for d in inventory}),
        "device_serial_numbers": sorted({d["device_sn"] for d in inventory if d["device_sn"] is not None}),
        "device_count": len(inventory) if payload is not None else None,
        "inventory_collected_at": aware_time(row.get("inventory_collected_at")),
        "inventory_complete": complete, "metadata_issues": issues,
    }
