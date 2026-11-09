import httpx
import pytest

from app.services.fusionsolar_client import FusionSolarClient, FusionSolarError
from app.services.kpi_mapper import map_realtime


@pytest.mark.parametrize("failure, expected_requests", [(305, 2), (407, 1)])
def test_session_relogin_once_and_no_quota_retry(monkeypatch, failure, expected_requests):
    monkeypatch.setenv("FUSIONSOLAR_BASE_URL", "https://example.test")
    monkeypatch.setenv("FUSIONSOLAR_USERNAME", "test")
    monkeypatch.setenv("FUSIONSOLAR_PASSWORD", "test")
    calls = {"login": 0, "kpi": 0}
    def handler(request):
        if request.url.path.endswith("/login"):
            calls["login"] += 1
            return httpx.Response(200, json={"success": True}, headers={"XSRF-TOKEN": str(calls["login"])})
        calls["kpi"] += 1
        if calls["kpi"] == 1 or failure == 407:
            return httpx.Response(200, json={"success": False, "failCode": failure})
        return httpx.Response(200, json={"success": True, "data": []})
    with FusionSolarClient() as client:
        client.http.close()
        client.http = httpx.Client(transport=httpx.MockTransport(handler))
        if failure == 407:
            with pytest.raises(FusionSolarError):
                client.get_station_kpi("A")
        else:
            assert client.get_station_kpi("A")["success"] is True
    assert calls["kpi"] == calls["login"] == expected_requests


def test_collector_time_separate_and_snapshot_has_only_own_plant():
    payload = {"data": [
        {"stationCode": "A", "dataItemMap": {"day_power": 10, "real_health_state": 3}},
        {"stationCode": "B", "dataItemMap": {"day_power": 20}},
    ]}
    snapshot = map_realtime(payload, "A")
    raw = snapshot["raw_data"]
    assert raw["measured_at"] is None and raw["collected_at"] is not None
    assert raw["real_health_state"] == 3
    assert len(raw["response"]["data"]) == 1 and raw["response"]["data"][0]["stationCode"] == "A"


@pytest.mark.parametrize("value", [float('nan'), float('inf'), '-Infinity'])
def test_non_finite_kpi_is_unknown(value):
    snapshot = map_realtime({"data": [{"stationCode": "A", "dataItemMap": {"day_power": 1, "realTimePower": value}}]}, "A")
    assert snapshot["pv_power_kw"] is None
