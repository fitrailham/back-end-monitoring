import os

from fastapi import APIRouter

from app.repositories.raw_repository import (
    latest_raw
)

from app.services.fusionsolar_client import (
    extract_list
)


router = APIRouter(
    prefix="/api/alarms",
    tags=["Alarms"]
)


@router.get("/{plant_code}")
def alarms(plant_code: str):

    path = os.getenv(
        "FUSIONSOLAR_ALARMS_PATH", ""
    )

    raw = (
        latest_raw(plant_code, path)
        if path else None
    )

    data = (
        extract_list(raw["raw_data"])
        if raw else []
    )

    return {
        "success": True,
        "plantCode": plant_code,
        "configured": bool(path),
        "collectedAt": (
            raw["collected_at"]
            if raw else None
        ),
        "total": len(data),
        "data": data
    }