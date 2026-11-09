from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import config
from app.errors import ApiException
from app.main import app
from app.repositories import plant_admin_repository
from app.services import fleet_service
from app.services.values import utc_now


def sample_row(code="A", name="Alpha", device_type=41):
    now = utc_now()
    return {
        "plant_code": code, "plant_name": name, "capacity_kwp": 100, "address": None, "is_demo": False,
        "metadata": {"country_code": "ID", "time_zone": "Asia/Makassar", "region": "Bali"},
        "recorded_at": now, "pv_power_kw": 0, "yield_today_kwh": 500, "total_yield_kwh": 1000,
        "data_source": "FUSIONSOLAR",
        "snapshot_raw": {"sampling_time_source": "device", "collected_at": now.isoformat(), "real_health_state": 3},
        "inventory_collected_at": now - timedelta(days=3),
        "inventory_payload": {"success": True, "data": [
            {"id": 123, "esnCode": "000123", "stationCode": code, "devTypeId": device_type,
             "latitude": "-8.698207", "longitude": "115.186885", "devName": "Inventory device"},
            {"id": 456, "esnCode": "000456", "stationCode": code, "devTypeId": 1, "optimizerNumber": 0,
             "latitude": "-8.698207", "longitude": "115.186885"},
        ]},
        "alarms_payload": None, "alarms_collected_at": None,
    }


@pytest.fixture
def rows():
    return [sample_row(f"P{i:02}", f"Plant {i:02}") for i in range(38)]


@pytest.fixture
def client(monkeypatch, rows):
    monkeypatch.setattr(fleet_service, "get_rows", lambda code=None: [r for r in rows if code is None or r["plant_code"] == code])
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "")
    monkeypatch.setattr(config, "PLANT_WRITE_TOKEN", "")
    with TestClient(app) as client:
        yield client


def test_overview_default_preserves_fleet_and_opt_in_page(client):
    full = client.get("/api/database/overview").json()
    page = client.get("/api/database/overview?page=2&page_size=10").json()
    assert full["total"] == page["total"] == 38
    assert len(full["data"]) == 38 and len(page["data"]) == 10
    assert page["meta"]["total_pages"] == 4
    assert full["meta"]["snapshot_id"] == page["meta"]["snapshot_id"]
    assert page["data"][0]["plant_code"] == "P10"


def test_metadata_available_without_inventory_requests(client):
    row = client.get("/api/database/plants").json()["data"][0]
    assert row["latitude"] == -8.698207 and row["longitude"] == 115.186885
    assert row["location_source"] == "device_inventory" and row["location_verified"] is False
    assert row["has_ess"] is True and row["ess_rated_capacity_kwh"] is None
    assert row["optimizer_quantity"] == 0
    assert row["time_zone"] == "Asia/Makassar"


def test_sn_filter_uses_inventory_when_soc_missing(client):
    response = client.get("/api/database/overview", params={"device_type": "ess", "device_sn": "000123", "search": "P01"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["data"][0]["battery_soc_percent"] is None


def test_devices_preserve_serial_and_legacy_aliases(client):
    body = client.get("/api/devices/P00").json()
    assert body["plantCode"] == body["plant_code"] == "P00"
    assert body["data"][0]["device_sn"] == body["data"][0]["esnCode"] == "000123"
    assert body["data"][0]["device_id"] == "123"
    assert body["quality"] == "stale"


def test_vendor_non_finite_extra_is_serialized_as_null(client, rows):
    rows[0]["inventory_payload"]["data"][0]["vendor_extra"] = float("nan")
    body = client.get("/api/devices/P00").json()
    assert body["data"][0]["vendor_extra"] is None


def test_summary_uses_entire_scope_and_filter(client):
    client.get("/api/database/overview?page=1&page_size=10")
    body = client.get("/api/portfolio/summary").json()
    assert body["data"]["total_plants"] == 38
    assert body["data"]["kpis"]["installed_pv_capacity_kwp"] == 3800
    assert body["data"]["kpis"]["current_power_kw"] == 0
    assert sum(body["data"]["plant_status"].values()) == 38
    assert body["data"]["active_alarms"]["total"] is None
    assert body["data"]["revenue"]["status"] == "unconfigured"
    filtered = client.get("/api/portfolio/summary?search=P01").json()
    assert filtered["data"]["total_plants"] == 1


def test_date_summary_does_not_rebase_bali_daily_counter_to_jakarta(client):
    today = utc_now().astimezone(__import__('zoneinfo').ZoneInfo('Asia/Jakarta')).date()
    body = client.get("/api/portfolio/summary", params={"date": str(today), "time_zone": "Asia/Jakarta"}).json()
    assert body["data"]["kpis"]["yield_today_kwh"] is None
    assert body["data"]["coverage"]["yield_today_kwh"]["unverified"] == 38


def test_unknown_measurement_is_not_fresh_or_normal(client, rows):
    rows[0]["snapshot_raw"]["sampling_time_source"] = "collector"
    body = client.get("/api/database/overview?search=P00").json()["data"][0]
    assert body["data_quality"]["state"] == "unverified"
    assert body["measured_at"] is None and body["collected_at"] is not None
    assert body["status"] == "unknown"
    dashboard = client.get("/api/dashboard/P00").json()
    assert dashboard["stale"] is True
    assert dashboard["data"]["yieldToday"] == 0.5
    assert dashboard["normalized"]["yield_today_kwh"] == 500


def test_zero_capacity_never_produces_infinity(client, rows):
    rows[0]["capacity_kwp"] = 0
    body = client.get("/api/database/overview?search=P00").json()["data"][0]
    assert body["specific_energy_kwh_per_kwp"] is None
    assert "CAPACITY_MISSING_OR_NON_POSITIVE" in body["metadata_issues"]


@pytest.mark.parametrize("query", ["page=0", "page_size=101", "sort=invalid", "status=online"])
def test_invalid_query_has_stable_error(client, query):
    response = client.get("/api/database/overview?" + query)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_missing_plant_and_database_error(client, monkeypatch):
    assert client.get("/api/database/plants/absent").json()["error"]["code"] == "PLANT_NOT_FOUND"
    def unavailable(*args):
        raise psycopg.OperationalError("secret database message")
    monkeypatch.setattr(fleet_service, "get_rows", unavailable)
    response = client.get("/api/database/overview")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "secret" not in response.text


def registration():
    return {"plant_code": "P00", "plant_name": "Local", "capacity_kwp": 100,
            "time_zone": "Asia/Makassar", "latitude": -8.6, "longitude": 115.1}


def test_write_disabled_and_requires_token(client, monkeypatch):
    monkeypatch.setattr(plant_admin_repository, "insert_plant", lambda payload: pytest.fail("Must not write"))
    assert client.post("/api/database/plants", json=registration()).status_code == 403
    monkeypatch.setattr(config, "PLANT_WRITE_TOKEN", "test-token")
    assert client.post("/api/database/plants", json=registration()).status_code == 401


def test_register_success_conflict_and_validation(client, monkeypatch):
    monkeypatch.setattr(config, "PLANT_WRITE_TOKEN", "test-token")
    headers = {"Authorization": "Bearer test-token"}
    saved = []
    monkeypatch.setattr(plant_admin_repository, "insert_plant", saved.append)
    response = client.post("/api/database/plants", json=registration(), headers=headers)
    assert response.status_code == 201 and len(saved) == 1
    for changes in ({"latitude": 91}, {"longitude": None}, {"time_zone": "WIB"}, {"capacity_kwp": 0}, {"pv_power_kw": 10}):
        response = client.post("/api/database/plants", json={**registration(), **changes}, headers=headers)
        assert response.status_code == 422
    def conflict(payload):
        raise ApiException(409, "PLANT_ALREADY_EXISTS", "Kode PLTS sudah terdaftar.")
    monkeypatch.setattr(plant_admin_repository, "insert_plant", conflict)
    response = client.post("/api/database/plants", json=registration(), headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "PLANT_ALREADY_EXISTS"


def test_openapi_has_response_contracts(client):
    schema = client.get("/openapi.json").json()
    for path in ("/api/database/overview", "/api/database/plants", "/api/portfolio/summary",
                 "/api/database/yield-statistics", "/api/database/rankings", "/api/devices/{plant_code}", "/api/alarms",
                 "/api/dashboard/{plant_code}", "/api/database/history/{plant_code}", "/api/database/sync-logs"):
        assert "$ref" in schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    overview = schema["components"]["schemas"]["PlantOverview"]
    assert "data_quality" in overview["properties"] and "latitude" in overview["properties"]
    examples = schema["paths"]["/api/alarms/{plant_code}"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    assert examples["alarms_unconfigured"]["value"]["total"] is None
