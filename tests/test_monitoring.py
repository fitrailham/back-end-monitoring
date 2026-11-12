from datetime import datetime, timezone

import pytest

from app.services.kpi_mapper import (
    NoUsableKpiError,
    map_station,
    map_realtime
)

from app.services.monitoring_service import (
    format_dashboard
)


def test_station_capacity_mw_to_kwp():

    station = map_station({
        "stationCode": "NE=1",
        "stationName": "A",
        "capacity": 0.9
    })

    assert station["capacity_kwp"] == 900.0


def test_map_realtime_avoids_fabricated_sensor_data():

    raw = {
        "data": [{
            "stationCode": "NE=1",
            "dataItemMap": {
                "realTimePower": 120
            }
        }]
    }

    snapshot = map_realtime(
        raw,
        "NE=1"
    )

    assert snapshot["pv_power_kw"] == 120

    assert snapshot["battery_soc_percent"] is None


def test_unknown_kpi_does_not_write_fake_zero():

    with pytest.raises(ValueError):

        map_realtime({
            "data": [{
                "dataItemMap": {
                    "unknown": 1
                }
            }]
        }, "NE=1")


@pytest.mark.parametrize("invalid", [None, "", "invalid", "NaN", float("inf"), True])
def test_realtime_uses_valid_alias_when_primary_value_is_invalid(invalid):
    snapshot = map_realtime({"data": [{
        "stationCode": "NE=1",
        "dataItemMap": {"day_power": invalid, "yield_today_kwh": 0},
    }]}, "NE=1")

    assert snapshot["yield_today_kwh"] == 0
    assert snapshot["pv_power_kw"] is None


@pytest.mark.parametrize("kpis, reason", [
    ({}, "dataItemMap kosong"),
    ({"real_health_state": 3}, "tidak ada field metrik yang dikenali"),
    ({"day_power": None, "total_power": "NaN"}, "semua nilai metrik"),
])
def test_unusable_kpi_identifies_plant_and_fields_without_logging_values(kpis, reason):
    with pytest.raises(NoUsableKpiError) as caught:
        map_realtime({"data": [{"stationCode": "NE=1", "dataItemMap": kpis}]}, "NE=1")

    assert caught.value.plant_code == "NE=1"
    assert "NE=1" in str(caught.value)
    assert reason in str(caught.value)
    assert set(caught.value.fields) == set(kpis)


def test_dashboard_converts_energy_only():

    result = format_dashboard({
        "plant_code": "NE=1",
        "plant_name": "A",
        "is_demo": False,
        "capacity_kwp": 500.0,
        "recorded_at": datetime.now(timezone.utc),
        "yield_today_kwh": 2500.0,
        "total_yield_kwh": 100000.0,
        "pv_power_kw": 90.0,
        "data_source": "FUSIONSOLAR"
    })

    assert result["data"]["yieldToday"] == 2.5

    assert result["data"]["currentPvPower"] == 90.0

    assert result["stale"] is False
