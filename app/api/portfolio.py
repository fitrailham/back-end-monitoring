from datetime import date as Date

from fastapi import APIRouter, Query

from app.errors import ERROR_RESPONSES
from app.schemas.contracts import DeviceType, PlantStatus, SummaryResponse
from app.services import portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"], responses=ERROR_RESPONSES)


@router.get("/summary", response_model=SummaryResponse)
def summary(date: Date | None = None, time_zone: str = "Asia/Jakarta",
    search: str | None = Query(None, max_length=255),
    country_code: str | None = Query(None, pattern=r"^[A-Z]{2}$"),
    region: str | None = Query(None, max_length=255), device_type: DeviceType | None = None,
    device_sn: str | None = Query(None, max_length=255), status: PlantStatus | None = None):
    return portfolio_service.summary(date, time_zone, search=search, country_code=country_code,
        region=region, device_type=device_type, device_sn=device_sn, status=status)
