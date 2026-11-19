from datetime import date as Date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app import config
from app.auth import require_admin
from app.errors import ApiException, ERROR_RESPONSES
from app.repositories import sync_repository
from app.schemas.contracts import (DeviceType, Grain, ListResponse, PlantOverview,
    PlantStatus, RankingsResponse, YieldStatisticsResponse, HistoryResponse, DashboardResponse, SyncLogsResponse)
from app.services import fleet_service, monitoring_service, portfolio_service, statistics_service

router = APIRouter(tags=["Monitoring"], responses=ERROR_RESPONSES)


@router.get("/api/database/overview", response_model=ListResponse[PlantOverview],
    description="All plants by default. Pagination is opt-in via page/page_size. Units: kW, kWp, kWh.")
def overview(
    search: str | None = Query(None, max_length=255), country_code: str | None = Query(None, pattern=r"^[A-Z]{2}$"),
    region: str | None = Query(None, max_length=255), device_type: DeviceType | None = None,
    device_sn: str | None = Query(None, max_length=255), status: PlantStatus | None = None,
    page: int | None = Query(None, ge=1), page_size: int | None = Query(None, ge=1, le=100),
    sort: Literal["plant_name", "capacity_kwp", "pv_power_kw", "yield_today_kwh", "total_yield_kwh", "specific_energy_kwh_per_kwp"] = "plant_name",
    order: Literal["asc", "desc"] = "asc",
):
    return fleet_service.overview(page, page_size, sort, order, search=search, country_code=country_code,
        region=region, device_type=device_type, device_sn=device_sn, status=status)


@router.get("/api/database/history/{plant_code}", response_model=HistoryResponse)
def history(plant_code: str, limit: int = Query(default=100, ge=1, le=500)):
    fleet_service.require_plant(fleet_service.get_rows(plant_code))
    rows = monitoring_service.history(plant_code, limit)
    return {"success": True, "total": len(rows), "data": rows}


@router.get("/api/database/yield-statistics", response_model=YieldStatisticsResponse,
    description="Interval energy from verified total counter differences, without interpolation. Missing periods stay null.")
def yield_statistics(
    from_at: datetime = Query(alias="from"), to_at: datetime = Query(alias="to"),
    grain: Grain = "hour", time_zone: str = "Asia/Jakarta", plant_code: str | None = None,
):
    statistics_service.validate_range(from_at, to_at, grain, time_zone)
    rows = fleet_service.get_rows(plant_code)
    if plant_code is not None:
        fleet_service.require_plant(rows)
    codes = [r["plant_code"] for r in rows if not r.get("is_demo")]
    data = statistics_service.interval_statistics(codes, from_at, to_at, grain, time_zone, plant_code=plant_code)
    buckets = data["buckets"]
    coverage = {"expected_buckets": sum(b["quality"] != "pending" for b in buckets),
                "complete_buckets": sum(b["quality"] in ("measured", "derived") for b in buckets),
                "partial_buckets": sum(b["quality"] == "partial" for b in buckets),
                "missing_buckets": sum(b["quality"] == "missing" for b in buckets),
                "pending_buckets": sum(b["quality"] == "pending" for b in buckets)}
    return {"success": True, "data": data,
            "meta": fleet_service.response_meta(data, is_partial=not data["complete"], time_zone=time_zone,
                coverage=coverage, returned=len(buckets), scope="plant" if plant_code is not None else "all_configured_plants",
                filters_applied={"plant_code": plant_code} if plant_code is not None else {},
                date_basis="request_timezone_interval_energy",
                warnings=["Collector timestamps and DEMO samples are excluded. Counter resets and gaps are not interpolated."])}


@router.get("/api/database/rankings", response_model=RankingsResponse)
def rankings(date: Date, metric: Literal["specific_energy", "yield"] = "specific_energy",
             limit: int = Query(5, ge=1, le=100), time_zone: str = "Asia/Jakarta"):
    return portfolio_service.rankings(date, time_zone, limit, metric)


@router.get("/api/dashboard/{plant_code}", response_model=DashboardResponse)
def dashboard(plant_code: str):
    row = fleet_service.require_plant(fleet_service.get_rows(plant_code))
    if row.get("recorded_at") is None and all(row.get(field) is None for field in fleet_service.METRIC_FIELDS):
        raise ApiException(503, "TELEMETRY_UNAVAILABLE",
            "Sumber telemetry belum tersedia dan tidak ada cache yang dapat digunakan.", True,
            retry_after_seconds=config.EXPECTED_INTERVAL_SECONDS)
    normalized = fleet_service.normalize(row)
    legacy = monitoring_service.format_dashboard(normalized)
    legacy["normalized"] = normalized
    legacy["data_quality"] = normalized["data_quality"]
    return legacy


@router.get("/api/database/sync-logs", response_model=SyncLogsResponse, dependencies=[Depends(require_admin)])
def sync_logs(limit: int = Query(default=20, ge=1, le=100)):
    return {"success": True, "data": sync_repository.recent_jobs(limit)}
