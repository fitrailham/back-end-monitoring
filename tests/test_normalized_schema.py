from datetime import datetime, timedelta, timezone

from app import config
from app.services import alarm_service, device_kpi_mapper, statistics_service


def test_device_kpi_does_not_store_signed_power_before_convention_is_verified(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_POWER_DIRECTION_CONVENTION", "unverified")
    now = datetime.now(timezone.utc)
    device = {"id": 10, "plant_code": "P1", "device_type": "ess", "vendor_device_type_id": "41"}
    row = {"devId": "vendor-10", "currentTime": int(now.timestamp() * 1000),
           "dataItemMap": {"ch_discharge_power": 2500, "battery_soc": 60}}
    mapped = device_kpi_mapper.map_device_realtime(row, device, now)
    assert mapped["battery_power_kw"] is None
    assert mapped["battery_soc_percent"] == 60
    assert mapped["measurement_time_verified"] is False
    assert mapped["field_quality"]["battery_soc_percent"] == "TIME_UNVERIFIED"


def test_device_kpi_accepts_verified_collect_time_and_configured_sign(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_POWER_DIRECTION_CONVENTION",
                        "grid_import_battery_discharge_positive")
    now = datetime.now(timezone.utc)
    device = {"id": 11, "plant_code": "P1", "device_type": "meter", "vendor_device_type_id": "17"}
    row = {"devId": "vendor-11", "collectTime": int(now.timestamp() * 1000),
           "dataItemMap": {"active_power": 2500}}
    mapped = device_kpi_mapper.map_device_realtime(row, device, now + timedelta(seconds=2))
    assert mapped["grid_power_kw"] == 2.5
    assert mapped["measurement_time_verified"] is True
    assert mapped["power_direction_convention"] == "grid_import_battery_discharge_positive"


def test_alarm_service_prefers_normalized_table_and_source_state(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "")
    now = datetime.now(timezone.utc)
    rows = [{
        "plant_code": "P1", "plant_name": "Plant 1", "alarm_configured": True,
        "alarm_source_status": "success", "alarm_source_complete": True,
        "alarm_last_success_at": now,
        "normalized_alarms": [{"alarm_id": "P1:alarm:1", "vendor_alarm_id": "1",
                               "severity": "major", "state": "active"}],
    }]
    result = alarm_service.fleet_alarms(rows)
    assert result["configured"] is True and result["total"] == 1
    assert result["coverage"]["complete"] is True
    assert result["data"][0]["alarm_id"] == "P1:alarm:1"


def test_stored_statistics_are_used_as_normalized_source():
    start = datetime(2026, 11, 8, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    rows = [{"plant_code": "P1", "start_at": start, "end_at": end,
             "yield_kwh": 12.5, "quality": "measured", "complete": True,
             "received_samples": 12, "expected_samples": 12,
             "method": "northbound_hour", "reason": None}]
    result = statistics_service.stored_statistics(
        ["P1"], [start, end], "hour", "UTC", rows,
        end + timedelta(minutes=1), timezone.utc, "P1",
    )
    assert result["method"] == "northbound_hour"
    assert result["total_yield_kwh"] == 12.5
    assert result["buckets"][0]["quality"] == "measured"
