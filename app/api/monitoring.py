from fastapi import (
    APIRouter,
    HTTPException,
    Query
)

from app.services import (
    monitoring_service as svc
)

from app.repositories import sync_repository


router = APIRouter(
    tags=["Monitoring"]
)


@router.get("/api/database/overview")
def overview():

    rows = svc.all_overviews()

    return {
        "success": True,
        "source": "PostgreSQL",
        "total": len(rows),
        "data": rows
    }


@router.get(
    "/api/database/history/{plant_code}"
)
def history(
    plant_code: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=500
    )
):

    rows = svc.history(
        plant_code,
        limit
    )

    return {
        "success": True,
        "total": len(rows),
        "data": rows
    }


@router.get("/api/dashboard/{plant_code}")
def dashboard(plant_code: str):

    row = next(
        (
            x for x in svc.all_overviews()
            if x["plant_code"] == plant_code
        ),
        None
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Plant tidak ditemukan"
        )

    return svc.format_dashboard(row)


@router.get("/api/database/sync-logs")
def sync_logs(
    limit: int = Query(
        default=20,
        ge=1,
        le=100
    )
):

    rows = sync_repository.recent_jobs(
        limit
    )

    return {
        "success": True,
        "data": rows
    }