from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.repositories import auth_repository, dashboard_repository, fleet_repository
from app.services import alarm_service, dashboard_home_service
from app.main import app


NOW = datetime(2026, 11, 19, 4, tzinfo=timezone.utc)
BUSINESS_DAY = {"date": "2026-11-19", "time_zone": "Asia/Jakarta"}


def plant(code, zone="Asia/Jakarta", devices=None, **extra):
    return {
        "plant_code": code, "plant_name": code, "capacity_kwp": 150,
        "metadata": {"time_zone": zone, "city": "Jakarta", "image_url": "https://example.com/plant.jpg"},
        "inventory_payload": {"success": True, "data": devices or []},
        **extra,
    }


def test_overview_uses_only_verified_sources_and_reports_coverage(monkeypatch, authenticated_admin):
    solar = [
        {"device_id": "a", "device_type": "inverter", "rated_ac_power_kw": 25, "device_sn": "001"},
        {"device_id": "b", "device_type": "inverter", "rated_ac_power_kw": 30, "device_sn": "002"},
        {"device_id": "ess", "device_type": "ess", "rated_energy_kwh": 50},
        {"device_id": "pack", "parent_device_id": "ess", "device_type": "ess", "rated_energy_kwh": 50},
    ]
    rows = [plant("A", devices=solar, pv_power_kw=85), plant("B", zone="Asia/Makassar")]
    monkeypatch.setattr(dashboard_home_service, "utc_now", lambda: NOW)
    monkeypatch.setattr(fleet_repository, "fleet", lambda *args, **kwargs: rows)
    monkeypatch.setattr(dashboard_repository, "station_health", lambda codes: [
        {"plant_code": "A", "collected_at": NOW, "raw_data": {"data": [
            {"stationCode": "A", "dataItemMap": {"real_health_state": 3}},
        ]}},
    ])
    monkeypatch.setattr(dashboard_repository, "inverter_power", lambda codes: [
        {"plant_code": "A", "measured_at": NOW, "measurement_time_verified": True, "active_power_kw": 0},
        {"plant_code": "A", "measured_at": NOW - timedelta(minutes=10),
         "measurement_time_verified": True, "active_power_kw": 20},
        {"plant_code": "A", "measured_at": NOW, "measurement_time_verified": True, "active_power_kw": None},
    ])
    monkeypatch.setattr(dashboard_repository, "station_yield", lambda codes, *args: [
        {"plant_code": "A", "day_yield": 12, "day_at": NOW, "day_verified": True,
         "total_yield": 900, "total_at": NOW},
        {"plant_code": "B", "day_yield": 30, "day_at": NOW, "day_verified": True},
    ])
    client = TestClient(app, **authenticated_admin)
    response = client.get("/api/v1/dashboard/overview", params=BUSINESS_DAY)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["kpis"] == {
        "current_power_kw": 0, "revenue_today_idr": None, "ess_rated_capacity_kwh": 50,
        "yield_today_kwh": 12, "inverter_rated_power_kw": 55, "total_yield_kwh": 900, "currency": "IDR",
    }
    assert data["revenue"]["status"] == "unconfigured"
    assert data["coverage"]["current_power_kw"]["stale"] == 1
    assert data["coverage"]["current_power_kw"]["partial"] is True
    assert data["coverage"]["yield_today_kwh"]["unverified"] == 1
    assert data["plant_status"] == {"normal": 1, "faulty": 0, "disconnected": 0, "unknown": 1}
    assert client.get("/api/v1/dashboard/overview").status_code == 422


def test_current_power_uses_cached_values_only_when_no_fresh_value_is_available():
    reading = dashboard_home_service.Reading
    cached = reading(20, "stale", NOW - timedelta(minutes=15), cached=True)
    assert dashboard_home_service.aggregate([cached, reading(0, "valid", NOW)])[0] == 0
    value, coverage = dashboard_home_service.aggregate([cached, reading()])
    assert value == 20
    assert coverage["stale"] == 1
    assert coverage["partial"] is True


def test_plants_preserves_metadata_and_account_scope(monkeypatch, authenticated_admin):
    rows = [plant("A", devices=[{"device_id": "a", "device_type": "inverter", "device_sn": "001"}]),
            plant("B")]
    original = auth_repository.find_session

    def viewer(hashed):
        user = original(hashed)
        return {**user, "role": "viewer", "plant_codes": ["A"]} if user else None

    monkeypatch.setattr(auth_repository, "find_session", viewer)
    monkeypatch.setattr(fleet_repository, "fleet", lambda *args, **kwargs: rows)
    monkeypatch.setattr(dashboard_repository, "station_health", lambda codes: [])
    client = TestClient(app, **authenticated_admin)
    response = client.get("/api/v1/plants", params={"page_size": 1})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 1
    assert result["data"][0]["plant_code"] == "A"
    assert result["data"][0]["status"] == "unknown"
    assert result["data"][0]["city"] == "Jakarta"
    assert result["data"][0]["device_serial_numbers"] == ["001"]
    assert client.get("/api/v1/plants", params={"snapshot_id": result["meta"]["snapshot_id"]}).status_code == 200
    assert client.get("/api/v1/plants", params={"snapshot_id": "old"}).status_code == 409
    assert TestClient(app).get("/api/v1/plants").status_code == 401


def test_alarms_distinguishes_empty_missing_failed_and_cached(monkeypatch, authenticated_admin):
    monkeypatch.setattr(alarm_service, "utc_now", lambda: NOW)
    monkeypatch.setattr(alarm_service.config, "FUSIONSOLAR_ALARMS_PATH", "")
    row = plant("A", devices=[{"device_id": "i", "device_type": "inverter", "device_name": "Inverter 1"}])
    monkeypatch.setattr(fleet_repository, "fleet", lambda *args, **kwargs: [row])
    client = TestClient(app, **authenticated_admin)
    get_alarms = lambda: client.get("/api/v1/alarms/active")
    assert get_alarms().json()["configured"] is False
    row.update(alarm_configured=True, alarm_source_status="unknown")
    assert get_alarms().json()["total"] is None
    row.update(alarm_source_status="success", alarm_source_complete=True, alarm_last_success_at=NOW)
    empty = get_alarms().json()
    assert (empty["data"], empty["total"], empty["quality"], empty["coverage"]["complete"]) == ([], 0, "fresh", True)
    active = {"occurrenceId": "real-event", "devDn": "i", "severity": 2, "status": 1, "acknowledged": True,
              "repairSuggestion": "Check the vendor manual.", "raiseTime": NOW.isoformat()}
    row.update(alarm_source_status="failed", alarm_source_complete=False, normalized_alarms=[active, active])
    cached = get_alarms().json()
    assert cached["total"] == 1
    assert cached["coverage"]["complete"] is False
    alarm = cached["data"][0]
    assert alarm["device_name"] == "Inverter 1"
    assert alarm["repair_suggestion"] == "Check the vendor manual."
    assert (alarm["acknowledged"], alarm["state"], alarm["severity"]) == (True, "active", "major")
    assert cached["collected_at"] is not None
    row.update(alarm_last_success_at=None, normalized_alarms=None)
    unavailable = get_alarms()
    assert unavailable.status_code == 503
    assert unavailable.json()["data"] is None
