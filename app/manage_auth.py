"""Explicit auth migration and local, non-overwriting account provisioning."""
import argparse
import csv
import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess
from uuid import uuid4

from app import config
from app.database import open_db
from app.security import hash_password


def migrate():
    with open_db() as conn:
        conn.execute((config.PROJECT_ROOT / "migrations/002_auth.sql").read_text(encoding="utf-8"))
    print("Auth schema ready.")


def private_file(path, content):
    # Only ignored local files in this checkout; refuse accidental tracked exports.
    target = Path(path).resolve()
    if target.parent != config.PROJECT_ROOT or not target.name.startswith(".env.") or target.name == ".env.example":
        raise ValueError("Gunakan file .env.* di root repo, contoh .env.auth-accounts.local")
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if os.name == "nt":
            result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                    check=True, capture_output=True, text=True)
            sid = next(csv.reader([result.stdout.strip()]))[1]
            subprocess.run(["icacls", str(target), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                           check=True, capture_output=True)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(content, stream, indent=2)
            stream.write("\n")
    finally:
        if fd is not None:
            os.close(fd)


def seed(viewer_plants, credentials_file=None):
    codes = sorted(set(viewer_plants))
    if not codes:
        raise ValueError("Pilih minimal satu --viewer-plant yang sudah terdaftar.")
    users = []
    for username, role in (("admin", "admin"), ("viewer", "viewer")):
        if credentials_file:
            password = secrets.token_urlsafe(24)
        else:
            password = getpass.getpass(f"Password baru untuk {username} (minimal 15 karakter): ")
            if password != getpass.getpass("Ulangi password: "):
                raise ValueError("Konfirmasi password berbeda.")
        if not 15 <= len(password) <= 1024:
            raise ValueError("Password harus 15–1024 karakter.")
        users.append({"id": uuid4(), "username": username, "role": role,
                      "password": password, "password_hash": hash_password(password)})
    with open_db() as conn:
        existing = conn.execute("SELECT username FROM public.auth_users WHERE username IN ('admin', 'viewer')").fetchall()
        if existing:
            raise ValueError("Akun admin/viewer sudah ada. Seed tidak menimpa akun, password, atau grant.")
        found = {r["plant_code"] for r in conn.execute(
            "SELECT plant_code FROM public.plants WHERE plant_code = ANY(%s)", (codes,)).fetchall()}
        if found != set(codes):
            raise ValueError("Kode PLTS tidak ditemukan: " + ", ".join(sorted(set(codes) - found)))
        for user in users:
            conn.execute("""
                INSERT INTO public.auth_users (id, username, display_name, role, password_hash)
                VALUES (%s, %s, %s, %s, %s)
            """, (user["id"], user["username"], "Dashboard " + user["role"].title(), user["role"], user["password_hash"]))
            if user["role"] == "viewer":
                for code in codes:
                    conn.execute("INSERT INTO public.auth_user_plants (user_id, plant_code) VALUES (%s, %s)",
                                 (user["id"], code))
        if credentials_file:
            private_file(credentials_file, {"accounts": [
                {"username": u["username"], "password": u["password"], "role": u["role"],
                 "plant_access": {"all_plants": u["role"] == "admin", "plant_codes": codes if u["role"] == "viewer" else []}}
                for u in users]})
    print("Akun admin (semua PLTS) dan viewer (PLTS terpilih) dibuat.")
    if credentials_file:
        print("Kredensial tersimpan di file lokal privat; password tidak dicetak.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    create = commands.add_parser("seed")
    create.add_argument("--viewer-plant", action="append", required=True)
    create.add_argument("--credentials-file", help="Generate passwords into a private, ignored .env.* file; otherwise prompt.")
    commands.add_parser("cleanup", help="Delete expired/revoked sessions and expired login counters.")
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()
    elif args.command == "seed":
        seed(args.viewer_plant, args.credentials_file)
    else:
        from datetime import timedelta
        with open_db() as conn:
            conn.execute("DELETE FROM public.auth_sessions WHERE expires_at <= now() OR revoked_at IS NOT NULL")
            conn.execute("DELETE FROM public.auth_login_limits WHERE window_start <= now() - %s",
                         (timedelta(seconds=config.AUTH_LOGIN_WINDOW_SECONDS),))
        print("Expired auth records removed.")


if __name__ == "__main__":
    main()
