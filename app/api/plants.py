from fastapi import (
    APIRouter,
    HTTPException
)

from app.services import plant_service


router = APIRouter(
    prefix="/api/database",
    tags=["Plants"]
)


@router.get("/plants")
def plants():

    rows = plant_service.list_all()

    return {
        "success": True,
        "total": len(rows),
        "data": rows
    }


@router.get("/plants/{plant_code}")
def plant_detail(plant_code: str):

    plant = plant_service.get_one(
        plant_code
    )

    if plant is None:
        raise HTTPException(
            status_code=404,
            detail="Plant tidak ditemukan"
        )

    return {
        "success": True,
        "data": plant
    }