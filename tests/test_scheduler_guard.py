import subprocess
import sys
from threading import Event
from unittest.mock import MagicMock, Mock

from app.scheduler import scheduler
from app.scheduler.worker_lock import SchedulerAlreadyRunning, scheduler_lock
from app.services.fusionsolar_client import NorthboundRateLimitError
from app.sync_runtime import remaining_time


def test_os_lock_excludes_another_process_and_releases_after_exit(tmp_path):
    path = tmp_path / "scheduler lock"
    script = """
import sys
from pathlib import Path
from app.scheduler.worker_lock import SchedulerAlreadyRunning, scheduler_lock
try:
    with scheduler_lock(Path(sys.argv[1])):
        print('acquired')
except SchedulerAlreadyRunning:
    sys.exit(23)
"""
    def attempt():
        return subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=10)

    with scheduler_lock(path):
        blocked = attempt()
        assert blocked.returncode == 23, blocked.stderr
    available = attempt()
    assert available.returncode == 0, available.stderr
    assert "acquired" in available.stdout


def test_duplicate_scheduler_never_starts_jobs(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "scheduler_lock", Mock(side_effect=SchedulerAlreadyRunning))
    runner = Mock()
    monkeypatch.setattr(scheduler, "_run", runner)
    scheduler.main()
    runner.assert_not_called()
    event = next(record for record in caplog.records if getattr(record, "event", None) == "scheduler.duplicate")
    assert event.details["diagnostic"]["code"] == "WORKER_DUPLICATE"


def test_inventory_login_quota_skips_other_jobs_and_retries_next_cycle(monkeypatch, caplog):
    monkeypatch.setenv("FUSIONSOLAR_ENABLED", "true")
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "10")
    client = Mock()
    manager = MagicMock()
    manager.__enter__.return_value = client
    monkeypatch.setattr(scheduler, "FusionSolarClient", lambda: manager)
    monkeypatch.setattr(scheduler.plant_sync, "run", Mock())
    inventory = Mock(side_effect=[NorthboundRateLimitError("login", 1200), 1])
    realtime, devices, alarms = Mock(), Mock(), Mock()
    monkeypatch.setattr(scheduler.inventory_sync, "run", inventory)
    monkeypatch.setattr(scheduler.realtime_sync, "run", realtime)
    monkeypatch.setattr(scheduler.device_realtime_sync, "run", devices)
    monkeypatch.setattr(scheduler.alarm_sync, "run", alarms)
    sleeps = []
    def wait(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            realtime.assert_not_called()
            devices.assert_not_called()
            alarms.assert_not_called()
        else:
            return True
    stop = Mock()
    stop.is_set.return_value = False
    stop.wait.side_effect = wait

    scheduler._run(stop)

    assert sleeps == [1200, 600]
    assert inventory.call_count == 2
    realtime.assert_called_once_with(client)
    event = next(record for record in caplog.records if getattr(record, "event", None) == "scheduler.quota_wait")
    assert event.details["job"] == "inventory_sync"
    assert event.details["endpoint"] == "login"
    assert event.details["diagnostic"]["code"] == "API_QUOTA"


def test_shutdown_during_realtime_skips_later_jobs(monkeypatch):
    stop = Event()
    client = MagicMock()
    monkeypatch.setenv("FUSIONSOLAR_ENABLED", "true")
    monkeypatch.setattr(scheduler, "FusionSolarClient", lambda: client)
    monkeypatch.setattr(scheduler.plant_sync, "run", Mock())
    monkeypatch.setattr(scheduler.inventory_sync, "run", Mock())

    def realtime(_):
        stop.set()
        remaining_time()

    monkeypatch.setattr(scheduler.realtime_sync, "run", realtime)
    devices, alarms = Mock(), Mock()
    monkeypatch.setattr(scheduler.device_realtime_sync, "run", devices)
    monkeypatch.setattr(scheduler.alarm_sync, "run", alarms)
    scheduler._run(stop)
    devices.assert_not_called()
    alarms.assert_not_called()
    client.__exit__.assert_called_once()
