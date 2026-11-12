"""Generate FE fixtures without accessing a real database or Northbound."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from app.main import app
from app.repositories import analytics_repository as repository
from app.services import analytics_service, fleet_service


def main():
    start = datetime(2026, 11, 11, 17, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    now = end + timedelta(hours=1)
    code = "PLANT-EXAMPLE-001"
    devices = [{"id": i, "device_key": str(i), "device_id": f"INV-EXAMPLE-{i}",
                "plant_code": code, "device_name": f"Inverter ilustratif {i}"} for i in (1, 2)]
    query = {"plant_code": code, "from": start.isoformat(), "to": end.isoformat(), "time_zone": "Asia/Jakarta"}
    power = [{"recorded_at": start + timedelta(minutes=i * 10), "timestamp_source": "device",
              "data_source": "FUSIONSOLAR", "power_direction_convention": analytics_service.VERIFIED_DIRECTION,
              **{field: 0 if field != "pv_power_kw" else float(i) for field in analytics_service.POWER_FIELDS}}
             for i in range(6)]
    counters = [{"device_id": device["id"], "recorded_at": start + timedelta(minutes=i * 10),
                 "measurement_time_verified": True, "total_yield_kwh": 100 + i,
                 "field_quality": {"total_yield_kwh": "VALID"}} for device in devices for i in range(7)]
    history = [{"sample_id": "1", "measured_at": start, "collected_at": start + timedelta(seconds=2),
                "measurement_time_verified": True, "active_power_kw": 0, "pv_input_power_kw": 0,
                "power_direction_convention": "unverified", "status": "unknown", "field_quality": {"active_power_kw": "VALID"}}]
    examples = {"notice": "EXAMPLE_ONLY: synthetic fixtures, never production data.", "endpoints": {}, "errors": {}}
    with ExitStack() as stack:
        stack.enter_context(patch.object(fleet_service, "get_rows", return_value=[{"plant_code": code, "is_demo": False}]))
        stack.enter_context(patch.object(fleet_service, "utc_now", return_value=now))
        stack.enter_context(patch.object(analytics_service, "utc_now", return_value=now))
        power_mock = stack.enter_context(patch.object(repository, "plant_power_samples", return_value=power))
        stack.enter_context(patch.object(repository, "inverters", return_value=devices))
        counters_mock = stack.enter_context(patch.object(repository, "device_counter_samples", return_value=counters))
        stack.enter_context(patch.object(repository, "device", return_value=devices[0]))
        stack.enter_context(patch.object(repository, "device_history", return_value=(history, 1)))
        client = stack.enter_context(TestClient(app))

        def capture(name, path, params, actual=None, status=200):
            response = client.get(actual or path, params=params)
            assert response.status_code == status, response.text
            examples["endpoints" if status == 200 else "errors"][name] = {
                "method": "GET", "path": path, "query": params,
                "status": response.status_code, "response": response.json(),
            }

        capture("power_measured", "/api/database/power-statistics", {**query, "resolution_minutes": 10})
        power_mock.return_value = [{**p, "timestamp_source": "collector"} for p in power]
        capture("power_unverified", "/api/database/power-statistics", query)
        capture("inverter_report", "/api/reports/inverters", {**query, "grain": "hour", "page_size": 1})
        counters_mock.return_value = []
        capture("inverter_report_missing", "/api/reports/inverters", {**query, "grain": "hour"})
        capture("device_history", "/api/devices/{device_id}/history", query, actual="/api/devices/INV-EXAMPLE-1/history")
        capture("invalid_time_range", "/api/database/power-statistics", {**query, "to": query["from"]}, status=422)
        capture("device_scope_required", "/api/reports/inverters", {k: v for k, v in {**query, "device_id": "INV-EXAMPLE-1"}.items() if k != "plant_code"}, status=422)

    target = ROOT / "docs" / "fe-analytics-examples.json"
    target.write_text(json.dumps(examples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported {len(examples['endpoints'])} illustrative responses and {len(examples['errors'])} error responses to {target}")


if __name__ == "__main__":
    main()
