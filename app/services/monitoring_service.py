from datetime import datetime, timezone

from app import config
from app.repositories import realtime_repository


def all_overviews():
    return realtime_repository.latest_per_plant()


def history(code: str, limit: int):
    return realtime_repository.history(code, limit)


def to_mwh(value):
    if value is None:
        return None

    return round(float(value) / 1000, 3)


def format_dashboard(row: dict):

    recorded = row.get("recorded_at")

    is_demo = (
        bool(row.get("is_demo"))
        or row.get("data_source") == "DEMO"
    )

    uncertain_time = (
        row.get("timestamp_source") == "collector"
    )

    stale = (
        recorded is None
        or (
            datetime.now(timezone.utc)
            - recorded.astimezone(timezone.utc)
        ).total_seconds()
        > config.STALE_AFTER_MINUTES * 60
    )

    if is_demo:
        message = "Data DEMO"

    elif uncertain_time:
        message = (
            "Timestamp dari collector; waktu pengukuran "
            "belum diverifikasi"
        )

    elif stale:
        message = "Data belum terbaru"

    else:
        message = ""

    return {
        "success": True,
        "stale": bool(
            stale or is_demo or uncertain_time
        ),
        "message": message,
        "recordedAt": recorded,

        "data": {
            "plantCode": row["plant_code"],
            "plantName": row["plant_name"],

            "pvCapacity": row.get("capacity_kwp"),
            "currentPvPower": row.get("pv_power_kw"),

            "yieldToday": to_mwh(
                row.get("yield_today_kwh")
            ),
            "totalYield": to_mwh(
                row.get("total_yield_kwh")
            ),

            "loadPower": row.get("load_power_kw"),
            "gridPower": row.get("grid_power_kw"),

            "batteryPower": row.get("battery_power_kw"),
            "batterySoc": row.get(
                "battery_soc_percent"
            ),

            "essChargeToday": row.get(
                "ess_charge_today_kwh"
            ),
            "essDischargeToday": row.get(
                "ess_discharge_today_kwh"
            )
        }
    }