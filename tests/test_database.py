from unittest.mock import patch

from app.repositories import plant_repository


def test_get_plant_found():

    fake = [{
        "plant_code": "NE=TEST",
        "plant_name": "Test PLTS"
    }]

    with patch.object(
        plant_repository,
        "fetch_rows",
        return_value=fake
    ):

        result = plant_repository.get_plant(
            "NE=TEST"
        )

    assert result["plant_name"] == "Test PLTS"


def test_get_plant_missing():

    with patch.object(
        plant_repository,
        "fetch_rows",
        return_value=[]
    ):

        assert plant_repository.get_plant(
            "UNKNOWN"
        ) is None