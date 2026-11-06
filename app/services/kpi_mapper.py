
from datetime import datetime, timezone

from app.services.fusionsolar_client import extract_list


def _number(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(mapping: dict, *keys):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return _number(mapping[key])

    return None


# ==========================================
# MASTER PLANT MAPPER
# ==========================================

def map_station(row: dict) -> dict:

    code = str(
        row.get("stationCode")
        or row.get("plantCode")
        or ""
    ).strip()

    if not code:
        raise ValueError(
            "stationCode tidak ditemukan"
        )

    # Huawei station list: capacity dalam MW
    capacity_mw = _first(row, "capacity")

    return {
        "plant_code": code,
        "plant_name": str(
            row.get("stationName") or code
        ),
        "capacity_kwp": (
            capacity_mw * 1000
            if capacity_mw is not None
            else None
        ),
        "address": row.get("stationAddr")
    }


# ==========================================
# REALTIME KPI MAPPER
# ==========================================

def map_realtime(
    payload: dict,
    plant_code: str
) -> dict:

    items = extract_list(payload)

    matching = [
        item for item in items
        if str(
            item.get("stationCode", plant_code)
        ) == plant_code
    ]

    if not matching:
        raise ValueError(
            f"Tidak ada KPI untuk plant {plant_code}"
        )

    row = matching[0]

    m = (
        row.get("dataItemMap")
        or row.get("kpis")
        or {}
    )

    if not isinstance(m, dict):
        raise ValueError(
            "KPI dataItemMap bukan object"
        )

    mapped = {
        "pv_power_kw": _first(
            m, "realTimePower", "pv_power_kw"
        ),

        "load_power_kw": _first(
            m, "load_power_kw"
        ),

        "grid_power_kw": _first(
            m, "grid_power_kw"
        ),

        "yield_today_kwh": _first(
            m, "day_power", "yield_today_kwh"
        ),

        "total_yield_kwh": _first(
            m, "total_power", "total_yield_kwh"
        ),

        "battery_power_kw": _first(
            m, "battery_power_kw"
        ),

        "battery_soc_percent": _first(
            m, "battery_soc_percent"
        ),

        "ess_charge_today_kwh": _first(
            m, "ess_charge_today_kwh"
        ),

        "ess_discharge_today_kwh": _first(
            m, "ess_discharge_today_kwh"
        )
    }

    if all(
        value is None
        for value in mapped.values()
    ):
        raise ValueError(
            "KPI tidak memiliki field yang dikenali. "
            "Periksa mapping Northbound."
        )

    # Gunakan waktu pengukuran jika tersedia.
    raw_time = (
        row.get("collectTime")
        or m.get("collectTime")
    )

    timestamp_source = "device"

    try:
        raw_time = float(raw_time)

        if raw_time <= 0:
            raise ValueError(
                "timestamp must be positive"
            )

        timestamp = datetime.fromtimestamp(
            raw_time / (
                1000 if raw_time > 1e11 else 1
            ),
            tz=timezone.utc
        )

    except (
        TypeError,
        ValueError,
        OverflowError,
        OSError
    ):
        timestamp = datetime.now(
            timezone.utc
        )
        timestamp_source = "collector"

    return {
        "plant_code": plant_code,
        "recorded_at": timestamp,
        **mapped,
        "data_source": "FUSIONSOLAR",
        "raw_data": {
            "sampling_time_source": timestamp_source,
            "response": payload
        }
    }
