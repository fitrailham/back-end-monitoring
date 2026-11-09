from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from app.jobs import inventory_sync


def configure(monkeypatch):
    monkeypatch.setattr(inventory_sync.plant_repository, "list_plants", lambda: [
        {"plant_code": "A", "is_demo": False}, {"plant_code": "B", "is_demo": False}])
    monkeypatch.setattr(inventory_sync.plant_repository, "open_db", lambda: nullcontext("db"))
    monkeypatch.setattr(inventory_sync.sync_repository, "start_job", lambda *args: 1)
    finish, save = Mock(), Mock()
    monkeypatch.setattr(inventory_sync.sync_repository, "finish_job", finish)
    monkeypatch.setattr(inventory_sync.raw_repository, "save_raw", save)
    return finish, save


def test_batch_inventory_uses_owner_code_and_preserves_empty_success(monkeypatch):
    finish, save = configure(monkeypatch)
    client = Mock()
    client.get_devices.return_value = {"success": True, "data": [{"stationCode": "A", "id": "device-A"}]}
    assert inventory_sync.run(client) == 2
    client.get_devices.assert_called_once_with("A,B")
    assert save.call_args_list[0].args[2]["data"] == [{"stationCode": "A", "id": "device-A"}]
    assert save.call_args_list[1].args[2]["data"] == []
    finish.assert_called_once_with(1, True, 2)


def test_incomplete_pagination_does_not_overwrite_complete_cache(monkeypatch):
    finish, save = configure(monkeypatch)
    client = Mock()
    client.get_devices.return_value = {"success": True, "data": {"total": 10, "list": [{"stationCode": "A"}]}}
    with pytest.raises(ValueError, match="paginated"):
        inventory_sync.run(client)
    save.assert_not_called()
    assert finish.call_args.args[:2] == (1, False)
