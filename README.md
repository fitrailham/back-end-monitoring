# IconGreen Solar Monitoring Backend

A FastAPI backend that retrieves solar plant data from the FusionSolar Northbound
API, stores it in PostgreSQL, and provides monitoring data for the frontend.
The API reads from the database; the synchronization worker runs separately.

## Requirements

- Python 3.10 or later.
- PostgreSQL with the `public.plants`, `public.plant_realtime`,
  `public.fusionsolar_raw`, and `public.sync_logs` tables.
- An active Northbound account with access to the required plants and APIs.
- An active SSH tunnel if required for the database connection.

The application does not currently provide migrations or create tables automatically.

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

The worker retrieves the plant list at startup, then KPIs and alarms on each cycle.
The interval follows `SYNC_INTERVAL_MINUTES`, with a minimum of 10 minutes.
Alarm synchronization is skipped when `FUSIONSOLAR_ALARMS_PATH` is empty.
The worker does not currently retrieve device lists automatically.

## Troubleshooting

- Database connection fails: check the database settings and SSH tunnel.
- Login rejected: check the Northbound credentials, server/region, and account status.
- `failCode=407`: wait before retrying and avoid running multiple synchronizations
  concurrently with the same account.
- No data appears: check plant/API permissions and job results in `sync_logs`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
