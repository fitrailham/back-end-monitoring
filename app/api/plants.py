from fastapi import APIRouter, Depends

from app.auth import require_plant_write
from app.errors import ERROR_RESPONSES
from app.repositories import plant_admin_repository
from app.schemas.contracts import ListResponse, PatchPlant, PlantDetailResponse, PlantMetadata, RegisterPlant, RegisterPlantResponse
from app.services import fleet_service, inventory_service

router = APIRouter(prefix="/api/database", tags=["Plants"], responses=ERROR_RESPONSES)


@router.get("/plants", response_model=ListResponse[PlantMetadata])
def plants():
    rows = [inventory_service.metadata(row) for row in fleet_service.get_rows()]
    zones = {r["time_zone"] for r in rows}
    return {"success": True, "total": len(rows), "data": rows,
            "meta": fleet_service.response_meta(rows, returned=len(rows),
                time_zone=next(iter(zones)) if len(zones) == 1 else None,
                is_partial=any(r["metadata_issues"] for r in rows))}


@router.get("/plants/{plant_code}", response_model=PlantDetailResponse)
def plant_detail(plant_code: str):
    row = fleet_service.require_plant(fleet_service.get_rows(plant_code))
    data = inventory_service.metadata(row)
    return {"success": True, "data": data,
            "meta": fleet_service.response_meta(data, scope="plant", time_zone=data["time_zone"],
                                                is_partial=bool(data["metadata_issues"]))}


@router.post("/plants", status_code=201, response_model=RegisterPlantResponse,
    dependencies=[Depends(require_plant_write)],
    description="Register a plant locally in plants and plant_metadata. Requires an admin session and X-CSRF-Token. Does not create a FusionSolar plant.")
def register_plant(payload: RegisterPlant):
    plant_admin_repository.insert_plant(payload)
    return {**plant_detail(payload.plant_code),
            "message": "PLTS berhasil didaftarkan pada sistem monitoring lokal."}


@router.patch("/plants/{plant_code}", response_model=PlantDetailResponse,
    dependencies=[Depends(require_plant_write)],
    description="Update verified master metadata locally; telemetry and alarms are not writable.")
def patch_plant(plant_code: str, payload: PatchPlant):
    plant_admin_repository.patch_plant(plant_code, payload)
    return plant_detail(plant_code)
