from datetime import datetime, timezone

import pytest

from app.services.kpi_mapper import (
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