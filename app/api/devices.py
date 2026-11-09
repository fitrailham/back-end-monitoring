from fastapi import APIRouter

from app import config
from app.errors import ERROR_RESPONSES
from app.schemas.contracts import DevicesResponse
from app.services import fleet_service, inventory_service
from app.services.values import aware_time, utc_now

router = APIRouter(prefix="/api/devices", tags=["Devices"], responses=ERROR_RESPONSES)


@router.get("/{plant_code}", response_model=DevicesResponse)
def devices(plant_code: str):
    row = fleet_service.require_plant(fleet_service.get_rows(plant_code))
    collected = aware_time(row.get("inventory_collected_at"))
    data = inventory_service.devices(row.get("inventory_payload"), plant_code)
    quality = "missing" if collected is None else "stale" if (utc_now() - collected).total_seconds() > config.STALE_AFTER_MINUTES * 60 else "fresh"
    metadata = inventory_service.metadata(row)
    return {"success": True, "plantCode": plant_code, "plant_code": plant_code,
            "collectedAt": collected, "collected_at": collected, "total": len(data), "data": data, "quality": quality,
            "meta": fleet_service.response_meta(data, scope="plant", time_zone=metadata["time_zone"],
                returned=len(data), is_partial=quality != "fresh" or not metadata["inventory_complete"])}
