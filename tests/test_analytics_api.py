from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories import analytics_repository as repository
from app.schemas.analytics import DeviceHistoryResponse, InverterReportResponse, PowerStatisticsResponse
from app.services import analytics_service, fleet_service

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
NOW = START + timedelta(days=2)
POWER_PATH = "/api/database/power-statistics"
REPORT_PATH = "/api/reports/inverters"
HISTORY_PATH = "/api/devices/V1/history"


def window(**extra):
    return {"from": START.isoformat(), "to": END.isoformat(), "time_zone": "Asia/Jakarta", **extra}


def inverter(key):
    return {"id": key, "device_key": str(key), "device_id": f"V{key}", "device_name": f"Inverter {key}", "plant_code": "P"}


def power_sample(minutes, value=0, verified=True):
    return {"recorded_at": START + timedelta(minutes=minutes), "data_source": "FUSIONSOLAR",
        "timestamp_source": "device" if verified else "collector", "power_direction_convention": analytics_service.VERIFIED_DIRECTION,
        **{field: value for field in analytics_service.POWER_FIELDS}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(fleet_service, "get_rows", lambda code=None: [{"plant_code": "P", "is_demo": False}] if code in (None, "P") else [])
    monkeypatch.setattr(analytics_service, "utc_now", lambda: NOW)
    monkeypatch.setattr(repository, "plant_power_samples", lambda *args: [])
    monkeypatch.setattr(repository, "inverters", lambda codes: [inverter(1), inverter(2)])
    monkeypatch.setattr(repository, "device_counter_samples", lambda *args: [])
    monkeypatch.setattr(repository, "device", lambda plant, device: inverter(1) if (plant, device) == ("P", "V1") else None)
    monkeypatch.setattr(repository, "device_history", lambda *args: ([], 0))
    with TestClient(app) as client:
        yield client


def test_power_preserves_measured_zero_units_and_timezone(client, monkeypatch):
    monkeypatch.setattr(repository, "plant_power_samples", lambda *args: [power_sample(i) for i in range(0, 60, 10)])
    response = client.get(POWER_PATH, params=window(plant_code="P"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["unit"] == "kW"
    assert body["data"]["complete"] is True
    assert body["data"]["truncated"] is False
    assert len(body["data"]["buckets"]) == 6
    assert body["data"]["buckets"][0]["pv_power_kw"] == 0
    assert body["data"]["buckets"][0]["label_at"].endswith("+07:00")


def test_power_excludes_collector_time_and_unverified_directions(client, monkeypatch):
    source = [power_sample(0, 99, False), power_sample(10, 0)]
    source[1]["power_direction_convention"] = "unverified"
    monkeypatch.setattr(repository, "plant_power_samples", lambda *args: source)
    data = client.get(POWER_PATH, params=window(plant_code="P")).json()["data"]
    assert data["buckets"][0]["pv_power_kw"] is None
    assert data["buckets"][0]["coverage"]["pv_power_kw"]["quality"] == "unverified"
    assert data["buckets"][1]["pv_power_kw"] == 0
    assert data["buckets"][1]["grid_power_kw"] is None
    assert data["buckets"][1]["coverage"]["grid_power_kw"]["reason"] == "POWER_DIRECTION_UNVERIFIED"


def test_power_average_and_last_include_more_than_500_samples(client, monkeypatch):
    monkeypatch.setattr(repository, "plant_power_samples", lambda *args: [power_sample(i, i) for i in range(600)])
    query = window(plant_code="P", to=(START + timedelta(hours=10)).isoformat())
    average = client.get(POWER_PATH, params=query).json()["data"]
    last = client.get(POWER_PATH, params={**query, "aggregation": "last"}).json()["data"]
    assert len(average["buckets"]) == 60
    assert average["buckets"][-1]["pv_power_kw"] == pytest.approx(594.5)
    assert last["buckets"][-1]["pv_power_kw"] == 599


def test_clumped_samples_do_not_prove_complete_hour(client, monkeypatch):
    monkeypatch.setattr(repository, "plant_power_samples", lambda *args: [power_sample(i / 10, 1) for i in range(6)])
    body = client.get(POWER_PATH, params=window(plant_code="P", resolution_minutes=60)).json()
    coverage = body["data"]["buckets"][0]["coverage"]["pv_power_kw"]
    assert coverage["expected_samples"] == 6
    assert coverage["valid_samples"] == 1
    assert coverage["quality"] == "partial"


@pytest.mark.parametrize("patch", [
    {"from": "2026-01-01T00:00:00"}, {"time_zone": "Invalid/Zone"}, {"to": START.isoformat()},
    {"resolution_minutes": 0}, {"resolution_minutes": 7}, {"aggregation": "sum"},
    {"to": (START + timedelta(days=32)).isoformat()},
])
def test_power_rejects_invalid_range_and_aggregation(client, patch):
    response = client.get(POWER_PATH, params={**window(plant_code="P"), **patch})
    assert response.status_code == 422
    assert response.json()["success"] is False
    assert "request_id" in response.json()


def test_power_future_is_pending_and_unknown_plant_is_404(client):
    body = client.get(POWER_PATH, params=window(plant_code="P", **{"from": (NOW + timedelta(days=1)).isoformat(), "to": (NOW + timedelta(days=1, hours=1)).isoformat()})).json()
    assert all(b["quality"] == "pending" and b["pv_power_kw"] is None for b in body["data"]["buckets"])
    assert client.get(POWER_PATH, params=window(plant_code="missing")).status_code == 404


def test_inverter_report_totals_precede_pagination_and_zero_is_valid(client, monkeypatch):
    samples = [{"device_id": key, "recorded_at": START + timedelta(minutes=i * 10),
                "total_yield_kwh": 100 + (i if key == 2 else 0), "measurement_time_verified": True,
                "field_quality": {"total_yield_kwh": "VALID"}}
               for key in (1, 2) for i in range(7)]
    monkeypatch.setattr(repository, "device_counter_samples", lambda *args: samples)
    query = window(plant_code="P", grain="hour", page_size=1)
    first = client.get(REPORT_PATH, params=query)
    assert first.status_code == 200, first.text
    first = first.json()
    second = client.get(REPORT_PATH, params={**query, "page": 2}).json()
    assert first["total"] == 2
    assert first["data"][0]["inverter_yield_kwh"] == 0
    assert second["data"][0]["inverter_yield_kwh"] == 6
    assert first["totals"]["inverter_yield_kwh"] == second["totals"]["inverter_yield_kwh"] == 6
    assert first["meta"]["snapshot_id"] == second["meta"]["snapshot_id"]
    assert first["totals"]["complete_rows"] == 2


def test_report_missing_history_is_not_fabricated_from_plant_yield(client):
    response = client.get(REPORT_PATH, params=window(plant_code="P", grain="hour"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert all(r["inverter_yield_kwh"] is None and r["quality"] == "missing" for r in body["data"])
    assert body["totals"]["inverter_yield_kwh"] is None
    assert body["meta"]["reason"] == "DEVICE_TELEMETRY_UNAVAILABLE"


def test_report_unverified_counter_is_excluded(client, monkeypatch):
    monkeypatch.setattr(repository, "device_counter_samples", lambda *args: [{
        "device_id": 1, "recorded_at": START, "total_yield_kwh": 99, "measurement_time_verified": False}])
    body = client.get(REPORT_PATH, params=window(plant_code="P", grain="hour")).json()
    assert body["data"][0]["inverter_yield_kwh"] is None
    assert body["data"][0]["coverage"]["unverified"] == 1


def test_device_scope_and_ownership_are_enforced(client):
    assert client.get(REPORT_PATH, params=window(device_id="V1")).status_code == 422
    assert client.get(REPORT_PATH, params=window(plant_code="P", device_id="unknown")).status_code == 404
    assert client.get(HISTORY_PATH, params=window()).status_code == 422
    assert client.get("/api/devices/other/history", params=window(plant_code="P")).status_code == 404
    assert client.get(HISTORY_PATH, params=window(plant_code="missing")).status_code == 404


def test_device_history_masks_unverified_times_and_preserves_verified_zero(client, monkeypatch):
    def history(*args):
        return [{"sample_id": str(i), "measured_at": START, "collected_at": START,
            "measurement_time_verified": bool(i), "active_power_kw": 0, "grid_power_kw": 8,
            "power_direction_convention": "unverified", "status": "unknown", "field_quality": {}}
            for i in range(2)], 601
    monkeypatch.setattr(repository, "device_history", history)
    response = client.get(HISTORY_PATH, params=window(plant_code="P", page_size=1000))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"][0]["measured_at"] is None
    assert body["data"][0]["active_power_kw"] is None
    assert body["data"][1]["active_power_kw"] == 0
    assert body["data"][1]["grid_power_kw"] is None
    assert body["total"] == 601
    assert body["meta"]["page_size"] == 1000
    assert body["units"]["power"] == "kW"


def test_empty_device_history_has_explicit_source_reason(client):
    response = client.get(HISTORY_PATH, params=window(plant_code="P"))
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
    assert response.json()["meta"]["reason"] == "DEVICE_TELEMETRY_UNAVAILABLE"


def test_dense_source_returns_explicit_error_instead_of_silent_truncation(client, monkeypatch):
    monkeypatch.setattr(repository, "MAX_SOURCE_ROWS", 1)
    monkeypatch.setattr(repository, "plant_power_samples", lambda *args: [power_sample(0), power_sample(1)])
    response = client.get(POWER_PATH, params=window(plant_code="P"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SOURCE_RANGE_TOO_DENSE"


def test_analytics_database_failures_use_existing_safe_error_contract(client, monkeypatch):
    def unavailable(*args):
        raise psycopg.OperationalError("private connection detail")
    monkeypatch.setattr(repository, "plant_power_samples", unavailable)
    response = client.get(POWER_PATH, params=window(plant_code="P"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "private" not in response.text


def test_new_endpoints_are_documented_and_have_no_get_body(client):
    schema = client.get("/openapi.json").json()
    for path in (POWER_PATH, REPORT_PATH, "/api/devices/{device_id}/history"):
        operation = schema["paths"][path]["get"]
        assert "requestBody" not in operation
        assert "$ref" in operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert "software_version" in schema["components"]["schemas"]["Device"]["properties"]


def test_illustrative_fe_examples_match_public_contracts_and_swagger(client):
    fixtures = json.loads((Path(__file__).resolve().parents[1] / "docs/fe-analytics-examples.json").read_text(encoding="utf-8"))
    schema = client.get("/openapi.json").json()
    for name, record in fixtures["endpoints"].items():
        model = PowerStatisticsResponse if record["path"] == POWER_PATH else InverterReportResponse if record["path"] == REPORT_PATH else DeviceHistoryResponse
        model.model_validate(record["response"])
        examples = schema["paths"][record["path"]]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
        assert name in examples
