# IconGreen Solar Monitoring Backend

Backend FastAPI untuk mengambil data PLTS dari FusionSolar Northbound API,
menyimpannya ke PostgreSQL, dan menyediakan data monitoring untuk frontend.
API membaca data dari DB; worker sinkronisasi dijalankan terpisah.

## Persiapan

- Python 3.10 atau lebih baru.
- PostgreSQL dengan tabel `public.plants`, `public.plant_realtime`,
  `public.fusionsolar_raw`, dan `public.sync_logs`.
- Akun Northbound aktif dengan izin mengakses plant dan API yang dibutuhkan.
- SSH tunnel aktif jika koneksi DB memerlukannya.

Aplikasi belum menyediakan migrasi atau pembuatan tabel otomatis.

## Instalasi (PowerShell)

Jalankan dari folder proyek:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Salin template hanya saat membuat `.env` pertama kali agar konfigurasi yang
sudah ada tidak tertimpa.

Edit `.env` sesuai koneksi DB dan akun Northbound Anda. Yang wajib diperiksa:

- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- `FUSIONSOLAR_BASE_URL`: URL HTTPS sesuai server/region akun.
- `FUSIONSOLAR_USERNAME` dan `FUSIONSOLAR_PASSWORD`: kredensial akun Northbound.
- `CORS_ORIGINS`: alamat frontend yang diizinkan mengakses API.

Jangan commit `.env` atau memasukkan password/token ke README.
`.env.example` hanya berisi contoh konfigurasi.

## Menjalankan API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

- Dokumentasi API: http://127.0.0.1:8000/docs
- Tes koneksi DB: `GET /api/database/test`
- Daftar plant: `GET /api/database/plants`
- Ringkasan monitoring: `GET /api/database/overview`
- Dashboard: `GET /api/dashboard/{plant_code}`
- Riwayat sinkronisasi: `GET /api/database/sync-logs`

`--reload` digunakan untuk pengembangan lokal.

## Sinkronisasi sekali ke DB

Perintah ini mengambil daftar plant dan KPI dari Northbound, lalu menulisnya
ke database yang dipilih di `.env`:

```powershell
@"
from app.jobs import plant_sync, realtime_sync
from app.services.fusionsolar_client import FusionSolarClient

with FusionSolarClient() as client:
    print("Plant tersimpan:", plant_sync.run(client))
    print("Snapshot KPI tersimpan:", realtime_sync.run(client))
"@ | .\.venv\Scripts\python.exe -
```

KPI diambil secara gabungan hingga 100 plant per request untuk mengurangi
pemanggilan API. Respons asli disimpan di `fusionsolar_raw`, snapshot di
`plant_realtime`, dan hasil job di `sync_logs`.
Field yang tidak diberikan API tetap `null`; waktu pencatatan memakai waktu
collector jika respons tidak menyertakan waktu pengukuran.

## Sinkronisasi berkala

Set `FUSIONSOLAR_ENABLED=true` di `.env`, lalu jalankan pada terminal terpisah:

```powershell
.\.venv\Scripts\python.exe -m app.scheduler.scheduler
```

Worker mengambil daftar plant saat mulai, lalu KPI dan alarm setiap siklus.
Interval mengikuti `SYNC_INTERVAL_MINUTES` dengan minimum 15 menit.
Alarm dilewati jika `FUSIONSOLAR_ALARMS_PATH` kosong.
Worker ini belum mengambil daftar perangkat secara otomatis.

## Jika terjadi kendala

- DB gagal terhubung: periksa konfigurasi DB dan SSH tunnel.
- Login ditolak: periksa kredensial Northbound, server/region, dan status akun.
- `failCode=407`: beri jeda sebelum mencoba kembali; hindari menjalankan
  beberapa sinkronisasi bersamaan dengan akun yang sama.
- Data tidak muncul: cek izin plant/API dan hasil job di `sync_logs`.

## Tes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
