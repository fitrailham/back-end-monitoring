from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query

from app.errors import ApiException, ERROR_RESPONSES
from app.schemas.analytics import DeviceHistoryResponse, InverterReportResponse, PowerStatisticsResponse
from app.schemas.contracts import Grain
from app.services import analytics_service

router = APIRouter(tags=["Range analytics"], responses=ERROR_RESPONSES)


@router.get("/api/database/power-statistics", response_model=PowerStatisticsResponse,
    description="Daya per rentang [from,to), maksimum 31 hari/5000 bucket. Waktu collector dan tanda grid/baterai belum terverifikasi dikecualikan. Sumber: plant_realtime; tidak memanggil vendor.")
def power_statistics(
    plant_code: str = Query(min_length=1, max_length=255),
    from_at: datetime = Query(alias="from"), to_at: datetime = Query(alias="to"),
    time_zone: str = "Asia/Jakarta",
    resolution_minutes: int = Query(10, ge=5, le=60, json_schema_extra={"enum": [5, 10, 15, 30, 60]}),
    aggregation: Literal["average", "last"] = "average",
):
    if resolution_minutes not in {5, 10, 15, 30, 60}:
        raise ApiException(422, "INVALID_RESOLUTION", "Gunakan resolution_minutes 5, 10, 15, 30, atau 60.")
    return analytics_service.power_statistics(plant_code, from_at, to_at, time_zone, resolution_minutes, aggregation)


@router.get("/api/reports/inverters", response_model=InverterReportResponse,
    description="Energi per inverter/bucket dari counter terverifikasi. Total dihitung sebelum pagination. device_id adalah ID vendor dan memerlukan scope plant_code. Tidak memanggil vendor.")
def inverter_report(
    from_at: datetime = Query(alias="from"), to_at: datetime = Query(alias="to"),
    plant_code: str | None = Query(None, min_length=1, max_length=255),
    device_id: str | None = Query(None, min_length=1, max_length=255),
    grain: Grain = "day", time_zone: str = "Asia/Jakarta",
    page: int = Query(1, ge=1, le=100_000), page_size: int = Query(100, ge=1, le=1000),
):
    return analytics_service.inverter_report(plant_code, device_id, from_at, to_at, grain, time_zone, page, page_size)


@router.get("/api/devices/{device_id}/history", response_model=DeviceHistoryResponse,
    description="Sampel perangkat berdasarkan rentang [from,to), maksimum 31 hari per request. device_id adalah ID vendor dari inventory; plant_code wajib untuk scope. Ikuti page/meta.total_pages untuk ekspor seluruh rentang.")
def device_history(
    device_id: str, plant_code: str = Query(min_length=1, max_length=255),
    from_at: datetime = Query(alias="from"), to_at: datetime = Query(alias="to"),
    time_zone: str = "Asia/Jakarta", page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(500, ge=1, le=1000),
):
    return analytics_service.device_history(plant_code, device_id, from_at, to_at, time_zone, page, page_size)
