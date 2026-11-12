"""Operator guidance for sync failures, without logging credentials or API bodies."""

import re

import httpx
import psycopg

from app.services.fusionsolar_client import FusionSolarError, NorthboundRateLimitError


def failure_details(exc: Exception) -> dict:
    """Expose the existing operator guidance as separate JSON fields."""
    category, _, description = failure_guidance(exc).partition("] ")
    reason, _, solution = description.partition(" Solusi: ")
    return {"code": category.lstrip("["), "reason": reason, "solution": solution}


def failure_guidance(exc: Exception) -> str:
    if isinstance(exc, NorthboundRateLimitError):
        return (
            f"[API_QUOTA] Northbound membatasi endpoint {exc.endpoint} dengan failCode=407. "
            f"Solusi: tunggu sekurangnya {exc.retry_after_seconds} detik; gunakan satu worker "
            "per akun. Cooldown lokal tidak menjamin kuota server sudah pulih."
        )
    if isinstance(exc, psycopg.Error):
        if exc.sqlstate in {"42804", "22P02"}:
            return (
                "[DB_VALUE_TYPE] Nilai tidak sesuai tipe kolom PostgreSQL. "
                "Solusi: periksa konversi nilai dan cast SQL; timestamp ISO pada CASE "
                "harus bertipe timestamptz."
            )
        if exc.sqlstate in {"42P01", "42703"}:
            return (
                "[DB_SCHEMA] Tabel atau kolom yang dibutuhkan query tidak ditemukan. "
                "Solusi: cocokkan skema database dengan query dan migrasi proyek."
            )
        if isinstance(exc, psycopg.OperationalError):
            return (
                "[DB_CONNECTION] Koneksi PostgreSQL gagal atau terputus. "
                "Solusi: periksa SSH tunnel, layanan PostgreSQL, dan konfigurasi DB pada .env."
            )
        return "[DB_ERROR] Operasi PostgreSQL gagal. Solusi: periksa SQLSTATE dan traceback berikut."
    if isinstance(exc, httpx.TimeoutException):
        return (
            "[API_TIMEOUT] Northbound tidak merespons dalam batas waktu. "
            "Solusi: periksa koneksi dan server FusionSolar; tunggu jadwal berikutnya."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "[API_RATE_LIMIT] Batas HTTP request tercapai. Solusi: tunggu jadwal berikutnya; hindari worker ganda."
        if status in {401, 403}:
            return "[API_ACCESS] Akses HTTP ditolak. Solusi: periksa sesi, akun, region, dan izin endpoint Northbound."
        return f"[API_HTTP] HTTP {status} dari Northbound. Solusi: periksa status layanan dan endpoint yang dipanggil."
    if isinstance(exc, httpx.RequestError):
        return "[API_CONNECTION] Koneksi Northbound gagal. Solusi: periksa jaringan, DNS, HTTPS, dan region server."
    if isinstance(exc, FusionSolarError):
        match = re.search(r"failCode=(\d+)\b", str(exc))
        code = match.group(1) if match else None
        if code == "407":
            return "[API_QUOTA] Northbound menolak request dengan failCode=407. Solusi: tunggu jadwal berikutnya; hindari worker ganda."
        if code == "305":
            return "[API_SESSION] Sesi Northbound tetap ditolak setelah login ulang. Solusi: periksa akun dan sesi aktif lainnya."
        return "[API_REJECTED] Respons Northbound ditolak. Solusi: periksa failCode, konfigurasi akun/region, dan izin endpoint."
    if isinstance(exc, ValueError):
        return (
            "[DATA_FORMAT] Data atau konfigurasi tidak memenuhi format yang diharapkan. "
            "Solusi: periksa kode plant, struktur respons, dan pesan validasi pada traceback berikut."
        )
    return "[SYNC_UNEXPECTED] Sinkronisasi gagal. Solusi: periksa jenis exception dan traceback berikut sebelum mengubah mapping."
