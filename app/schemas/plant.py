from pydantic import BaseModel, ConfigDict


class Plant(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plant_code: str
    plant_name: str
    capacity_kwp: float | None = None
    address: str | None = None
    is_demo: bool = False