
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app import config
from app.database import fetch_rows

logger = logging.getLogger(__name__)

router = APIRouter()


# ========================================
# SQL: LATEST RECORD PER PLANT
# ========================================

OVERVIEW_SQL = """
SELECT
    p.plant_code,
    p.plant_name,
    p.capacity_kwp::float8 AS capacity_kwp,
    p.is_demo,

    r.recorded_at,
    r.pv_power_kw::float8 AS pv_power_kw,
    r.load_power_kw::float8 AS load_power_kw,
    r.grid_power_kw::float8 AS grid_power_kw,

    r.yield_today_kwh::float8 AS yield_today_kwh,
    r.total_yield_kwh::float8 AS total_yield_kwh,

    r.battery_power_kw::float8 AS battery_power_kw,
    r.battery_soc_percent::float8 AS battery_soc_percent,

    r.ess_charge_today_kwh::float8 AS ess_charge_today_kwh,
    r.ess_discharge_today_kwh::float8 AS ess_discharge_today_kwh,

    r.data_source

FROM public.plants p

LEFT JOIN LATERAL (
    SELECT pr.*
    FROM public.plant_realtime pr
    WHERE pr.plant_code = p.plant_code
    ORDER BY pr.recorded_at DESC, pr.id DESC
    LIMIT 1
) r ON TRUE
"""


def query_or_503(sql: str, params: tuple = ()):
    try:
        return fetch_rows(sql, params)

    except Exception:
        logger.exception("[API] Database query failed")

        raise HTTPException(
            status_code=503,
            detail="Database belum dapat diakses. Periksa SSH Tunnel dan .env."
        ) from None


# ========================================
# 1. DATABASE TEST
# ========================================

@router.get("/api/database/test")
def test_database():

    logger.info("[API] GET /api/database/test")

    rows = query_or_503("""
        SELECT
            current_database() AS database_name,
            current_user AS db_user,
            current_setting('server_version') AS postgres_version,
            NOW() AS server_time
    """)

    return {
        "success": True,
        "data": rows[0]
    }


# ========================================
# 2. GET ALL PLANTS
# ========================================

@router.get("/api/database/plants")
def list_plants():

    logger.info("[API] GET /api/database/plants")

    rows = query_or_503("""
        SELECT
            plant_code,
            plant_name,
            capacity_kwp::float8 AS capacity_kwp,
            address,
            is_demo
        FROM public.plants
        ORDER BY plant_name
    """)

    return {
        "success": True,
        "total": len(rows),
        "data": rows
    }


# ========================================
# 3. PLANT OVERVIEW
# ========================================

@router.get("/api/database/overview")
def overview():

    logger.info("[API] GET /api/database/overview")

    rows = query_or_503(
        OVERVIEW_SQL + " ORDER BY p.plant_name"
    )

    return {
        "success": True,
        "source": "PostgreSQL",
        "total": len(rows),
        "data": rows
    }


# ========================================
# 4. HISTORICAL MONITORING
# ========================================

@router.get("/api/database/history/{plant_code}")
def history(
    plant_code: str,
    limit: int = Query(default=100, ge=1, le=500)
):

    logger.info(
        "[API] GET history for plant %s",
        plant_code
    )

    rows = query_or_503("""
        SELECT
            plant_code,
            recorded_at,
            pv_power_kw::float8 AS pv_power_kw,
            load_power_kw::float8 AS load_power_kw,
            grid_power_kw::float8 AS grid_power_kw,
            yield_today_kwh::float8 AS yield_today_kwh,
            battery_soc_percent::float8 AS battery_soc_percent,
            data_source
        FROM public.plant_realtime
        WHERE plant_code = %s
        ORDER BY recorded_at DESC, id DESC
        LIMIT %s
    """, (plant_code, limit))

    rows.reverse()

    return {
        "success": True,
        "total": len(rows),
        "data": rows
    }


# ========================================
# 5. REACT DASHBOARD
# ========================================

def kwh_to_mwh(value):
    if value is None:
        return None

    return round(value / 1000, 3)


@router.get("/api/dashboard/{plant_code}")
def dashboard(plant_code: str):

    logger.info(
        "[API] GET dashboard for plant %s",
        plant_code
    )

    rows = query_or_503(
        OVERVIEW_SQL + " WHERE p.plant_code = %s",
        (plant_code,)
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Plant tidak ditemukan"
        )

    row = rows[0]
    recorded_at = row["recorded_at"]

    is_demo = (
        bool(row["is_demo"])
        or row["data_source"] == "DEMO"
    )

    is_old = (
        recorded_at is None
        or (
            datetime.now(timezone.utc)
            - recorded_at.astimezone(timezone.utc)
        ).total_seconds()
        > config.STALE_AFTER_MINUTES * 60
    )

    stale = is_demo or is_old

    message = (
        "Data DEMO, bukan realtime FusionSolar"
        if is_demo else
        "Data belum terbaru"
        if is_old else ""
    )

    return {
        "success": True,
        "stale": stale,
        "message": message,
        "retryAfterSeconds": 0,
        "recordedAt": recorded_at,

        "data": {
            "plantCode": row["plant_code"],
            "plantName": row["plant_name"],

            "pvCapacity": row["capacity_kwp"],
            "currentPvPower": row["pv_power_kw"],

            "yieldToday": kwh_to_mwh(
                row["yield_today_kwh"]
            ),

            "totalYield": kwh_to_mwh(
                row["total_yield_kwh"]
            ),

            "loadPower": row["load_power_kw"],
            "gridPower": row["grid_power_kw"],

            "batteryPower": row["battery_power_kw"],
            "batterySoc": row["battery_soc_percent"],

            "essChargeToday": row["ess_charge_today_kwh"],
            "essDischargeToday": row["ess_discharge_today_kwh"],
        }
    }
