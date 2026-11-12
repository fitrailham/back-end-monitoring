import httpx
import pytest

from app.services import fusionsolar_client
from app.services.fusionsolar_client import FusionSolarClient, FusionSolarError, NorthboundRateLimitError
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


def configure_rate_limit_test(monkeypatch, handler):
    monkeypatch.setenv("FUSIONSOLAR_BASE_URL", "https://example.test")
    monkeypatch.setenv("FUSIONSOLAR_USERNAME", "test")
    monkeypatch.setenv("FUSIONSOLAR_PASSWORD", "test")
    monkeypatch.setenv("FUSIONSOLAR_QUOTA_COOLDOWN_SECONDS", "600")
    clock = [1000.0]
    monkeypatch.setattr(fusionsolar_client.time, "monotonic", lambda: clock[0])
    client = FusionSolarClient()
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    return client, clock


def test_login_quota_blocks_other_jobs_and_direct_login_until_cooldown(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(200, json={"success": False, "failCode": 407})
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"success": True}, headers={"XSRF-TOKEN": "new-token"})
        return httpx.Response(200, json={"success": True, "data": []})

    client, clock = configure_rate_limit_test(monkeypatch, handler)
    with client:
        with pytest.raises(NorthboundRateLimitError) as blocked:
            client.get_devices("A")
        assert blocked.value.endpoint == "login"
        clock[0] += 1
        with pytest.raises(NorthboundRateLimitError) as remaining:
            client.get_station_kpi("A")
        assert remaining.value.retry_after_seconds == 599
        with pytest.raises(NorthboundRateLimitError):
            client.login()
        assert calls == ["/thirdData/login"]
        clock[0] += 599
        assert client.get_station_kpi("A")["success"] is True
        assert calls == ["/thirdData/login", "/thirdData/login", "/thirdData/getStationRealKpi"]


def test_305_followed_by_login_407_does_not_create_a_login_loop(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(200, json={"success": True}, headers={"XSRF-TOKEN": "old-token"})
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"success": False, "failCode": "407"})
        return httpx.Response(200, json={"success": False, "failCode": 305})

    client, _ = configure_rate_limit_test(monkeypatch, handler)
    with client:
        with pytest.raises(NorthboundRateLimitError):
            client.get_devices("A")
        with pytest.raises(NorthboundRateLimitError):
            client.get_station_kpi("A")
        assert calls == ["/thirdData/login", "/thirdData/getDevList", "/thirdData/login"]


def test_endpoint_quota_keeps_valid_session_and_does_not_retry_login(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"success": True}, headers={"XSRF-TOKEN": "valid-token"})
        if len(calls) == 2:
            return httpx.Response(200, json={"success": False, "failCode": 407})
        return httpx.Response(200, json={"success": True, "data": []})

    client, clock = configure_rate_limit_test(monkeypatch, handler)
    with client:
        with pytest.raises(NorthboundRateLimitError):
            client.get_devices("A")
        with pytest.raises(NorthboundRateLimitError):
            client.get_station_kpi("A")
        assert len(calls) == 2
        clock[0] += 600
        assert client.get_station_kpi("A")["success"] is True
        assert calls.count("/thirdData/login") == 1
