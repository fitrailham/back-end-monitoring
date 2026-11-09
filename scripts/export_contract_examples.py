"""Export illustrative API fixtures. Never reads or writes a real database."""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg
from fastapi.testclient import TestClient

from app import config
from app.errors import ApiException
from app.main import app
from app.repositories import fleet_repository, plant_admin_repository
from app.services import alarm_service, fleet_service, portfolio_service, statistics_service


def main():
    now = datetime(2026, 11, 9, 1, tzinfo=timezone.utc)
    raw = {
        "plant_code": "PLANT-DEMO-001", "plant_name": "PLTS Contoh (fixture)",
        "capacity_kwp": 100, "address": "Alamat ilustratif", "is_demo": False,
        "metadata": {"country_code": "ID", "country_name": "Indonesia", "region": "Bali", "time_zone": "Asia/Makassar"},
        "recorded_at": now - timedelta(minutes=1), "pv_power_kw": 75,
        "yield_today_kwh": 500, "total_yield_kwh": 123456.7,
        "data_source": "FUSIONSOLAR", "snapshot_raw": {"sampling_time_source": "device", "real_health_state": 3,
            "collected_at": now.isoformat()},
        "inventory_collected_at": now - timedelta(days=1),
        "inventory_payload": {"success": True, "data": [
            {"stationCode": "PLANT-DEMO-001", "id": "fixture-inverter", "esnCode": "000EXAMPLE01", "devTypeId": 1,
             "optimizerNumber": 0, "latitude": -8.6, "longitude": 115.1},
            {"stationCode": "PLANT-DEMO-001", "id": "fixture-ess", "esnCode": "000EXAMPLE02", "devTypeId": 41,
             "latitude": -8.6, "longitude": 115.1},
        ]},
        "alarms_collected_at": now - timedelta(minutes=1),
        "alarms_payload": {"success": True, "data": [
            {"alarmId": 123, "esnCode": "000EXAMPLE01", "status": 1, "lev": 4,
             "alarmName": "Alarm ilustratif", "acknowledged": True, "raiseTime": int(now.timestamp() * 1000)},
        ]},
    }
    rows = [raw]
    start = datetime(2026, 11, 7, 16, tzinfo=timezone.utc)
    samples = [{"plant_code": raw["plant_code"], "recorded_at": start + timedelta(minutes=10 * i),
                "total_yield_kwh": 10000 + i, "timestamp_source": "device", "data_source": "FUSIONSOLAR", "id": i}
               for i in range(145)]
    result = {"notice": "EXAMPLE_ONLY: all responses use illustrative fixtures, never production data.",
              "endpoints": {}, "errors": {}}

    def fetch(code=None):
        return deepcopy([r for r in rows if code is None or r["plant_code"] == code])

    def insert(payload):
        registered = deepcopy(raw)
        registered.update(plant_code=payload.plant_code, plant_name=payload.plant_name,
                          capacity_kwp=payload.capacity_kwp, metadata=payload.model_dump(mode="json"),
                          inventory_payload=None, inventory_collected_at=None)
        rows.append(registered)

    with patch.object(fleet_repository, "fleet", side_effect=fetch), \
         patch.object(fleet_repository, "counter_samples", return_value=samples), \
         patch.object(config, "PLANT_WRITE_TOKEN", "fixture-write-token"), \
         patch.object(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList"), \
         patch.object(fleet_service, "utc_now", return_value=now), \
         patch.object(portfolio_service, "utc_now", return_value=now), \
         patch.object(statistics_service, "utc_now", return_value=now), \
         patch.object(alarm_service, "utc_now", return_value=now), \
         patch.object(plant_admin_repository, "insert_plant", side_effect=insert), \
         TestClient(app) as client:

        def capture(name, path, query=None, method="GET", body=None, actual_path=None, expected=200, error=False):
            headers = {"Authorization": "Bearer fixture-write-token"} if method != "GET" else {}
            response = client.request(method, actual_path or path, params=query, json=body, headers=headers)
            assert response.status_code == expected, (name, response.status_code, response.text)
            record = {"method": method, "path": path, "query": query or {},
                      "request_body": body, "status": response.status_code, "response": response.json()}
            result["errors" if error else "endpoints"][name] = record

        code = raw["plant_code"]
        capture("plants", "/api/database/plants")
        capture("plant_detail", "/api/database/plants/{plant_code}", actual_path=f"/api/database/plants/{code}")
        capture("overview", "/api/database/overview", {"page": 1, "page_size": 10, "device_type": "ess",
            "search": "Contoh", "country_code": "ID", "device_sn": "000EXAMPLE01", "status": "normal",
            "sort": "yield_today_kwh", "order": "desc"})
        capture("portfolio_summary", "/api/portfolio/summary")
        capture("yield_statistics", "/api/database/yield-statistics", {
            "plant_code": code, "from": start.isoformat(), "to": (start + timedelta(days=1)).isoformat(),
            "grain": "hour", "time_zone": "Asia/Makassar"})
        capture("rankings", "/api/database/rankings", {"date": "2026-11-08", "time_zone": "Asia/Makassar"})
        capture("devices", "/api/devices/{plant_code}", actual_path=f"/api/devices/{code}")
        capture("alarms_configured", "/api/alarms/{plant_code}", {"state": "active", "severity": "warning", "page": 1, "page_size": 20}, actual_path=f"/api/alarms/{code}")
        with patch.object(config, "FUSIONSOLAR_ALARMS_PATH", ""):
            capture("alarms_unconfigured", "/api/alarms/{plant_code}", actual_path=f"/api/alarms/{code}")
        capture("fleet_alarms", "/api/alarms", {"state": "active", "page": 1, "page_size": 10})
        body = {"plant_code": "LOCAL-EXAMPLE-001", "plant_name": "Registrasi ilustratif", "capacity_kwp": 100,
                "time_zone": "Asia/Makassar", "country_code": "ID", "latitude": -8.6, "longitude": 115.1}
        capture("register_plant", "/api/database/plants", method="POST", body=body, expected=201)
        capture("plant_not_found", "/api/database/plants/{plant_code}", actual_path="/api/database/plants/absent", expected=404, error=True)
        capture("invalid_query", "/api/database/overview", {"page_size": 101}, expected=422, error=True)
        with patch.object(plant_admin_repository, "insert_plant", side_effect=ApiException(409, "PLANT_ALREADY_EXISTS", "Kode PLTS sudah terdaftar.",
            fields=[{"field": "plant_code", "message": "Kode PLTS harus unik."}])):
            capture("duplicate_plant", "/api/database/plants", method="POST", body=body, expected=409, error=True)
        with patch.object(fleet_repository, "fleet", return_value=[{**raw, "recorded_at": None,
            "pv_power_kw": None, "yield_today_kwh": None, "total_yield_kwh": None}]):
            capture("telemetry_unavailable", "/api/dashboard/{plant_code}",
                    actual_path=f"/api/dashboard/{code}", expected=503, error=True)
        with patch.object(fleet_repository, "fleet", side_effect=psycopg.OperationalError("fixture offline")):
            capture("database_unavailable", "/api/database/overview", expected=503, error=True)

    target = ROOT / "docs" / "frontend-api-examples.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Exported", len(result["endpoints"]), "endpoint examples and", len(result["errors"]), "errors")


if __name__ == "__main__":
    main()
