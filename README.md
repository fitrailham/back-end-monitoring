# IconGreen Solar Monitoring Backend

A FastAPI backend that retrieves solar plant data from the FusionSolar Northbound
API, stores it in PostgreSQL, and provides monitoring data for the frontend.
The API reads from the database; the synchronization worker runs separately.

## Requirements

- Python 3.10 or later.
- PostgreSQL with the four legacy tables plus `plant_metadata`, `devices`,
  `device_realtime`, `plant_energy_statistics`, `alarms`, `plant_source_state`,
  `energy_tariffs`, and `environmental_factors`.
- An active Northbound account with access to the required plants and APIs.
- An active SSH tunnel if required for the database connection.

The application does not create tables automatically. The schema must be created by
the database administrator. The provided migration only adds supporting indexes.

## Installation (PowerShell)

Run these commands from the project directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Copy the template only when creating `.env` for the first time to avoid
overwriting an existing configuration.

Edit `.env` with your database connection and Northbound account settings. Check:

- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- `FUSIONSOLAR_BASE_URL`: the HTTPS URL for your account's server/region.
- `FUSIONSOLAR_USERNAME` and `FUSIONSOLAR_PASSWORD`: Northbound account credentials.
- `CORS_ORIGINS`: frontend origins allowed to access the API.

Do not commit `.env` or include passwords or tokens in the README.
`.env.example` contains example configuration values only.

## Running the API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

- API documentation: http://127.0.0.1:8000/docs
- Database connection test: `GET /api/database/test`
- Plant list: `GET /api/database/plants`
- Monitoring overview: `GET /api/database/overview`
- Dashboard: `GET /api/dashboard/{plant_code}`
- Synchronization history: `GET /api/database/sync-logs`

Use `--reload` for local development.

## One-time Database Synchronization

This command retrieves the plant list and KPIs from Northbound and writes them
to the database configured in `.env`:

```powershell
@"
from app.jobs import plant_sync, realtime_sync
from app.services.fusionsolar_client import FusionSolarClient

with FusionSolarClient() as client:
    print("Plants saved:", plant_sync.run(client))
    print("KPI snapshots saved:", realtime_sync.run(client))
"@ | .\.venv\Scripts\python.exe -
```

KPIs are retrieved in batches of up to 100 plants per request to reduce API calls.
Raw responses are stored in `fusionsolar_raw`, snapshots in `plant_realtime`,
and job results in `sync_logs`.
Fields not provided by the API remain `null`. The collector's timestamp is used
when a response does not include a measurement timestamp.

## Scheduled Synchronization

Set `FUSIONSOLAR_ENABLED=true` in `.env`, then run this in a separate terminal:

```powershell
.\.venv\Scripts\python.exe -m app.scheduler.scheduler
```

The worker retrieves the plant list at startup, then station KPIs and alarms on each cycle.
The interval follows `SYNC_INTERVAL_MINUTES`, with a minimum of 10 minutes.
Alarm synchronization is skipped when `FUSIONSOLAR_ALARMS_PATH` is empty.
Device inventory is refreshed at startup and every 24 hours. Device realtime KPI
remains disabled until `FUSIONSOLAR_DEVICE_REALTIME_ENABLED=true` because it consumes
additional Northbound quota. Signed grid/battery power remains null until
`FUSIONSOLAR_POWER_DIRECTION_CONVENTION=grid_import_battery_discharge_positive` is
explicitly configured after the source sign has been verified.

## Troubleshooting

- Database connection fails: check the database settings and SSH tunnel.
- Login rejected: check the Northbound credentials, server/region, and account status.
- `failCode=407`: wait before retrying and avoid running multiple synchronizations
  concurrently with the same account.
- No data appears: check plant/API permissions and job results in `sync_logs`.

## Frontend API contract

Kontrak frontend dan langkah integrasi ada di [docs/frontend-api.md](docs/frontend-api.md).
Endpoint baru: `GET /api/portfolio/summary`, `GET /api/database/yield-statistics`,
`GET /api/database/rankings`, dan `GET /api/alarms`.
Metadata lokasi/perangkat tersedia langsung pada plants dan overview.
Overview tanpa `page`/`page_size` tetap mengembalikan seluruh PLTS.

Setelah seluruh tabel normalisasi dibuat, indeks pendukung dapat diterapkan secara
terpisah setelah memeriksa target database pada `.env`:

```powershell
.\.venv\Scripts\python.exe -m app.migrate
```

Migrasi hanya menambahkan indeks dan tidak mengisi data. API membaca `plant_metadata`
dan `devices`, dengan fallback ke cache legacy selama tabel baru masih kosong.
POST/PATCH memerlukan tabel baru dan `PLANT_WRITE_TOKEN` yang
tidak kosong, dikirim melalui `Authorization: Bearer <token>`. Jangan menanamkan token
administrator ke bundle frontend publik. Tanpa token konfigurasi, operasi tulis ditolak.

Worker memperbarui inventory perangkat saat startup dan setiap 24 jam.
Metadata inventory tidak digunakan sebagai bukti freshness telemetry.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
