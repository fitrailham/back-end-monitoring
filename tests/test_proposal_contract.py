"""Contract checks against proposal-1; illustrative numbers are never production defaults."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from app import config
from app.repositories import fleet_repository, plant_admin_repository
from app.services.values import utc_now
from test_frontend_api import client, rows, registration


def assert_fields_present(expected, actual, path="response"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        for key, value in expected.items():
            assert key in actual, f"Missing {path}.{key}"
            assert_fields_present(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), path
        if expected and actual:
            assert_fields_present(expected[0], actual[0], path + "[]")


@pytest.mark.parametrize("name", ["plants", "plant_detail", "overview", "portfolio_summary", "yield_statistics",
    "rankings", "devices", "alarms_unconfigured", "alarms_configured", "fleet_alarms", "register_plant"])
def test_exported_responses_contain_all_proposed_fields(name):
    root = config.PROJECT_ROOT / "docs"
    proposal = json.loads((root / "frontend-api-proposal-1.json").read_text(encoding="utf-8"))
    actual = json.loads((root / "frontend-api-examples.json").read_text(encoding="utf-8"))
    assert_fields_present(proposal["endpoints"][name]["response"], actual["endpoints"][name]["response"])


def test_overview_proposal_metadata_is_scoped_before_pagination(client):
    body = client.get("/api/database/overview", params={"page": 1, "page_size": 10}).json()
    assert body["meta"]["returned"] == len(body["data"]) == 10
    coverage = body["meta"]["coverage"]["pv_power_kw"]
    assert coverage["expected_plants"] == coverage["expected"] == 38
    assert coverage["valid_plants"] == coverage["valid"] == 38 and coverage["complete"] is True
    filtered = client.get("/api/database/overview", params={"search": "P01", "status": "normal"}).json()
    assert filtered["meta"]["filters_applied"] == {"search": "P01", "status": "normal"}
    assert filtered["meta"]["coverage"]["capacity_kwp"]["expected_plants"] == 1
    assert "poa_irradiation_today_kwh_m2" in filtered["data"][0]
    assert filtered["data"][0]["revenue_today_idr"] is None


@pytest.mark.parametrize("path", ["/api/database/plants", "/api/database/plants/P00", "/api/database/overview?search=P00"])
def test_serial_numbers_available_in_master_and_overview(client, path):
    body = client.get(path).json()
    row = body["data"][0] if isinstance(body["data"], list) else body["data"]
    assert row["device_serial_numbers"] == ["000123", "000456"]
    assert body["meta"]["time_zone"] == "Asia/Makassar"


def test_not_installed_metrics_are_separate_from_missing(client, rows):
    rows[0]["inventory_payload"]["data"][0]["devTypeId"] = 17
    quality = client.get("/api/database/overview?search=P00").json()["data"][0]["data_quality"]
    assert "battery_soc_percent" in quality["not_applicable_fields"]
    assert "battery_soc_percent" not in quality["missing_fields"]
    assert "load_power_kw" in quality["missing_fields"]


def test_summary_keeps_old_and_proposed_alarm_coverage_fields(client):
    data = client.get("/api/portfolio/summary").json()["data"]
    alarms = data["active_alarms"]
    assert alarms["configured"] is False and alarms["critical"] is None
    assert alarms["unconfigured_plants"] == alarms["coverage"]["unconfigured_plants"] == 38
    assert alarms["complete"] is False
    assert data["coverage"]["pv_power_kw"] == data["coverage"]["current_power_kw"]
    assert data["coverage"]["capacity_kwp"] == data["coverage"]["installed_pv_capacity_kwp"]
    assert data["environmental_benefits"]["standard_coal_saved_kg"] is None
    assert data["environmental_benefits"]["factor_source"] is None
    assert data["revenue"]["status"] == "unconfigured" and data["revenue"]["tariff_version"] is None


def test_empty_filtered_scope_preserves_source_configuration(client, monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList")
    body = client.get("/api/portfolio/summary?search=NO-SUCH-PLANT").json()
    assert body["data"]["total_plants"] == 0
    assert body["data"]["active_alarms"]["configured"] is True
    assert body["data"]["active_alarms"]["total"] == 0


def test_per_plant_alarm_query_filters_and_paginates(client, rows, monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList")
    rows[0].update(alarms_collected_at=utc_now(), alarms_payload={"success": True, "data": [
        {"alarmId": 1, "lev": 2, "status": 1, "acknowledged": True},
        {"alarmId": 2, "lev": 2, "status": 1},
        {"alarmId": 3, "lev": 4, "status": 1},
    ]})
    body = client.get("/api/alarms/P00?state=active&severity=major&page=1&page_size=1").json()
    assert body["total"] == 2 and len(body["data"]) == 1
    assert body["meta"]["total_pages"] == 2 and body["meta"]["returned"] == 1
    assert body["meta"]["coverage"] == body["coverage"]
    assert body["data"][0]["plant_name"] == "Plant 00"
    assert body["meta"]["filters_applied"]["severity"] == "major"
    assert client.get("/api/alarms/P00?page_size=101").status_code == 422


def test_statistics_proposal_aliases_and_bucket_coverage(client, monkeypatch):
    monkeypatch.setattr(fleet_repository, "energy_statistics", lambda *args: [])
    start = datetime(2026, 11, 8, tzinfo=timezone.utc)
    readings = [{"plant_code": "P00", "recorded_at": start + timedelta(minutes=i),
                 "total_yield_kwh": 100+i, "timestamp_source": "device", "data_source": "FUSIONSOLAR"}
                for i in range(0, 61, 10)]
    monkeypatch.setattr(fleet_repository, "counter_samples", lambda *args: readings)
    body = client.get("/api/database/yield-statistics", params={"plant_code": "P00", "from": start.isoformat(),
        "to": (start+timedelta(hours=2)).isoformat(), "grain": "hour", "time_zone": "Asia/Jakarta"}).json()
    assert body["data"]["from"] == body["data"]["from_at"]
    assert body["data"]["to"] == body["data"]["to_at"]
    assert body["data"]["scope"] == "plant" and body["data"]["plant_code"] == "P00"
    assert body["data"]["total_yield_kwh"] == 60 and body["meta"]["is_partial"] is True
    assert body["data"]["buckets"][0]["label"] == "07:00"
    assert body["meta"]["coverage"] == {"expected_buckets": 2, "complete_buckets": 1,
        "partial_buckets": 0, "missing_buckets": 1, "pending_buckets": 0}


def test_rankings_proposal_metadata(client, monkeypatch):
    monkeypatch.setattr(fleet_repository, "counter_samples", lambda *args: [])
    body = client.get("/api/database/rankings?date=2026-11-08&time_zone=Asia/Jakarta").json()
    assert body["meta"]["ranking_date"] == "2026-11-08"
    assert body["meta"]["eligible_plants"] == body["eligible_plants"] == 0
    assert body["meta"]["excluded_plants"] == body["excluded_plants"] == 38
    assert body["meta"]["excluded_reasons"]["missing_yield"] == 38


def test_telemetry_unavailable_has_retry_metadata_without_discarding_cache(client, rows):
    for key in ("recorded_at", "pv_power_kw", "yield_today_kwh", "total_yield_kwh"):
        rows[0][key] = None
    response = client.get("/api/dashboard/P00")
    assert response.status_code == 503 and response.json()["error"]["code"] == "TELEMETRY_UNAVAILABLE"
    assert response.json()["error"]["retry_after_seconds"] == config.EXPECTED_INTERVAL_SECONDS
    assert response.headers["Retry-After"] == str(config.EXPECTED_INTERVAL_SECONDS)
    rows[0]["recorded_at"] = utc_now()
    rows[0]["yield_today_kwh"] = 0
    assert client.get("/api/dashboard/P00").status_code == 200


def test_registration_message_metadata_and_validation_field_name(client, monkeypatch):
    monkeypatch.setattr(plant_admin_repository, "insert_plant", lambda payload: None)
    headers = {}
    response = client.post("/api/database/plants", json=registration(), headers=headers)
    assert response.status_code == 201 and response.json()["message"]
    assert response.json()["meta"]["time_zone"] == "Asia/Makassar"
    invalid = client.post("/api/database/plants", json={**registration(), "latitude": 91}, headers=headers)
    assert invalid.json()["error"]["fields"][0]["field"] == "latitude"
