from fastapi import APIRouter

from app.repositories.raw_repository import (
    latest_raw
)

from app.services.fusionsolar_client import (
    extract_list
)


router = APIRouter(
    prefix="/api/devices",
    tags=["Devices"]
)


@router.get("/{plant_code}")
def devices(plant_code: str):

    raw = latest_raw(
        plant_code,
        "getDevList"
    )

    data = (
        extract_list(raw["raw_data"])
        if raw else []
    )

    return {
        "success": True,
        "plantCode": plant_code,
        "collectedAt": (
            raw["collected_at"]
            if raw else None
        ),
        "total": len(data),
        "data": data
    }