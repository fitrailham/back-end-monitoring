from datetime import date as Date
from typing import Annotated

from fastapi import APIRouter, Query

from app.errors import ERROR_RESPONSES
from app.schemas.dashboard_home import DashboardAlarms, DashboardOverview, DashboardPlants
from app.services import dashboard_home_service


router = APIRouter(prefix="/api/v1", tags=["Dashboard Home"], responses=ERROR_RESPONSES)
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Snapshot = Annotated[str | None, Query(description="Snapshot ID from the previous page.", max_length=128)]


@router.get("/dashboard/overview", operation_id="getDashboardOverview", response_model=DashboardOverview)
def overview(date: Date, time_zone: str):
    return dashboard_home_service.overview(date, time_zone)


@router.get("/plants", operation_id="getPlants", response_model=DashboardPlants)
def plants(page: Page = 1, page_size: PageSize = 100, snapshot_id: Snapshot = None):
    return dashboard_home_service.plants(page, page_size, snapshot_id)


@router.get("/alarms/active", operation_id="getActiveAlarms", response_model=DashboardAlarms)
def active_alarms(page: Page = 1, page_size: PageSize = 100, snapshot_id: Snapshot = None):
    return dashboard_home_service.active_alarms(page, page_size, snapshot_id)
