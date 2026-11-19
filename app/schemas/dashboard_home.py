from datetime import datetime
from typing import Literal

from app.schemas.contracts import (
    AlarmCoverage, Contract, MetricCoverageCounts, PlantOverview, PlantStatus,
    QualityState, ResponseMeta, Severity,
)


class DashboardKpis(Contract):
    current_power_kw: float | None
    revenue_today_idr: float | None
    ess_rated_capacity_kwh: float | None
    yield_today_kwh: float | None
    inverter_rated_power_kw: float | None
    total_yield_kwh: float | None
    currency: Literal["IDR"] = "IDR"


class DashboardRevenue(Contract):
    status: str
    reason: str | None = "Approved revenue methodology and tariffs have not been configured."


class DashboardOverviewData(Contract):
    total_plants: int
    updated_at: datetime | None
    quality: QualityState
    kpis: DashboardKpis
    coverage: dict[str, MetricCoverageCounts]
    plant_status: dict[PlantStatus, int]
    revenue: DashboardRevenue


class DashboardOverview(Contract):
    success: Literal[True] = True
    data: DashboardOverviewData
    meta: ResponseMeta


class DashboardPageMeta(ResponseMeta):
    page: int
    page_size: int
    total_pages: int


class DashboardPlants(Contract):
    success: Literal[True] = True
    total: int
    data: list[PlantOverview]
    meta: DashboardPageMeta


class DashboardAlarm(Contract):
    alarm_id: str
    plant_code: str
    plant_name: str | None = None
    device_id: str | None = None
    device_name: str | None
    device_sn: str | None = None
    name: str | None
    severity: Severity
    state: Literal["active"]
    occurred_at: datetime | None
    repair_suggestion: str | None
    acknowledged: bool | None = None
    description: str | None = None


class DashboardAlarms(Contract):
    success: Literal[True] = True
    configured: bool
    quality: QualityState
    collected_at: datetime | None
    total: int | None
    coverage: AlarmCoverage
    data: list[DashboardAlarm]
    meta: DashboardPageMeta
