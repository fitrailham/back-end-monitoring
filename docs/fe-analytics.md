# API prioritas untuk konsumsi frontend

Implementasi tahap ini mengikuti prioritas checklist FE: history daya berdasarkan
tanggal, laporan energi inverter, dan riwayat perangkat. Scheduler, collector,
pengaturan vendor, dan skema database tidak diubah pada tahap API ini.

## Endpoint baru

| GET | Tujuan | Parameter khusus |
| --- | --- | --- |
| `/api/database/power-statistics` | Grafik daya Home/Monitoring/Trend berdasarkan rentang | `plant_code` wajib; `resolution_minutes=5,10,15,30,60` (default 10); `aggregation=average,last` (default average) |
| `/api/reports/inverters` | Energi per inverter dan bucket, total sebelum pagination | `plant_code` opsional; `device_id` opsional dan memerlukan `plant_code`; `grain=hour,day,month,year` (default day); `page=1`; `page_size=100` (maks. 1000) |
| `/api/devices/{device_id}/history` | History perangkat dan Export Performance Data | `plant_code` wajib; ID vendor dari inventory; `page=1`; `page_size=500` (maks. 1000) |

Ketiganya memerlukan query `from`, `to`, dan menerima `time_zone` IANA
(default `Asia/Jakarta`). Timestamp harus memiliki offset/Z. `from` inklusif dan
`to` eksklusif. Counter inverter tepat pada `to` hanya dipakai sebagai batas
perhitungan energi, bukan sampel di periode berikutnya.

Power/history maksimum 31 hari per request; power maksimum 5000 bucket.
Laporan memakai batas range statistik yield yang sudah ada: hour 31 hari,
day 366 hari, month 3660 hari, year 36600 hari, maksimum 1000 bucket dan
50000 baris perangkat/bucket. Sumber melebihi 200000 sampel mengembalikan
422 `SOURCE_RANGE_TOO_DENSE`, bukan hasil terpotong diam-diam.

## Identitas dan inventory

`device_id` tetap ID vendor berbentuk **string**, termasuk ketika isinya angka.
Query history wajib memakai `plant_code` agar scope perangkat tidak ambigu.
`device_key` adalah ID internal database, juga string; jangan mengganti
`device_id` dengan `device_key` pada URL history. Route inventory lama
`GET /api/devices/{plant_code}` tetap tersedia.

Model Device di Swagger kini menyebut `device_key`, `software_version`,
`parent_device_id`, dan `parent_device_name` secara eksplisit. Parent yang belum
dipetakan bernilai null; tidak ditebak dari urutan perangkat. Nama/ID vendor
dan kontrak inventory lama dipertahankan.

## Aturan data dan rendering FE

- `0` adalah pengukuran valid. `null` berarti tidak ada nilai yang layak dipakai.
- Daya memakai kW, energi memakai kWh, SOC memakai persen. `average` adalah
  rata-rata sampel valid dalam bucket, bukan energi atau rata-rata berbobot waktu.
- Power saat ini memakai history station di `plant_realtime`; history perangkat
  memakai `device_realtime`. Sumber tersebut dinyatakan dalam kontrak.
- Bucket daya memuat `coverage` per metrik: jumlah slot sampel yang diharapkan,
  valid, hilang, belum terverifikasi, quality, dan reason. Sampel yang menumpuk
  dalam satu slot tidak membuktikan cakupan satu jam penuh.
- Waktu collector tidak dipakai sebagai waktu pengukuran. Field daya dengan
  timestamp belum terverifikasi tidak diplot sebagai pengukuran.
- Grid dan battery power memerlukan konvensi tanda terverifikasi
  `grid_import_battery_discharge_positive`; selain itu nilainya null dengan
  alasan `POWER_DIRECTION_UNVERIFIED`. Nilai positif berarti impor/discharge.
- Bucket historis yang memang memiliki pengukuran valid tetap dapat dipakai;
  usia historis tidak otomatis membuat pengukuran tanggal tersebut invalid.
- Laporan inverter memakai selisih counter total energi inverter terverifikasi.
  Yield PLTS tidak dibagi ke inverter. Counter reset, gap, atau baseline yang
  hilang ditandai; energi tidak diinterpolasi dari daya.
- `quality=partial` dapat memuat energi yang sudah diketahui. Beri label parsial;
  jangan menyebutnya total lengkap. `missing` tetap null; `pending` berarti
  interval belum terjadi. Bucket terbuka memiliki status belum lengkap.
- `totals` laporan mencakup **semua hasil filter sebelum pagination**. Jangan
  menjumlahkan `totals` setiap halaman ketika mengekspor.
- History mengembalikan `total` dan `meta.total_pages`; ambil setiap halaman
  untuk ekspor. Batas 500 pada endpoint history PLTS lama tidak berlaku sebagai
  batas total endpoint history perangkat baru.
- `DEVICE_TELEMETRY_UNAVAILABLE` berarti belum ada history perangkat pada scope
  tersebut. Inventory terisi tidak membuktikan telemetry tersedia.

## Contoh pemanggilan FE

```ts
const query = new URLSearchParams({
  plant_code: selectedPlant.plant_code,
  from: fromDate.toISOString(),
  to: toDate.toISOString(),
  time_zone: selectedPlant.time_zone || "Asia/Jakarta",
  resolution_minutes: "10",
  aggregation: "average",
});
const response = await fetch(`${API_BASE}/api/database/power-statistics?${query}`, { signal });
const body = await response.json();
if (!response.ok || !body.success) throw new Error(body.message || "Gagal mengambil daya");
const points = body.data.buckets.map((bucket) => ({
  timestamp: bucket.start_at,
  pv: bucket.pv_power_kw, // null tetap null, jangan memakai `|| 0`.
  quality: bucket.coverage.pv_power_kw.quality,
}));
```

Laporan inverter menggunakan `body.data` sebagai baris tabel, `body.total` untuk
pagination, dan `body.totals.inverter_yield_kwh` untuk total periode. History
perangkat menggunakan `encodeURIComponent(device.device_id)` pada path dan
tetap mengirim query `plant_code`. Selalu periksa `meta.is_partial` dan quality.

Contoh response JSON lengkap, termasuk terukur/nol, belum terverifikasi, tidak
ada history, pagination dan error: [fe-analytics-examples.json](fe-analytics-examples.json).
Seluruh nilai di file contoh adalah fixture ilustratif, bukan data produksi.
Contoh yang sama terpasang pada Swagger `/docs`. File request siap pakai:
[fe-analytics.http](fe-analytics.http). Envelope error mengikuti kontrak existing:
`success=false`, `message`, `data=null`, `error.code`, dan `request_id`.

Regenerasi fixture lokal tanpa database/vendor:

```powershell
.\.venv\Scripts\python.exe scripts/export_analytics_examples.py
```

## API yang sudah ada dan tetap digunakan

Master PLTS GET/POST/PATCH, overview, summary portofolio, yield-statistics,
rankings, inventory, dan alarm tetap memakai path/kontrak yang sudah tersedia.
Filter AG Grid dan ekspor lokal tidak memerlukan endpoint baru. Ketersediaan
route tidak menjamin data sumber lengkap; FE tetap mengikuti metadata kualitas.

Audit live read-only disimpan lokal di `logs/fe-api-readiness-audit.json` dan
tidak dimasukkan ke Git. API baru tidak mengambil data langsung dari vendor.
Jika tabel telemetry kosong atau waktunya belum terverifikasi, grafik/laporan
tetap berstatus missing/unverified sampai sumber menyediakan pengukuran valid.

## Cakupan berikutnya

Energy breakdown, Plant Report lengkap, Revenue Trend, CV, layout bersama,
rename, parameters, alarm actions dan subscriptions **belum ditambahkan oleh
tahap prioritas ini**. Tabel energi/tarif yang sudah ada dapat dipakai setelah
metrik dan coverage terbukti lengkap. CV string memerlukan telemetry dan mapping
string nyata; layout/rename/subscriptions memerlukan desain penyimpanan serta
izin/audit. Operasi perangkat dan clear alarm memerlukan bukti dukungan vendor.
Tidak ada tabel baru, migrasi produksi, aktivasi collector, atau pengiriman
laporan yang dilakukan dalam tahap API ini.
