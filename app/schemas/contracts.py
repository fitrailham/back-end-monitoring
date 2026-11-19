"""Frontend contracts. Legacy field names remain on existing endpoints."""
from datetime import date as Date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.services.values import valid_zone

PlantStatus = Literal["normal", "faulty", "disconnected", "unknown"]
QualityState = Literal["fresh", "stale", "partial", "missing", "unverified"]
DeviceType = Literal["inverter", "ess", "meter", "logger", "dongle", "emi", "unknown"]
Severity = Literal["critical", "major", "minor", "warning", "unclassified"]
Grain = Literal["hour", "day", "month", "year"]


class Contract(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class DataQuality(Contract):
    state: QualityState
    measured_at: datetime | None = None
    collected_at: datetime | None = None
    measurement_time_verified: bool = False
    age_seconds: float | None = None
    stale_after_seconds: int
    expected_interval_seconds: int
    missing_fields: list[str] = Field(default_factory=list)
    not_applicable_fields: list[str] = Field(default_factory=list)
    field_quality: dict[str, str] = Field(default_factory=dict)


class PlantMetadata(Contract):
    plant_code: str
    plant_name: str
    capacity_kwp: float | None = None
    address: str | None = None
    is_demo: bool = False
    country_code: str | None = None
    country_name: str | None = None
    region: str | None = None
    city: str | None = None
    time_zone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_source: str | None = None
    location_verified: bool = False
    location_conflict: bool = False
    image_url: str | None = None
    grid_connected_on: Date | None = None
    has_pv: bool | None = None
    has_ess: bool | None = None
    ess_quantity: int | None = None
    ess_rated_capacity_kwh: float | None = None
    inverter_rated_power_kw: float | None = None
    optimizer_quantity: int | None = None
    optimizer_quantity_complete: bool = False
    device_types: list[DeviceType] = Field(default_factory=list)
    device_serial_numbers: list[str] = Field(default_factory=list)
    device_count: int | None = None
    inventory_collected_at: datetime | None = None
    inventory_complete: bool = False
    metadata_issues: list[str] = Field(default_factory=list)


class PlantOverview(PlantMetadata):
    recorded_at: datetime | None = Field(default=None, description="Legacy storage timestamp; may be collector time.")
    measured_at: datetime | None = None
    collected_at: datetime | None = None
    timestamp_source: str | None = None
    measurement_time_verified: bool = False
    data_source: str | None = None
    status: PlantStatus = "unknown"
    status_source: str | None = None
    status_reason: str
    last_seen_at: datetime | None = None
    pv_power_kw: float | None = None
    load_power_kw: float | None = None
    grid_power_kw: float | None = None
    battery_power_kw: float | None = None
    battery_soc_percent: float | None = None
    yield_today_kwh: float | None = None
    total_yield_kwh: float | None = None
    ess_charge_today_kwh: float | None = None
    ess_discharge_today_kwh: float | None = None
    specific_energy_kwh_per_kwp: float | None = None
    performance_ratio_percent: float | None = None
    poa_irradiation_today_kwh_m2: float | None = None
    revenue_today_idr: float | None = None
    yield_business_date: Date | None = None
    power_direction_convention: str = "source_native_unverified"
    data_quality: DataQuality


class ResponseMeta(Contract):
    generated_at: datetime
    snapshot_id: str
    is_partial: bool = False
    page: int | None = None
    page_size: int | None = None
    total_pages: int | None = None
    returned: int | None = None
    scope: str = "all_configured_plants"
    date: Date | None = None
    time_zone: str | None = None
    date_basis: str | None = None
    filters_applied: dict[str, str] = Field(default_factory=dict)
    coverage: "dict[str, MetricCoverage] | BucketCoverage | AlarmCoverage | None" = None
    ranking_date: Date | None = None
    eligible_plants: int | None = None
    excluded_plants: int | None = None
    excluded_reasons: dict[str, int] = Field(default_factory=dict)
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


T = TypeVar("T")


class ListResponse(Contract, Generic[T]):
    success: bool = True
    source: str = "PostgreSQL"
    total: int
    data: list[T]
    meta: ResponseMeta


class ItemResponse(Contract, Generic[T]):
    success: bool = True
    data: T


class PlantDetailResponse(ItemResponse[PlantMetadata]):
    meta: ResponseMeta


class RegisterPlantResponse(PlantDetailResponse):
    message: str


class MetricCoverageCounts(Contract):
    expected: int
    valid: int
    missing: int
    stale: int
    unverified: int
    not_applicable: int
    partial: bool


class MetricCoverage(MetricCoverageCounts):
    @computed_field
    @property
    def expected_plants(self) -> int:
        return self.expected

    @computed_field
    @property
    def valid_plants(self) -> int:
        return self.valid

    @computed_field
    @property
    def missing_plants(self) -> int:
        return self.missing

    @computed_field
    @property
    def stale_plants(self) -> int:
        return self.stale

    @computed_field
    @property
    def unverified_plants(self) -> int:
        return self.unverified

    @computed_field
    @property
    def not_applicable_plants(self) -> int:
        return self.not_applicable

    @computed_field
    @property
    def complete(self) -> bool:
        return not self.partial


class BucketCoverage(Contract):
    expected_buckets: int
    complete_buckets: int
    partial_buckets: int
    missing_buckets: int
    pending_buckets: int


class Device(Contract):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    device_id: str | None
    device_sn: str | None
    device_name: str | None
    device_key: str | None = None
    software_version: str | None = None
    parent_device_id: str | None = None
    parent_device_name: str | None = None
    plant_code: str
    vendor_device_type_id: int | str | None
    model: str | None = None
    device_type: DeviceType
    optimizer_quantity: int | None
    latitude: float | None
    longitude: float | None
    rated_ac_power_kw: float | None
    rated_energy_kwh: float | None
    status: PlantStatus = "unknown"
    last_seen_at: datetime | None = None


class DevicesResponse(Contract):
    success: bool = True
    plantCode: str
    plant_code: str
    collectedAt: datetime | None
    collected_at: datetime | None
    total: int
    data: list[Device]
    quality: QualityState
    meta: ResponseMeta


class Alarm(Contract):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    alarm_id: str | None
    vendor_alarm_id: str | None
    plant_code: str
    plant_name: str | None = None
    device_id: str | None
    device_sn: str | None
    severity: Severity
    state: Literal["active", "cleared"] | None
    acknowledged: bool | None
    name: str | None
    description: str | None
    occurred_at: datetime | None
    cleared_at: datetime | None


class AlarmCoverage(Contract):
    expected_plants: int
    configured_plants: int
    unconfigured_plants: int
    failed_plants: int
    missing_plants: int
    stale_plants: int
    valid_plants: int
    complete: bool


class AlarmsResponse(Contract):
    success: bool = True
    plantCode: str | None = None
    plant_code: str | None = None
    configured: bool
    collectedAt: datetime | None = None
    collected_at: datetime | None = None
    total: int | None
    data: list[Alarm]
    quality: QualityState
    coverage: AlarmCoverage
    meta: ResponseMeta


class ActiveAlarms(Contract):
    configured: bool = False
    total: int | None
    severity: dict[Severity, int | None]
    coverage: AlarmCoverage

    @computed_field
    @property
    def critical(self) -> int | None:
        return self.severity["critical"]

    @computed_field
    @property
    def major(self) -> int | None:
        return self.severity["major"]

    @computed_field
    @property
    def minor(self) -> int | None:
        return self.severity["minor"]

    @computed_field
    @property
    def warning(self) -> int | None:
        return self.severity["warning"]

    @computed_field
    @property
    def unclassified(self) -> int | None:
        return self.severity["unclassified"]

    @computed_field
    @property
    def configured_plants(self) -> int:
        return self.coverage.configured_plants

    @computed_field
    @property
    def unconfigured_plants(self) -> int:
        return self.coverage.unconfigured_plants

    @computed_field
    @property
    def failed_plants(self) -> int:
        return self.coverage.failed_plants

    @computed_field
    @property
    def complete(self) -> bool:
        return self.coverage.complete


class PortfolioKpis(Contract):
    current_power_kw: float | None
    yield_today_kwh: float | None
    total_yield_kwh: float | None
    installed_pv_capacity_kwp: float | None
    inverter_rated_power_kw: float | None
    ess_rated_capacity_kwh: float | None
    revenue_today_idr: float | None = None
    currency: str = "IDR"


class Revenue(Contract):
    status: str = "unconfigured"
    revenue_today_idr: float | None = None
    self_consumption_savings_idr: float | None = None
    export_revenue_idr: float | None = None
    eligible_energy_kwh: float | None = None
    tariff_set_id: str | None = None
    method: str | None = None
    period: str = "day"
    currency: str = "IDR"
    basis: str | None = None
    tariff_version: str | None = None
    is_estimate: bool | None = None
    reason: str | None = "Approved revenue methodology and tariffs have not been configured"


class EnvironmentalBenefits(Contract):
    status: str = "unconfigured"
    eligible_energy_kwh: float | None = None
    co2_avoided_kg: float | None = None
    coal_saved_kg: float | None = None
    equivalent_tree_years: float | None = None
    factor_set_id: str | None = None
    source: str | None = None
    version: str | None = None
    method: str | None = None
    period: str = "lifetime"
    reason: str | None = "Approved environmental conversion factors have not been configured"

    @computed_field
    @property
    def standard_coal_saved_kg(self) -> float | None:
        return self.coal_saved_kg

    @computed_field
    @property
    def factor_source(self) -> str | None:
        return self.source

    @computed_field
    @property
    def factor_version(self) -> str | None:
        return self.version


class PortfolioSummary(Contract):
    total_plants: int
    kpis: PortfolioKpis
    plant_status: dict[PlantStatus, int]
    active_alarms: ActiveAlarms
    coverage: dict[str, MetricCoverage]
    environmental_benefits: EnvironmentalBenefits = Field(default_factory=EnvironmentalBenefits)
    revenue: Revenue = Field(default_factory=Revenue)


class SummaryResponse(ItemResponse[PortfolioSummary]):
    meta: ResponseMeta


class YieldBucket(Contract):
    start_at: datetime
    end_at: datetime
    label: str
    label_at: str
    yield_kwh: float | None
    quality: Literal["measured", "derived", "partial", "missing", "pending"]
    received_samples: int
    expected_samples: int
    reason: str | None
    coverage: MetricCoverage


class YieldStatistics(Contract):
    aggregation: str = "interval_energy"
    unit: str = "kWh"
    method: str = "verified_total_counter_difference"
    scope: Literal["plant", "portfolio"]
    plant_code: str | None = None
    metric: str = "pv_generation_energy"
    total_yield_kwh: float | None
    grain: Grain
    time_zone: str
    from_at: datetime
    to_at: datetime
    complete: bool
    truncated: bool = False
    buckets: list[YieldBucket]

    @computed_field(alias="from")
    @property
    def from_time(self) -> datetime:
        return self.from_at

    @computed_field(alias="to")
    @property
    def to_time(self) -> datetime:
        return self.to_at


class YieldStatisticsResponse(ItemResponse[YieldStatistics]):
    meta: ResponseMeta


class RankingRow(Contract):
    rank: int
    plant_code: str
    plant_name: str
    yield_kwh: float
    capacity_kwp: float
    specific_energy_kwh_per_kwp: float
    quality: str


class RankingsResponse(ListResponse[RankingRow]):
    eligible_plants: int
    excluded_plants: int
    exclusion_reasons: dict[str, int]


class HistoryRow(Contract):
    plant_code: str
    recorded_at: datetime
    pv_power_kw: float | None = None
    load_power_kw: float | None = None
    grid_power_kw: float | None = None
    yield_today_kwh: float | None = None
    battery_soc_percent: float | None = None
    data_source: str | None = None
    measured_at: datetime | None = None
    collected_at: datetime | None = None
    measurement_time_verified: bool = False


class HistoryResponse(Contract):
    success: bool = True
    total: int
    data: list[HistoryRow]


class LegacyDashboardData(Contract):
    plantCode: str
    plantName: str
    pvCapacity: float | None
    currentPvPower: float | None
    yieldToday: float | None = Field(description="Legacy MWh rounded for display. Canonical kWh is in normalized.")
    totalYield: float | None
    loadPower: float | None
    gridPower: float | None
    batteryPower: float | None
    batterySoc: float | None
    essChargeToday: float | None
    essDischargeToday: float | None


class DashboardResponse(Contract):
    success: bool = True
    stale: bool
    message: str
    recordedAt: datetime | None
    data: LegacyDashboardData
    normalized: PlantOverview
    data_quality: DataQuality


class SyncLog(Contract):
    id: int
    job_name: str
    plant_code: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    records_inserted: int | None
    error_message: str | None


class SyncLogsResponse(Contract):
    success: bool = True
    data: list[SyncLog]


class SourceFreshness(Contract):
    api_endpoint: str
    configured: bool
    expected_plants: int
    fresh: int
    stale: int
    partial: int
    failed: int
    missing: int
    unconfigured: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    checked_at: datetime


class SyncHealthResponse(Contract):
    success: bool = True
    data: list[SourceFreshness]


class RegisterPlant(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    plant_code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_=.:\-]+$")
    plant_name: str = Field(min_length=1, max_length=255)
    capacity_kwp: float = Field(gt=0)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    country_name: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    time_zone: str
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    grid_connected_on: Date | None = None

    @field_validator("time_zone")
    @classmethod
    def check_zone(cls, value):
        if valid_zone(value) is None:
            raise ValueError("Gunakan timezone IANA yang valid.")
        return value

    @model_validator(mode="after")
    def coordinates_together(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude dan longitude harus diisi berpasangan.")
        return self


class ErrorField(Contract):
    field: str
    message: str


class PatchPlant(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    country_name: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=255)
    time_zone: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_verified: bool = False
    image_url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")
    grid_connected_on: Date | None = None
    has_pv: bool | None = None
    has_ess: bool | None = None
    ess_rated_capacity_kwh: float | None = Field(default=None, ge=0)
    inverter_rated_power_kw: float | None = Field(default=None, ge=0)
    capacity_kwp: float | None = Field(default=None, gt=0)

    @field_validator("time_zone")
    @classmethod
    def check_zone(cls, value):
        if value is not None and valid_zone(value) is None:
            raise ValueError("Gunakan timezone IANA yang valid.")
        return value

    @model_validator(mode="after")
    def check_coordinates(self):
        supplied = self.model_fields_set
        if ("latitude" in supplied) != ("longitude" in supplied) or (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude dan longitude harus diisi berpasangan.")
        if self.location_verified and (self.latitude is None or self.longitude is None):
            raise ValueError("Verifikasi lokasi memerlukan pasangan koordinat eksplisit.")
        if "capacity_kwp" in supplied and self.capacity_kwp is None:
            raise ValueError("Kapasitas harus positif jika diperbarui.")
        return self


class ErrorDetail(Contract):
    code: str
    retryable: bool = False
    retry_after_seconds: int | None = None
    fields: list[ErrorField] = Field(default_factory=list)


class ApiError(Contract):
    success: bool = False
    message: str
    data: None = None
    error: ErrorDetail
    request_id: str


# Resolve the metadata coverage union after all response models have been declared.
ResponseMeta.model_rebuild()
