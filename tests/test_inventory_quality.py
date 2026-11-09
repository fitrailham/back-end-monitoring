from datetime import timedelta

from app.services import fleet_service, inventory_service
from app.services.values import utc_now
from test_frontend_api import sample_row


def test_conflicting_locations_not_arbitrarily_selected():
    row = sample_row()
    row["inventory_payload"]["data"][1]["latitude"] = -7
    result = inventory_service.metadata(row)
    assert result["latitude"] is None and result["location_conflict"] is True
    row["metadata"].update(latitude=-8, longitude=115, location_verified=True)
    result = inventory_service.metadata(row)
    assert result["latitude"] == -8 and result["location_verified"] is True


def test_missing_inventory_cannot_prove_no_ess_and_unknown_type_is_visible():
    row = sample_row()
    row["inventory_payload"] = None
    assert inventory_service.metadata(row)["has_ess"] is None
    row["inventory_payload"] = {"success": True, "data": [{"devTypeId": 9999}]}
    result = inventory_service.metadata(row)
    assert result["has_ess"] is None and result["device_types"] == ["unknown"]


def test_known_no_ess_is_distinct_from_missing_soc():
    row = sample_row(device_type=17)
    result = fleet_service.normalize(row)
    assert result["has_ess"] is False and result["battery_soc_percent"] is None
    assert result["data_quality"]["field_quality"]["battery_soc_percent"] == "NOT_INSTALLED"


def test_partial_optimizer_inventory_and_zero_coordinate_sentinel():
    row = sample_row()
    del row["inventory_payload"]["data"][1]["optimizerNumber"]
    for device in row["inventory_payload"]["data"]:
        device.update(latitude=0, longitude=0)
    result = inventory_service.metadata(row)
    assert result["optimizer_quantity"] is None
    assert result["optimizer_quantity_complete"] is False
    assert result["latitude"] is None


def test_success_with_null_inventory_does_not_prove_equipment_absence():
    row = sample_row()
    row["inventory_payload"] = {"success": True, "data": None}
    result = inventory_service.metadata(row)
    assert result["has_ess"] is None and result["inventory_complete"] is False


def test_metric_coverage_categories_and_unknown_total():
    valid = fleet_service.normalize(sample_row())
    unverified_raw = sample_row("B")
    unverified_raw["snapshot_raw"]["sampling_time_source"] = "collector"
    unverified = fleet_service.normalize(unverified_raw)
    stale_raw = sample_row("C")
    stale_raw["recorded_at"] = utc_now() - timedelta(hours=1)
    stale = fleet_service.normalize(stale_raw)
    missing_raw = sample_row("D")
    missing_raw["pv_power_kw"] = None
    missing = fleet_service.normalize(missing_raw)
    total, coverage = fleet_service.metric_total([valid, unverified, stale, missing], "pv_power_kw")
    assert total == 0
    assert [coverage[k] for k in ("expected", "valid", "missing", "stale", "unverified")] == [4, 1, 1, 1, 1]
    assert fleet_service.metric_total([unverified, stale, missing], "pv_power_kw")[0] is None
