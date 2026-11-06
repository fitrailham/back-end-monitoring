from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from app.jobs import realtime_sync


def configure_repositories(monkeypatch, codes):
    monkeypatch.setattr(
        realtime_sync.plant_repository, "list_plants",
        lambda: [{"plant_code": code, "is_demo": False} for code in codes]
        + [{"plant_code": "DEMO", "is_demo": True}],
    )
    monkeypatch.setattr(realtime_sync.plant_repository, "open_db", lambda: nullcontext("db"))
    monkeypatch.setattr(realtime_sync.sync_repository, "start_job", lambda *args: 1)
    finish = Mock()
    save = Mock()
    raw = Mock()
    monkeypatch.setattr(realtime_sync.sync_repository, "finish_job", finish)
    monkeypatch.setattr(realtime_sync.realtime_repository, "save_snapshot", save)
    monkeypatch.setattr(realtime_sync.raw_repository, "save_raw", raw)
    return finish, save, raw


@pytest.mark.parametrize("size", [38, 101])
def test_batches_real_plants_and_preserves_each_plants_metrics(monkeypatch, size):
    codes = [f"NE={i}" for i in range(size)]
    finish, save, raw = configure_repositories(monkeypatch, codes)
    client = Mock()

    def response(joined_codes):
        return {"data": [
            {"stationCode": code, "dataItemMap": {"day_power": int(code.split("=")[1])}}
            for code in reversed(joined_codes.split(","))
        ]}

    client.get_station_kpi.side_effect = response
    assert realtime_sync.run(client) == size
    assert [call.args[0] for call in client.get_station_kpi.call_args_list] == [
        ",".join(codes[offset:offset + 100]) for offset in range(0, size, 100)
    ]
    assert save.call_count == raw.call_count == size
    for index, call in enumerate(save.call_args_list):
        assert call.args[0]["plant_code"] == codes[index]
        assert call.args[0]["yield_today_kwh"] == index
    finish.assert_called_once_with(1, True, size)


def test_missing_plant_kpi_does_not_save_incomplete_batch(monkeypatch):
    finish, save, raw = configure_repositories(monkeypatch, ["NE=1", "NE=2"])
    client = Mock()
    client.get_station_kpi.return_value = {
        "data": [{"stationCode": "NE=1", "dataItemMap": {"day_power": 10}}]
    }
    with pytest.raises(ValueError, match="Tidak ada KPI"):
        realtime_sync.run(client)
    save.assert_not_called()
    raw.assert_not_called()
    assert finish.call_args.args[:3] == (1, False, 0)
