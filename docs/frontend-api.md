# Kontrak frontend IconGreen

Implementasi mengikuti audit 9 November 2026. Base URL lokal: `http://127.0.0.1:8000`.
Swagger `/docs` menyediakan response model, nullable fields, enum, dan error schemas.
Contoh pada `frontend-api-examples.json` menggunakan fixture, bukan data produksi.

Referensi JSON pengguna disimpan utuh secara semantik pada
[`frontend-api-proposal-1.json`](frontend-api-proposal-1.json). Contoh implementasi yang
dibangkitkan melalui TestClient tersedia pada
[`frontend-api-examples.json`](frontend-api-examples.json). Regenerasi tanpa akses database:

```powershell
.\.venv\Scripts\python.exe scripts/export_contract_examples.py
```

## Penyesuaian terhadap JSON proposal-1

Seluruh nama field pada 11 skenario response proposal tersedia pada response model.
Nilai contoh tidak menjadi default produksi. Field lama tetap tersedia bersama alias baru:

| Kebutuhan proposal | Implementasi |
|---|---|
| device_serial_numbers pada master/overview | Daftar serial unik dari inventory; string dan leading zero dipertahankan |
| meta pada detail, perangkat, dan registrasi | generated_at, snapshot_id, scope, timezone, dan kualitas cakupan |
| Coverage expected_plants/valid_plants/dst. | Alias dari expected/valid/dst.; complete adalah kebalikan partial |
| Coverage overview | Dihitung untuk seluruh hasil filter sebelum pagination; bukan satu halaman |
| meta.returned/filters_applied | Jumlah baris yang dikirim dan filter yang benar-benar diberikan |
| not_applicable_fields | Terpisah dari missing_fields untuk perangkat yang diketahui tidak terpasang |
| PR, irradiation, revenue per baris | Field nullable tersedia; belum dihitung tanpa input/metode yang disetujui |
| Summary active_alarms yang datar | configured, severity counts, dan cakupan datar tersedia; severity/coverage lama tetap ada |
| Environmental/revenue metadata | Alias standard_coal_saved_kg/factor_source/factor_version dan period/basis/currency/tariff_version/is_estimate |
| Statistik scope/plant_code/from/to/metric/total | from/to adalah alias from_at/to_at; total adalah jumlah energi bucket yang diketahui, dengan complete/is_partial |
| Statistik coverage bucket | expected/complete/partial/missing/pending buckets; pending tidak masuk expected_buckets |
| Ranking metadata | ranking_date, eligible_plants, excluded_plants, excluded_reasons; field cakupan di root tetap ada |
| Alarm per PLTS | state/severity dan pagination opt-in; total tetap sebelum pagination |
| Fleet alarm plant_name | Nama PLTS dari master, bukan perkiraan dari perangkat |
| POST response | message konfirmasi dan meta, HTTP 201; tidak membuat plant upstream |
| Error tanpa cache telemetry | Dashboard mengirim 503 TELEMETRY_UNAVAILABLE dengan Retry-After mengikuti interval sumber |

Beberapa nilai dan istilah pada fixture sengaja tidak disalin sebagai klaim data aktual:

- scope menggunakan `all_configured_plants`, `all_configured_plants_filtered`, atau `plant`.
  Backend saat ini belum memiliki otorisasi portofolio per pengguna; label authorized_portfolio
  pada proposal bukan jaminan kontrol akses yang sudah diterapkan.
- expected_interval_seconds mengikuti konfigurasi worker (minimum 600 detik), bukan angka
  ilustratif 300 detik. Retry-After hanya dikirim ketika interval retry diketahui.
- Selisih counter memakai quality derived, bukan measured. Label jam HH:mm dilengkapi
  label_at ISO dengan offset untuk membedakan jam berulang saat DST.
- Status vendor_device_type_id berupa integer untuk tipe numerik dan string untuk kode nonnumerik;
  ID perangkat dan nomor seri tetap string. Field model tetap dikirim null jika tidak tersedia.
- Location provenance mengikuti bukti: plant_metadata atau device_inventory; koordinat kandidat
  inventory tidak otomatis menjadi verified_device. Demo tidak dinyatakan fresh/healthy.
- Revenue, PR, dan faktor lingkungan tidak mengambil angka EXAMPLE_ONLY dari fixture. Nilai
  tetap null/unconfigured sampai sumber, faktor/tarif, dan metodologi tersedia.

Schema Swagger memuat respons fixture implementasi dan model field nullable/enum tersebut.

## Kompatibilitas dan unit

- GET memakai query/path tanpa JSON body. POST/PATCH memakai JSON.
- Plants dan overview tetap mengirim `success`, `total`, `data`; field baru bersifat aditif.
- Overview tanpa `page` maupun `page_size` mengembalikan semua hasil filter.
- `total` selalu jumlah hasil filter sebelum pagination; `meta.total_pages` hanya untuk mode paginated.
- Angka normalisasi memakai kW, kWp, kWh, dan persen; unknown adalah null, diketahui nol adalah 0.
- Dashboard lama tetap mengirim camelCase dan yield MWh yang dibulatkan. Field `normalized`
  menyediakan kontrak snake_case, kWh, dan presisi sumber tanpa pembulatan tampilan.
- Devices mempertahankan alias vendor dan camelCase envelope, ditambah field normalisasi.
  ID dan serial disajikan sebagai string. Nomor seri yang sudah kehilangan nol di sumber numerik
  tidak dapat dipulihkan oleh backend.
- `recorded_at` adalah alias timestamp penyimpanan lama. `measured_at` hanya tersedia untuk
  waktu sumber yang diketahui; `collected_at` tidak menggantikannya. Respons baru dibuat
  pada `meta.generated_at`, dengan `snapshot_id` berbasis isi data, bukan ID transaksi bersama
  lintas request. Setiap request membaca cache dalam satu SQL statement; scope filter yang berbeda
  dapat menghasilkan snapshot ID yang berbeda.

## Metadata PLTS dan peta

`GET /api/database/plants` dan `GET /api/database/plants/{plant_code}` menyediakan:
country/region/city/time_zone, koordinat dan provenance, foto/tanggal grid connection,
has_pv/has_ess, jumlah perangkat ESS, device_types, nominal inverter dan kapasitas ESS,
jumlah optimizer serta penanda kelengkapan inventory. Field kontrak tetap dikirim ketika null.

Lokasi master terverifikasi diprioritaskan. Tanpa master, satu pasangan koordinat inventory
yang konsisten menjadi kandidat `location_verified: false`. Koordinat yang berkonflik tidak
dipilih secara sembarang. Pasangan (0,0) dianggap kandidat sentinel dan tidak dipetakan otomatis.
Frontend dapat menampilkan kandidat marker dengan penanda belum diverifikasi.

Timezone, negara, nama wilayah, foto, tanggal commissioning, kapasitas inverter dan ESS tidak
ditebak dari koordinat, nama PLTS, atau nama model. Gunakan PATCH master untuk melengkapinya.
`optimizer_quantity` hanya total yang diketahui lengkap; jika belum lengkap, nilainya null
dan `optimizer_quantity_complete` false. `ess_quantity` menghitung perangkat inventory tipe
battery/ESS; bukan estimasi jumlah rack, modul, atau kapasitas fisik terpasang.

## Overview dan filter

```text
/api/database/overview?page=1&page_size=10&device_type=ess&sort=yield_today_kwh&order=desc
```

Filter: `search` (nama/kode), `country_code`, `region`, `device_type`, `device_sn`, `status`.
Pagination opt-in: page >= 1, page_size 1..100, default 10 jika mode pagination digunakan.
Sort: plant_name, capacity_kwp, pv_power_kw, yield_today_kwh, total_yield_kwh,
specific_energy_kwh_per_kwp. Order asc/desc; nilai null selalu terakhir; plant_code tie-breaker.
Filter SN dan jenis perangkat menggunakan inventory, bukan SOC. Filter/pagination dijalankan
di backend pada hasil bulk SQL; belum menggunakan pagination SQL untuk portofolio sangat besar.

`data_quality.state`: fresh/stale/partial/missing/unverified. `field_quality` menjelaskan
VALID, STALE, TIME_UNVERIFIED, SOURCE_UNAVAILABLE, NOT_INSTALLED, atau DEMO_DATA.
Freshness mengikuti `STALE_AFTER_MINUTES`; expected interval mengikuti worker
`SYNC_INTERVAL_MINUTES` (minimum 10 menit). Refresh frontend 5 menit tidak mengubah cadence sumber.

Status operasional normal/faulty/disconnected/unknown terpisah dari kualitas telemetry.
Pemetaan `real_health_state`: 1 disconnected, 2 faulty, 3 normal, hanya jika waktu sumber
terverifikasi dan belum stale. Tanpa bukti waktu yang cukup, status unknown. Daya nol bukan
bukti disconnected. `power_direction_convention: source_native_unverified` menunjukkan bahwa
konvensi tanda grid/baterai belum dikonfirmasi; frontend tidak boleh mengasumsikan arah panah.
PR null sampai pengukuran dan metodologi irradiation tersedia.

## Summary portofolio

```text
/api/portfolio/summary
/api/portfolio/summary?date=2026-11-09&time_zone=Asia/Makassar&region=Bali
```

Mendukung filter scope yang sama dengan overview, tanpa pagination. `data` memuat total_plants,
kpis, plant_status, active_alarms, coverage per KPI, revenue, environmental_benefits.
Coverage membagi expected menjadi valid/missing/stale/unverified/not_applicable. Total memakai
nilai valid saja; ketika tidak ada nilai valid, total null. Stale/unverified tidak dijumlahkan
sebagai data fresh; total parsial ditandai. Kapasitas PV <= 0 dikecualikan dari agregasi kapasitas.

Tanpa `date`, yield menjumlahkan counter hari lokal masing-masing PLTS yang waktunya
terverifikasi dan timezone-nya dikonfigurasi (`date_basis: sum_of_verified_plant_local_days`).
Jika `date` dikirim, hanya counter dengan tanggal tersebut dan timezone PLTS identik dengan
timezone permintaan yang diterima. Counter harian tidak dikonversi diam-diam ke zona lain.
Endpoint ini belum melakukan rekonstruksi yield historis pada summary; untuk interval/hari
historis atau lintas timezone gunakan yield-statistics. KPI current_power tetap snapshot saat ini.

Revenue dan environmental benefits disajikan `status: unconfigured` dengan nilai null;
tidak ada tarif/faktor perkiraan. Summary alarm mencakup semua alarm aktif di scope,
termasuk yang sudah acknowledged; pagination daftar alarm tidak mempengaruhi summary.

## Statistik energi interval

```text
/api/database/yield-statistics?from=2026-11-08T00:00:00%2B08:00&to=2026-11-09T00:00:00%2B08:00&grain=hour&time_zone=Asia/Makassar
```

`plant_code` opsional; kosong berarti seluruh PLTS non-demo. from inklusif, to eksklusif;
timestamp wajib memiliki offset/Z. grain hour/day/month/year. Bucket kalender mengikuti timezone
IANA permintaan, termasuk batas hari/bulan/tahun dan perubahan DST. Batas maksimal rentang:
hour 31 hari, day 366 hari, month 3660 hari, year 36600 hari; maksimal 1000 bucket.
Rentang berlebih ditolak 422, tidak dipotong diam-diam.

Metode saat ini `verified_total_counter_difference`: selisih counter total_yield_kwh berurutan
yang timestamp-nya dari sumber, bukan collector, dan bukan demo. Counter yield_today tidak
dijumlahkan atau diplot sebagai energi interval. Sampel sebelum from dibaca sebagai baseline;
counter pada to boleh dipakai untuk menutup selisih interval, tidak dihitung sebagai sampel
di dalam interval. Pembacaan ganda pada timestamp sama memakai rekaman terakhir.

Tidak ada interpolasi. Selisih yang melewati batas bucket, counter reset, baseline hilang,
atau gap lebih dari 1,5 kali expected interval tidak dianggap interval lengkap. Energi yang
diketahui untuk sebagian bucket dapat dikembalikan dengan quality partial dan reason;
tanpa selisih valid nilainya null/quality missing. Bucket mendatang pending, expected_samples 0,
dan tidak dihitung sebagai telemetry hilang. `complete` menilai bucket yang sudah berlalu;
pending bukan bukti energi periode sudah tersedia. Respons menyatakan `truncated: false`.

Dengan cache lama yang seluruh waktunya collector, statistik akan missing/unverified.
Ini disengaja: endpoint tidak mengubah collector time menjadi measurement time.

## Ranking

```text
/api/database/rankings?date=2026-11-08&metric=specific_energy&limit=5&time_zone=Asia/Makassar
```

metric specific_energy atau yield, limit 1..100. Energi dihitung dari counter terverifikasi
pada interval tanggal dan timezone yang sama. Ranking hanya menerima periode dengan coverage
lengkap, capacity_kwp > 0, non-demo; saat belum ada baseline/sampel memadai, hasil bisa kosong.
Metadata eligible_plants/excluded_plants/exclusion_reasons menjelaskan cakupan. Tie-breaker
plant_code stabil. Counter hari lokal tidak digunakan untuk membandingkan lintas timezone.

## Devices dan alarm

`GET /api/devices/{plant_code}` meneruskan inventory cache dan normalisasi ID/SN, jenis,
optimizer, koordinat serta rating yang memang disediakan sumber. Inventory diperbarui worker
saat startup dan setiap 24 jam; tidak digunakan sebagai waktu telemetry.

`GET /api/alarms/{plant_code}` dan `GET /api/alarms` menyediakan normalisasi severity/state.
Fleet menerima plant_code, severity, state, page, page_size. State yang tidak dikenali tetap
null dan coverage incomplete; acknowledgment tidak berarti cleared. Severity vendor 1/2/3/4
dipetakan critical/major/minor/warning. State vendor 1 adalah active; state lain tidak ditebak.

- Endpoint sumber belum dikonfigurasi: configured false, total null, data [].
- Sumber berhasil dan hasil kosong: total 0; hanya coverage lengkap/fresh yang membuktikan nol.
- Cache stale, kolektor gagal, atau sebagian PLTS hilang: cache tetap dikirim jika ada,
  quality/coverage menandai incomplete; jumlah cache bukan jaminan jumlah aktual.

`FUSIONSOLAR_ALARMS_PATH` tetap harus sesuai endpoint dan izin akun vendor. Kode tidak
mengaktifkan endpoint yang belum dikonfirmasi atau mengubah `.env` pengguna.

## Registrasi dan pembaruan metadata lokal

Jalankan `.venv\Scripts\python.exe -m app.migrate` untuk kolom metadata dan indeks.
Migrasi tidak dijalankan otomatis. Atur token administrator pada `PLANT_WRITE_TOKEN`;
request memakai `Authorization: Bearer <token>`. Tanpa konfigurasi, operasi tulis 403.
Gunakan otorisasi server/proxy yang sesuai sebelum menawarkan administrasi pada deployment publik;
token administrator tidak boleh dimasukkan ke bundle frontend publik.

`POST /api/database/plants` menerima plant_code/name, capacity_kwp positif, time_zone IANA,
country/region/city/address, pasangan koordinat, grid_connected_on. Berhasil 201, duplikasi 409.
Registrasi lokal tidak membuat PLTS di FusionSolar atau memverifikasi akses upstream.

`PATCH /api/database/plants/{plant_code}` menerima metadata lokasi/wilayah/timezone/foto,
grid_connected_on, has_pv/has_ess, rating inverter/ESS, dan koreksi capacity_kwp positif.
Telemetry dan alarm tidak boleh ditulis. Untuk verifikasi lokasi kirim pasangan koordinat
beserta `location_verified: true`; koordinat yang diubah tanpa flag otomatis belum diverifikasi.
Sinkronisasi master dari vendor tetap dapat memperbarui kapasitas berdasarkan data vendor.

## Error dan pekerjaan upstream tersisa

Format error: success false, message, data null, error.code/retryable/retry_after_seconds/fields,
request_id. Header X-Request-ID cocok dengan request_id. 404 PLANT_NOT_FOUND,
409 PLANT_ALREADY_EXISTS, 422 VALIDATION_ERROR atau kode validasi rentang, 503 DATABASE_UNAVAILABLE
atau MIGRATION_REQUIRED. Pesan database tidak diteruskan ke client.

Worker mencoba login ulang sekali untuk failCode 305, tanpa retry otomatis quota 407.
Sumber yang saat ini menyimpan day_power/total_power tidak menyediakan daya PV/load/grid atau
telemetry ESS. Implementasi ini belum menambahkan pengambilan getDevRealKpi dan belum menetapkan
konvensi arah daya. Dibutuhkan izin API dan pemetaan telemetry perangkat yang tervalidasi untuk
mengisi field tersebut. Kapasitas nol harus dikoreksi berdasarkan nameplate, bukan estimasi.

Pemetaan tipe perangkat/health/severity merujuk dokumen Huawei
[SmartPVMS 25.2.0 NBI Reference, Issue 01](https://www.scribd.com/document/980589962/SmartPVMS-25-2-0-Northbound-API-Reference)
(salinan dokumen vendor), dan retry sesi pada
[SmartPVMS V300R006C00 Interface Reference, bagian error codes](https://pdfcoffee.com/smartpvms-v300r006c00-interface-reference-4-pdf-free.html).
Override DEVICE_TYPE_MAP hanya setelah mencocokkan versi NBI deployment.
