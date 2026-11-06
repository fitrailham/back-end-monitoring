from datetime import datetime
from pydantic import BaseModel


class Snapshot(BaseModel):
    plant_code: str
    recorded_at: datetime

    pv_power_kw: float | None = None
    load_power_kw: float | None = None
    grid_power_kw: float | None = None

    yield_today_kwh: float | None = None
    total_yield_kwh: float | None = None

    battery_power_kw: float | None = None
    battery_soc_percent: float | None = None

    ess_charge_today_kwh: float | None = None
    ess_discharge_today_kwh: float | None = None

    data_source: str = "FUSIONSOLAR"