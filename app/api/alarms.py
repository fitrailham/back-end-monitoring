from typing import Literal

from fastapi import APIRouter, Query

from app.errors import ERROR_RESPONSES
from app.schemas.contracts import AlarmsResponse, Severity
from app.services import alarm_service, fleet_service

router = APIRouter(prefix="/api/alarms", tags=["Alarms"], responses=ERROR_RESPONSES)


@router.get("", response_model=AlarmsResponse)
def fleet_alarms(plant_code: str | None = None, severity: Severity | None = None,
    state: Literal["active", "cleared"] | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100)):
    rows = fleet_service.get_rows(plant_code)
    if plant_code is not None:
        fleet_service.require_plant(rows)
    return alarm_service.fleet_alarms(rows, severity, state, page, page_size, plant_code=plant_code)


@router.get("/{plant_code}", response_model=AlarmsResponse)
def alarms(plant_code: str, severity: Severity | None = None,
    state: Literal["active", "cleared"] | None = None,
    page: int | None = Query(None, ge=1), page_size: int | None = Query(None, ge=1, le=100)):
    rows = fleet_service.get_rows(plant_code)
    fleet_service.require_plant(rows)
    return alarm_service.fleet_alarms(rows, severity, state, page, page_size, plant_code=plant_code)
