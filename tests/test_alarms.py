from datetime import timedelta

from app import config
from app.services import alarm_service
from app.services.values import utc_now


def row(code="A", data=None):
    return {"plant_code": code, "alarms_payload": {"success": True, "data": data or []},
            "alarms_collected_at": utc_now()}


def test_unconfigured_is_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "")
    response = alarm_service.fleet_alarms([row()])
    assert response["total"] is None and response["data"] == []
    assert response["coverage"]["unconfigured_plants"] == 1
    assert response["coverage"]["complete"] is False


def test_configured_empty_is_known_zero(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList")
    response = alarm_service.fleet_alarms([row()])
    assert response["total"] == 0 and response["coverage"]["complete"] is True


def test_active_acknowledged_alarm_and_fleet_pagination(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList")
    alarms = [
        {"alarmId": 1, "status": 1, "lev": 2, "esnCode": "000001", "acknowledged": True},
        {"alarmId": 2, "state": "cleared", "severity": "critical"},
        {"alarmId": 3, "status": 1, "lev": 4},
    ]
    rows = [row(data=alarms)]
    summary = alarm_service.active_summary(rows)
    page = alarm_service.fleet_alarms(rows, state="active", page=1, page_size=1)
    assert summary["total"] == page["total"] == 2
    assert len(page["data"]) == 1
    assert summary["severity"]["major"] == 1 and summary["severity"]["warning"] == 1


def test_stale_and_failed_cache_remains_partial(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList")
    cached = row(data=[{"alarmId": 1, "status": 1, "lev": 2}])
    cached.update(alarms_collected_at=utc_now() - timedelta(hours=1),
                  alarm_job_status="FAILED", alarm_job_started_at=utc_now())
    response = alarm_service.fleet_alarms([cached])
    assert response["total"] == 1 and response["quality"] == "stale"
    assert response["coverage"]["failed_plants"] == 1 and response["coverage"]["complete"] is False


def test_unknown_alarm_state_does_not_prove_no_fault(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList")
    response = alarm_service.active_summary([row(data=[{"alarmId": 1}])])
    assert response["coverage"]["complete"] is False


def test_null_source_list_is_not_a_verified_empty_result(monkeypatch):
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "getAlarmList")
    source = row()
    source["alarms_payload"]["data"] = None
    response = alarm_service.fleet_alarms([source])
    assert response["total"] is None and response["coverage"]["missing_plants"] == 1
