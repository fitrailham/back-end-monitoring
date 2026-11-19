from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from app.jobs import realtime_sync
from app.logging_config import JsonLogFormatter
from app.scheduler import scheduler
from app.services.fusionsolar_client import FusionSolarError


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
    raw = Mock(return_value={"id": 123})
    state = Mock()
    monkeypatch.setattr(realtime_sync.sync_repository, "finish_job", finish)
    monkeypatch.setattr(realtime_sync.realtime_repository, "save_snapshot", save)
    monkeypatch.setattr(realtime_sync.raw_repository, "save_raw", raw)
    monkeypatch.setattr(realtime_sync.normalized_repository, "upsert_source_state", state)
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


@pytest.mark.parametrize("unusable", [
    {},
    {"real_health_state": 1},
    {"day_power": None, "total_power": ""},
    {"day_power": "NaN"},
    {"vendor_metric": 987654321},
])
def test_unusable_plant_preserves_other_snapshots_raw_data_and_failure_state(monkeypatch, caplog, unusable):
    finish, save, raw = configure_repositories(monkeypatch, ["NE=1", "NE=2", "NE=3"])
    client = Mock()
    client.get_station_kpi.return_value = {"data": [
        {"stationCode": "NE=2", "dataItemMap": unusable},
        {"stationCode": "NE=3", "dataItemMap": {"day_power": 20}},
        {"stationCode": "NE=1", "dataItemMap": {"day_power": 0}},
    ]}

    assert realtime_sync.run(client) == 2
    assert [call.args[0]["plant_code"] for call in save.call_args_list] == ["NE=1", "NE=3"]
    assert [call.args[0]["yield_today_kwh"] for call in save.call_args_list] == [0, 20]
    assert raw.call_count == 3
    assert raw.call_args_list[1].args == ("NE=2", "getStationRealKpi", client.get_station_kpi.return_value, "db")
    states = realtime_sync.normalized_repository.upsert_source_state.call_args_list
    assert [call.kwargs["status"] for call in states] == ["success", "failed", "success"]
    assert states[1].kwargs["complete"] is False
    assert states[1].kwargs["count"] == 0
    assert "NE=2" in states[1].kwargs["error"]
    assert finish.call_args.args[:3] == (1, False, 2)
    assert "1 dari 3" in finish.call_args.args[3]
    warning = next(record for record in caplog.records if getattr(record, "event", None) == "realtime.snapshot_skipped")
    assert "987654321" not in JsonLogFormatter().format(warning)
    assert warning.details["plant_code"] == "NE=2"
    assert warning.details["diagnostic"]["solution"]
    assert warning.details["impact"]
    assert warning.details["evidence"] == {
        "table": "public.fusionsolar_raw", "raw_id": 123,
        "source_state_table": "public.plant_source_state",
    }
    summary = next(record for record in caplog.records if getattr(record, "event", None) == "realtime.summary")
    assert summary.details["saved"] == 2 and summary.details["skipped"] == 1
    assert "solution" not in str(summary.details)


def test_all_unusable_plants_record_failure_without_overwriting_snapshots(monkeypatch):
    finish, save, raw = configure_repositories(monkeypatch, ["NE=1", "NE=2"])
    client = Mock()
    client.get_station_kpi.return_value = {"data": [
        {"stationCode": code, "dataItemMap": {"day_power": None}}
        for code in ["NE=1", "NE=2"]
    ]}

    assert realtime_sync.run(client) == 0
    save.assert_not_called()
    assert raw.call_count == 2
    states = realtime_sync.normalized_repository.upsert_source_state.call_args_list
    assert all(call.kwargs["status"] == "failed" for call in states)
    assert finish.call_args.args[:3] == (1, False, 0)
    assert "2 dari 2" in finish.call_args.args[3]


def test_unusable_plant_can_recover_on_the_next_run(monkeypatch):
    finish, save, raw = configure_repositories(monkeypatch, ["NE=1"])
    client = Mock()
    client.get_station_kpi.side_effect = [
        {"data": [{"stationCode": "NE=1", "dataItemMap": {}}]},
        {"data": [{"stationCode": "NE=1", "dataItemMap": {"day_power": 0}}]},
    ]

    assert realtime_sync.run(client, "NE=1") == 0
    assert realtime_sync.run(client, "NE=1") == 1
    save.assert_called_once()
    assert raw.call_count == 2
    states = realtime_sync.normalized_repository.upsert_source_state.call_args_list
    assert [call.kwargs["status"] for call in states] == ["failed", "success"]
    assert finish.call_args.args == (1, True, 1)


def test_unusable_kpi_does_not_skip_later_batches(monkeypatch):
    codes = [f"NE={i}" for i in range(101)]
    finish, save, raw = configure_repositories(monkeypatch, codes)
    client = Mock()
    client.get_station_kpi.side_effect = lambda joined_codes: {"data": [
        {"stationCode": code, "dataItemMap": {} if code == "NE=0" else {"day_power": 1}}
        for code in joined_codes.split(",")
    ]}

    assert realtime_sync.run(client) == 100
    assert client.get_station_kpi.call_count == 2
    assert save.call_count == 100
    assert raw.call_count == 101
    assert save.call_args.args[0]["plant_code"] == "NE=100"
    assert finish.call_args.args[:3] == (1, False, 100)


def test_api_failure_still_raises_and_preserves_completed_batch_status(monkeypatch, caplog):
    codes = [f"NE={i}" for i in range(101)]
    finish, save, raw = configure_repositories(monkeypatch, codes)
    client = Mock()
    client.get_station_kpi.side_effect = [
        {"data": [{"stationCode": code, "dataItemMap": {"day_power": 1}} for code in codes[:100]]},
        FusionSolarError("Northbound getStationRealKpi gagal, failCode=407"),
    ]

    with pytest.raises(FusionSolarError, match="407"):
        realtime_sync.run(client)

    assert client.get_station_kpi.call_count == 2
    assert save.call_count == raw.call_count == 100
    states = realtime_sync.normalized_repository.upsert_source_state.call_args_list
    assert len(states) == 101
    assert all(call.kwargs["status"] == "success" for call in states[:100])
    assert states[-1].args[0] == "NE=100"
    assert states[-1].kwargs["status"] == "failed"
    assert finish.call_args.args[:3] == (1, False, 100)
    failure = next(record for record in caplog.records if getattr(record, "event", None) == "realtime.failed")
    assert failure.details["diagnostic"]["code"] == "API_QUOTA"
    assert failure.details["saved"] == 100


def test_database_failure_is_not_treated_as_unavailable_kpi(monkeypatch):
    finish, save, raw = configure_repositories(monkeypatch, ["NE=1"])
    client = Mock()
    client.get_station_kpi.return_value = {
        "data": [{"stationCode": "NE=1", "dataItemMap": {"day_power": 1}}]
    }
    save.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        realtime_sync.run(client)

    assert finish.call_args.args[:3] == (1, False, 0)
    state = realtime_sync.normalized_repository.upsert_source_state.call_args
    assert state.kwargs["status"] == "failed"


def test_failed_transaction_does_not_log_uncommitted_raw_evidence(monkeypatch, caplog):
    configure_repositories(monkeypatch, ["NE=1"])
    client = Mock()
    client.get_station_kpi.return_value = {
        "data": [{"stationCode": "NE=1", "dataItemMap": {}}]
    }
    realtime_sync.normalized_repository.upsert_source_state.side_effect = [RuntimeError("write failed"), None]

    with pytest.raises(RuntimeError, match="write failed"):
        realtime_sync.run(client)

    assert not any(getattr(record, "event", None) == "realtime.snapshot_skipped" for record in caplog.records)


def test_scheduler_continues_device_and_alarm_jobs_after_unusable_station_kpi(monkeypatch, caplog):
    finish, save, raw = configure_repositories(monkeypatch, ["NE=1"])
    client = Mock()
    client.get_station_kpi.return_value = {
        "data": [{"stationCode": "NE=1", "dataItemMap": {"real_health_state": 1}}]
    }
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(scheduler, "FusionSolarClient", lambda: context)
    monkeypatch.setenv("FUSIONSOLAR_ENABLED", "true")
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "10")
    monkeypatch.setattr(scheduler.plant_sync, "run", Mock())
    monkeypatch.setattr(scheduler.inventory_sync, "run", Mock())
    device_sync, alarm_sync = Mock(), Mock()
    monkeypatch.setattr(scheduler.device_realtime_sync, "run", device_sync)
    monkeypatch.setattr(scheduler.alarm_sync, "run", alarm_sync)
    stop = Mock()
    stop.is_set.return_value = False
    stop.wait.return_value = True

    # Test the cycle independently from a real worker holding the checkout lock.
    scheduler._run(stop)

    device_sync.assert_called_once_with(client)
    alarm_sync.assert_called_once_with(client)
    save.assert_not_called()
    raw.assert_called_once()
    assert finish.call_args.args[:3] == (1, False, 0)
    assert "Siklus gagal" not in caplog.text
