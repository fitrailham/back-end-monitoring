from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.jobs import inventory_sync


def configure(monkeypatch):
    monkeypatch.setattr(inventory_sync.plant_repository, "list_plants", lambda: [
        {"plant_code": "A", "is_demo": False}, {"plant_code": "B", "is_demo": False}])
    monkeypatch.setattr(inventory_sync.plant_repository, "open_db", lambda: nullcontext("db"))
    monkeypatch.setattr(inventory_sync.sync_repository, "start_job", lambda *args: 1)
    finish, save = Mock(), Mock(return_value={"id": 1, "collected_at": datetime.now(timezone.utc)})
    upsert_devices, upsert_state = Mock(), Mock()
    monkeypatch.setattr(inventory_sync.sync_repository, "finish_job", finish)
    monkeypatch.setattr(inventory_sync.raw_repository, "save_raw", save)
    monkeypatch.setattr(inventory_sync.normalized_repository, "upsert_devices", upsert_devices)
    monkeypatch.setattr(inventory_sync.normalized_repository, "upsert_source_state", upsert_state)
    return finish, save, upsert_devices, upsert_state


def test_batch_inventory_uses_owner_code_and_preserves_empty_success(monkeypatch):
    finish, save, upsert_devices, upsert_state = configure(monkeypatch)
    client = Mock()
    client.get_devices.return_value = {"success": True, "data": [{"stationCode": "A", "id": "device-A"}]}
    assert inventory_sync.run(client) == 2
    client.get_devices.assert_called_once_with("A,B")
    assert save.call_args_list[0].args[2]["data"] == [{"stationCode": "A", "id": "device-A"}]
    assert save.call_args_list[1].args[2]["data"] == []
    assert upsert_devices.call_count == 2
    assert upsert_state.call_count == 2
    finish.assert_called_once_with(1, True, 2)


def test_incomplete_pagination_does_not_overwrite_complete_cache(monkeypatch):
    finish, save, upsert_devices, upsert_state = configure(monkeypatch)
    client = Mock()
    client.get_devices.return_value = {"success": True, "data": {"total": 10, "list": [{"stationCode": "A"}]}}
    with pytest.raises(ValueError, match="paginated"):
        inventory_sync.run(client)
    save.assert_not_called()
    assert finish.call_args.args[:2] == (1, False)
