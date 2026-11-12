from datetime import datetime
from typing import Literal

from pydantic import Field, computed_field

from app.schemas.contracts import Contract, Grain, ItemResponse, ListResponse, MetricCoverage, ResponseMeta


class TimeRange(Contract):
    from_at: datetime
    to_at: datetime
    time_zone: str

    @computed_field(alias="from")
    @property
    def from_time(self) -> datetime:
        return self.from_at

    @computed_field(alias="to")
    @property
    def to_time(self) -> datetime:
        return self.to_at


class SampleCoverage(Contract):
    expected_samples: int
    valid_samples: int
    missing_samples: int
    unverified_samples: int
    quality: Literal["measured", "partial", "missing", "unverified", "pending"]
    reason: str | None = None


class PowerBucket(Contract):
    start_at: datetime
    end_at: datetime
    label_at: str
    pv_power_kw: float | None = None
    load_power_kw: float | None = None
    grid_power_kw: float | None = None
    battery_power_kw: float | None = None
    quality: Literal["measured", "partial", "missing", "pending"]
    coverage: dict[str, SampleCoverage]


class PowerStatistics(TimeRange):
    plant_code: str
    unit: Literal["kW"] = "kW"
    aggregation: Literal["average", "last"]
    resolution_minutes: int
    expected_interval_seconds: int
    source: Literal["plant_realtime"] = "plant_realtime"
    power_direction_convention: str = "grid_import_battery_discharge_positive_when_verified"
    complete: bool
    truncated: bool = False
    buckets: list[PowerBucket]


class PowerStatisticsResponse(ItemResponse[PowerStatistics]):
    meta: ResponseMeta


class InverterReportRow(Contract):
    plant_code: str
    device_key: str
    device_id: str
    device_name: str | None = None
    device_sn: str | None = None
    start_at: datetime
    end_at: datetime
    label: str
    label_at: str
    inverter_yield_kwh: float | None
    quality: Literal["measured", "derived", "partial", "missing", "pending"]
    reason: str | None
    received_samples: int
    expected_samples: int
    coverage: MetricCoverage


class ReportTotals(Contract):
    inverter_yield_kwh: float | None
    total_rows: int
    complete_rows: int
    partial_rows: int
    missing_rows: int
    pending_rows: int
    scope: Literal["all_filtered_rows_before_pagination"] = "all_filtered_rows_before_pagination"


class InverterReportResponse(ListResponse[InverterReportRow], TimeRange):
    grain: Grain
    unit: Literal["kWh"] = "kWh"
    aggregation: Literal["interval_energy"] = "interval_energy"
    method: Literal["verified_total_counter_difference"] = "verified_total_counter_difference"
    complete: bool
    truncated: bool = False
    totals: ReportTotals


class DeviceHistoryRow(Contract):
    sample_id: str
    measured_at: datetime | None
    collected_at: datetime
    measurement_time_verified: bool
    active_power_kw: float | None = None
    pv_input_power_kw: float | None = None
    load_power_kw: float | None = None
    grid_power_kw: float | None = None
    battery_power_kw: float | None = None
    battery_soc_percent: float | None = None
    total_yield_kwh: float | None = None
    yield_today_kwh: float | None = None
    field_quality: dict[str, str] = Field(default_factory=dict)
    power_direction_convention: str
    status: str


class DeviceHistoryResponse(ListResponse[DeviceHistoryRow], TimeRange):
    plant_code: str
    device_id: str
    device_key: str
    measurement_time_verified: bool
    units: dict[str, str]
