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

If a plant returns an empty KPI map, unrecognised metric names, or only invalid/null
metric values, the worker keeps its raw response for diagnosis and records a
failed source state with the plant code and received field names. It does not
write an empty snapshot or replace the plant's previous readings with zeros.
Other plants and later batches continue synchronising; device and alarm jobs
still run on schedule. The realtime job is recorded as `FAILED` in `sync_logs`
with the number of valid snapshots saved and details of the skipped plants,
including when every plant is skipped. API and database failures still raise an
error. No additional Northbound requests are made for skipped plants.

## Scheduled Synchronization

Set `FUSIONSOLAR_ENABLED=true` in `.env`, then run this in a separate terminal:

```powershell
.\.venv\Scripts\python.exe -m app.scheduler.scheduler
```

The worker retrieves the plant list at startup, then station KPIs and alarms on each cycle.
The interval follows `SYNC_INTERVAL_MINUTES`, with a minimum of 10 minutes.
Restart the worker after changing Python code: stop the existing scheduler with
`Ctrl+C`, then run the command above again. The scheduler does not hot-reload
imported modules. If source files change while it runs, a traceback can display
new source lines even though the process is still executing the old code.
The startup `Runtime` message identifies the PID, interpreter, and realtime
module path so the running checkout can be verified.
Only one scheduler may run per checkout. A second instance exits before login
with `[WORKER_DUPLICATE]`. `.scheduler.lock` is an OS-held lock; a leftover file
does not mean a worker is active, and must not be deleted while a worker runs.
This local guard does not cover other computers or applications sharing the
same Northbound account; use one collector per account or separate accounts.
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
  HTTP 200 only confirms delivery; a JSON body with `success=false` still rejects
  the operation. The client applies an account-wide local cooldown of at least
  600 seconds, configurable through `FUSIONSOLAR_QUOTA_COOLDOWN_SECONDS`. It makes
  no additional HTTP/login calls during that cooldown. A scheduler quota failure
  skips the remaining jobs in the cycle, waits at least the cooldown, and keeps
  a quota-blocked inventory sync due for retry instead of delaying it for 24 hours.
  This delay is a local safeguard, not a guarantee that Huawei's quota has reset.
  Avoid repeated restarts: they create new login attempts and reset local state.
- No data appears: check plant/API permissions and job results in `sync_logs`.
- `[REALTIME SYNC] Snapshot dilewati`: check the plant code and field names in
  the warning and `plant_source_state.last_error`, then inspect the response in
  `fusionsolar_raw`. Empty/null values require source data to become available;
  unknown field names require a verified mapping. The next scheduled cycle
  retries the normal batched request.

KPI warnings include a reason code, received field names, suggested action
(`Solusi`), impact (`Dampak`), job ID, and the committed `fusionsolar_raw` row ID.
`KPI_EMPTY` means no KPI fields were supplied; `KPI_INVALID_VALUES` means known
metrics contain no valid numeric values; `KPI_STATUS_ONLY` means only status/income
fields were supplied; `KPI_FIELDS_UNKNOWN` means the metric names need verification.
Status/income fields are never substituted for power or energy measurements.
The same reason and suggested action are kept in the failed source state.
Scheduler errors identify the failing job and give guidance for API quota/session,
network, database connection/schema/type errors. Tracebacks remain available for
diagnosis. Raw evidence IDs are logged only after a successful database commit.

### Log Scheduler FusionSolar

Terminal scheduler menampilkan `[HH:mm:ss] LEVEL COMPONENT MESSAGE` melalui Rich.
`SUCCESS` berwarna hijau, `WARNING` kuning, dan `ERROR` merah pada terminal yang
mendukung warna. Saat stdout diarahkan ke file, format yang sama memakai teks
polos. Audit tersimpan di `logs/scheduler.audit.jsonl` sebagai satu objek JSON
lengkap per baris.

`LOG_LEVEL` (default `INFO`) dan `LOG_TIME_ZONE` (default `Asia/Jakarta`) berlaku
untuk terminal dan audit. API tetap memakai `LOG_FORMAT=pretty_json` atau
`LOG_FORMAT=json`.

`realtime.snapshot_skipped` memisahkan `job_id`, `plant_code`, `endpoint`,
`diagnostic.code/reason/received_fields/solution`, `impact`, dan referensi
`evidence.table/raw_id`. `realtime.summary` hanya memuat jumlah berhasil/dilewati
dan daftar plant terkait; uraian penyebab tidak diulang. `status=partial` pada
ringkasan menjelaskan hasil parsial, sedangkan `job_status=FAILED` tetap mengikuti
status yang disimpan di `sync_logs`. Exception beserta traceback ditulis sebagai
field JSON, bukan baris teks di luar JSON. Logging bawaan server Uvicorn tetap
mengikuti konfigurasi servernya.

## Frontend API contract

API baca prioritas terbaru untuk range daya, laporan inverter, dan history
perangkat beserta aturan kualitas/pagination ada di [panduan konsumsi FE](docs/fe-analytics.md).
Contoh JSON ilustratif juga tersedia di Swagger; tahap ini tidak mengubah scheduler
atau menambah tabel database.

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
Endpoint data sekarang memerlukan sesi login. POST/PATCH metadata PLTS memerlukan
role admin dan `X-CSRF-Token`; token statis `PLANT_WRITE_TOKEN` sudah digantikan.

## Dashboard login

Kontrak, contoh request/response, CSRF, CORS, dan konfigurasi produksi tersedia di
[docs/auth-api.md](docs/auth-api.md). Swagger tersedia di `/docs`.

Setelah koneksi PostgreSQL/SSH tunnel aktif, jalankan migrasi dan buat akun awal:

```powershell
.\.venv\Scripts\python.exe -m app.manage_auth migrate
.\.venv\Scripts\python.exe -m app.manage_auth seed --viewer-plant "KODE_PLTS_YANG_ADA" --credentials-file .env.auth-accounts.local
```

Ganti kode PLTS dengan kode aktual; ulangi `--viewer-plant` untuk beberapa PLTS.
Admin dapat mengakses semua PLTS; viewer hanya mendapat PLTS yang ditugaskan.
File kredensial lokal dikecualikan Git dan dibatasi untuk pengguna OS yang membuatnya.
Password tidak dicetak. Seed menolak menimpa akun yang sudah ada. Tanpa opsi file,
script meminta password lewat prompt tersembunyi. Migrasi dan seed tidak berjalan otomatis.

Worker memperbarui inventory perangkat saat startup dan setiap 24 jam.
Metadata inventory tidak digunakan sebagai bukti freshness telemetry.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
