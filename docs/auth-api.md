# Autentikasi dashboard PLTS

Semua endpoint data `/api/database/*`, `/api/dashboard/*`, `/api/devices/*`,
`/api/alarms*`, `/api/portfolio/*`, dan `/api/reports/*` memerlukan sesi.
`GET /` (health), `/docs`, `/redoc`, dan `/openapi.json` tetap publik.
Perubahan ini menggantikan akses baca anonim dan token tulis statis.

## Setup database dan akun awal

Koneksi `.env` dan SSH tunnel harus aktif. Migrasi berikut hanya menambah empat
tabel autentikasi dan indeks; tidak mengubah data telemetry atau akun vendor.
Tabel `public.plants` harus sudah tersedia.

```powershell
.\.venv\Scripts\python.exe -m app.manage_auth migrate
.\.venv\Scripts\python.exe -m app.manage_auth seed --viewer-plant "KODE_PLTS_AKTUAL" --credentials-file .env.auth-accounts.local
```

Gunakan kode aktual dari `public.plants`; ulangi `--viewer-plant` untuk menambah PLTS.
Nama akun awal adalah `admin` dan `viewer`. Password acak berbeda dibuat oleh script
dan disimpan hanya di file lokal privat tersebut, dengan ACL Windows untuk pembuat
file / mode 0600 pada POSIX. File `.env.*` dikecualikan Git. Jangan unggah file ini,
menempelkannya ke issue, atau mengirimkannya ke bundle FE. Bagikan hanya lewat kanal
privat yang disepakati tim. Script tidak mencetak password. Tanpa `--credentials-file`,
password dimasukkan lewat prompt tersembunyi (15–1024 karakter).

Seed menolak menimpa akun yang sudah ada, tidak mereset password, dan memvalidasi
semua kode PLTS sebelum commit. Migrasi aman diulang. Schema tidak dibuat otomatis
saat server menyala. Restart API setelah perubahan konfigurasi.

## Role dan scope PLTS

| Role | Akses data | Perubahan metadata |
| --- | --- | --- |
| `admin` | Semua PLTS saat ini dan yang ditambahkan kemudian | POST/PATCH master PLTS; diagnostik database dan sync logs |
| `viewer` | Hanya kode PLTS di `auth_user_plants` | Ditolak dengan 403 |

`plant_access.all_plants=true` adalah wewenang global admin. Untuk viewer,
`plant_access.plant_codes` adalah daftar izin eksplisit; daftar kosong berarti tidak
memiliki akses PLTS. Daftar, total, ranking, alarm, laporan, dan statistik agregat
difilter sebelum penghitungan/pagination. Request eksplisit untuk PLTS di luar scope
mendapat `403 PLANT_ACCESS_DENIED`. PLTS yang tidak ada mendapat 404 jika pengguna
berwenang mengakses scope tersebut. Session dan status aktif/grant pengguna diperiksa
ulang setiap request. Menonaktifkan akun atau mencabut grant segera berlaku.

## Cookie, sesi, dan CSRF

- Cookie sesi: `icongreen_session`, `HttpOnly`, host-only, `Path=/`, default `SameSite=Lax`.
- Masa berlaku absolut default **28.800 detik (8 jam)**; tidak diperpanjang oleh request.
- Cookie `Secure` wajib pada produksi HTTPS. Jangan menyediakan login lewat HTTP publik.
- Token sesi acak 256 bit; database hanya menyimpan SHA-256 token dan hash token CSRF.
- Login merotasi sesi browser sebelumnya. Logout mencabut sesi itu di server lalu menghapus cookie.
  Sesi browser/perangkat lain tetap berlaku.
- FE memperoleh token CSRF pra-login lewat `GET /api/auth/csrf`, lalu mengirimkannya
  di header `X-CSRF-Token` pada login. Cookie pra-login HttpOnly berlaku 10 menit.
- Setelah login, pakai token CSRF **baru** dari login atau `/me` untuk seluruh
  POST/PATCH/PUT/DELETE, termasuk logout. Token pra-login tidak berlaku untuk logout.
- Token CSRF disimpan dalam memori FE. Setelah reload, ambil lagi melalui `/me`.
- Origin browser diverifikasi terhadap `CORS_ORIGINS`; request tanpa Origin tetap
  wajib mengirim header CSRF untuk operasi tulis. CORS menolak origin yang tidak diizinkan.
- Password disimpan dengan scrypt, salt acak per password, N=131072, r=8, p=1.
  Username tidak peka huruf besar/kecil; password diverifikasi persis tanpa trim.

Pilihan ini mengikuti panduan [autentikasi OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[sesi OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[CSRF OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
dan [penyimpanan password OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
Untuk CSRF sesi, server memvalidasi hash token yang terikat sesi. Pra-login memakai
cookie/header, pengecekan origin eksplisit, dan CORS yang mewajibkan preflight header kustom.

## Endpoint dan contoh

Swagger: `/docs`; schema JSON: `/openapi.json`. Cookie otomatis dipakai browser
Swagger setelah login. Isi `X-CSRF-Token` yang didokumentasikan pada request terkait.
Contoh berikut ilustratif dan tidak memuat kredensial sungguhan.

### 1. GET /api/auth/csrf

```http
GET /api/auth/csrf
Origin: http://localhost:5173
```

```json
{"success":true,"data":{"csrf_token":"<token-pra-login>"}}
```

Browser menerima cookie `icongreen_login_csrf`. Seluruh request autentikasi
memakai `Cache-Control: no-store`.

### 2. POST /api/auth/login

```http
POST /api/auth/login
Content-Type: application/json
X-CSRF-Token: <token-pra-login>
Cookie: icongreen_login_csrf=<token-pra-login>

{"username":"viewer","password":"<password-dari-kanal-privat>"}
```

```json
{
  "success": true,
  "data": {
    "user": {
      "id": "a01d577b-7161-4027-9a2a-eb89136e6de2",
      "username": "viewer",
      "display_name": "Dashboard Viewer",
      "role": "viewer",
      "plant_access": {"all_plants": false, "plant_codes": ["PLANT-EXAMPLE-001"]}
    },
    "csrf_token": "<token-csrf-sesi>",
    "expires_at": "2026-11-16T16:00:00Z"
  }
}
```

Status 200 dan `Set-Cookie: icongreen_session=...; HttpOnly; Path=/; SameSite=lax`
(ditambah `Secure` di produksi). Token sesi tidak disertakan pada JSON.

### 3. GET /api/auth/me

```http
GET /api/auth/me
Cookie: icongreen_session=<cookie-dikelola-browser>
```

Status 200, bentuk JSON sama dengan login: `data.user`, `data.csrf_token`,
`data.expires_at`. Gunakan ini untuk memulihkan state FE setelah reload.
Sesi kedaluwarsa/dicabut/tidak valid atau akun nonaktif menghasilkan 401.

### 4. POST /api/auth/logout

```http
POST /api/auth/logout
X-CSRF-Token: <token-csrf-sesi>
Cookie: icongreen_session=<cookie-dikelola-browser>
```

```json
{"success":true,"message":"Logout berhasil."}
```

Status 200. Cookie dihapus setelah pencabutan server berhasil. Logout tanpa sesi
valid mendapat 401; FE dapat membersihkan state lokal bila menerima 401.
CSRF tidak valid mendapat 403 dan sesi tetap berlaku.

## Integrasi fetch FE

```javascript
const API = import.meta.env.VITE_API_BASE_URL; // misalnya http://localhost:8000
let csrfToken;

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...options.headers,
    },
  });
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.message);
    Object.assign(error, { status: response.status, code: body.error?.code,
      retryAfterSeconds: body.error?.retry_after_seconds, requestId: body.request_id });
    throw error;
  }
  return body;
}

async function login(username, password) {
  csrfToken = (await api("/api/auth/csrf")).data.csrf_token;
  const { data } = await api("/api/auth/login", {
    method: "POST", body: JSON.stringify({ username, password }),
  });
  csrfToken = data.csrf_token;
  return data.user;
}

async function restoreSession() {
  const { data } = await api("/api/auth/me");
  csrfToken = data.csrf_token;
  return data.user;
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  csrfToken = undefined;
}
```

Saat aplikasi dibuka, panggil `restoreSession`; 401 berarti tampilkan login.
Setelah login, panggil endpoint data normal. Sembunyikan kontrol admin berdasarkan
role untuk UX; backend tetap menegakkan otorisasi. Jangan menyimpan password,
cookie sesi, atau token CSRF di localStorage. FE harus mematikan mode login preview.

## Error dan pembatasan percobaan

```json
{
  "success": false,
  "message": "Username atau password tidak valid.",
  "data": null,
  "error": {"code": "INVALID_CREDENTIALS", "retryable": false, "retry_after_seconds": null, "fields": []},
  "request_id": "req-contoh"
}
```

| Status | Kode | Tindakan FE |
| --- | --- | --- |
| 401 | `INVALID_CREDENTIALS` / `UNAUTHORIZED` | Tampilkan pesan login / kembali ke login |
| 403 | `FORBIDDEN` / `PLANT_ACCESS_DENIED` | Tampilkan akses ditolak |
| 403 | `CSRF_INVALID` / `ORIGIN_DENIED` | Ambil token kembali atau periksa origin konfigurasi |
| 429 | `RATE_LIMITED` | Tunggu `Retry-After` / `error.retry_after_seconds` |
| 422 | `VALIDATION_ERROR` | Periksa `error.fields` |
| 503 | `DATABASE_UNAVAILABLE` | Tampilkan gangguan layanan; jangan menyatakan password salah |

Default maksimum 5 percobaan per username dan 30 per IP dalam fixed window 15 menit.
Penghitungan mencakup percobaan berhasil, gagal, dan username tidak dikenal, setelah
verifikasi CSRF. Counter PostgreSQL atomik dibagi lintas worker dan bertahan restart.
Request ditolak tidak memperpanjang window. Login tidak mengungkap keberadaan username.
Server memakai alamat peer ASGI. Jika di belakang proxy, izinkan forwarded headers
hanya dari IP proxy tepercaya pada konfigurasi Uvicorn; jangan percaya header dari internet.

Jalankan cleanup berkala (misalnya setiap hari) agar baris sesi/counter tidak menumpuk:

```powershell
.\.venv\Scripts\python.exe -m app.manage_auth cleanup
```

## CORS dan deployment

```dotenv
APP_ENV=production
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
AUTH_SESSION_SECONDS=28800
AUTH_LOGIN_WINDOW_SECONDS=900
AUTH_LOGIN_MAX_ATTEMPTS=5
AUTH_LOGIN_IP_MAX_ATTEMPTS=30
CORS_ORIGINS=https://dashboard.example.com
```

Origin adalah scheme + hostname + port tanpa trailing slash/path; wildcard ditolak.
Respons CORS mengizinkan credentials, `Content-Type`, `X-CSRF-Token`, dan mengekspos
`X-Request-ID` serta `Retry-After`. Untuk pengembangan HTTP gunakan hostname konsisten
(`localhost` di FE dan BE, atau keduanya `127.0.0.1`), `APP_ENV=development`,
`AUTH_COOKIE_SECURE=false`, `AUTH_COOKIE_SAMESITE=lax`.

Produksi sebaiknya FE dan API berada di site yang sama, misalnya
`dashboard.example.com` dan `api.example.com`, atau proxy API lewat domain FE.
Jika FE dan BE benar-benar berbeda site, set `AUTH_COOKIE_SAMESITE=none` dan
`AUTH_COOKIE_SECURE=true`; browser dapat memblokir third-party cookie sehingga proxy
di site FE lebih andal. Nilai Secure=false pada production/None ditolak saat startup.

Perubahan password administratif harus mengunci row pengguna dan mencabut seluruh
sesi pengguna dalam transaksi yang sama. Aplikasi ini belum menyediakan UI reset
password, registrasi publik, atau MFA. Akun dashboard terpisah dari kredensial FusionSolar.

## Pengujian

`python -m pytest -q` mencakup tes integrasi PostgreSQL terisolasi untuk auth.
Set `TEST_POSTGRES_BIN` ke direktori binary `initdb`/`pg_ctl` bila belum terdeteksi.
Tes menyiapkan cluster sementara, bind hanya ke loopback dengan port acak, lalu
menghentikannya; database aplikasi di `.env` tidak digunakan. Bila binary tidak ada,
tes integrasi ditandai skipped; tes lainnya tetap berjalan.
