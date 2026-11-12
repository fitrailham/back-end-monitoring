import httpx
import psycopg
import pytest

from app.services.fusionsolar_client import FusionSolarError
from app.services.sync_diagnostics import failure_guidance


@pytest.mark.parametrize("exc, category", [
    (psycopg.errors.DatatypeMismatch("private-payload"), "DB_VALUE_TYPE"),
    (psycopg.errors.UndefinedTable("private-payload"), "DB_SCHEMA"),
    (psycopg.OperationalError("private-payload"), "DB_CONNECTION"),
    (httpx.ReadTimeout("private-payload"), "API_TIMEOUT"),
    (httpx.ConnectError("private-payload"), "API_CONNECTION"),
    (FusionSolarError("private-payload failCode=407"), "API_QUOTA"),
    (FusionSolarError("private-payload failCode=305"), "API_SESSION"),
    (FusionSolarError("private-payload failCode=4070"), "API_REJECTED"),
    (ValueError("private-payload"), "DATA_FORMAT"),
])
def test_guidance_classifies_failure_without_copying_exception_payload(exc, category):
    guidance = failure_guidance(exc)
    assert f"[{category}]" in guidance
    assert "Solusi:" in guidance
    assert "private-payload" not in guidance


def test_http_rate_limit_is_distinct_from_authentication_failure():
    request = httpx.Request("POST", "https://example.test/thirdData/getStationRealKpi")
    for status, category in [(429, "API_RATE_LIMIT"), (403, "API_ACCESS"), (503, "API_HTTP")]:
        exc = httpx.HTTPStatusError("private-payload", request=request, response=httpx.Response(status, request=request))
        assert f"[{category}]" in failure_guidance(exc)
