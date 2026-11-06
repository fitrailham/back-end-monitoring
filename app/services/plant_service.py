from app.repositories import plant_repository
from app.services.fusionsolar_client import extract_list
from app.services.kpi_mapper import map_station


def list_all():
    return plant_repository.list_plants()


def get_one(code: str):
    return plant_repository.get_plant(code)


def sync_stations(client) -> int:

    payload = client.get_stations()
    stations = extract_list(payload)

    if not stations:
        raise ValueError(
            "Daftar plant kosong atau format belum dikenali"
        )

    for row in stations:
        plant = map_station(row)
        plant_repository.upsert_plant(plant)

    return len(stations)