from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row
import pytest

from app import config

from app.auth import csrf_token
from app.repositories import auth_repository
from app.security import token_hash
from app.services.values import utc_now


@pytest.fixture
def authenticated_admin(monkeypatch):
    token = "test-session-token"
    csrf = csrf_token(token)
    user = {"id": "00000000-0000-0000-0000-000000000001", "username": "admin",
            "display_name": "Test admin", "role": "admin", "plant_codes": [],
            "csrf_hash": token_hash(csrf), "expires_at": utc_now() + timedelta(hours=8)}
    monkeypatch.setattr(auth_repository, "find_session", lambda hashed: user if hashed == token_hash(token) else None)
    return {"cookies": {"icongreen_session": token}, "headers": {"X-CSRF-Token": csrf}}


@pytest.fixture(scope="module")
def auth_postgres(tmp_path_factory):
    # Use installed binaries, or set TEST_POSTGRES_BIN. No existing database is reused.
    fallback = "C:/Program Files/PostgreSQL/17/bin" if os.name == "nt" else ""
    binary = Path(os.getenv("TEST_POSTGRES_BIN", fallback))
    initdb = str(binary / ("initdb.exe" if os.name == "nt" else "initdb")) if binary.is_dir() else shutil.which("initdb")
    pgctl = str(binary / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")) if binary.is_dir() else shutil.which("pg_ctl")
    if not initdb or not pgctl or not Path(initdb).exists() or not Path(pgctl).exists():
        pytest.skip("PostgreSQL binaries unavailable; set TEST_POSTGRES_BIN for isolated integration tests")
    temp = tmp_path_factory.mktemp("isolated-postgres")
    data = temp / "data"
    kwargs = {"check": True, "capture_output": True, "text": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run([initdb, "-D", str(data), "-U", "auth_test", "-A", "trust", "--encoding=UTF8", "--no-locale"], **kwargs)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    options = f"-h 127.0.0.1 -p {port} -F"
    if os.name != "nt":
        options += f" -k {temp}"
    # pg_ctl's detached Windows child inherits pipe handles; avoid capture_output
    # on daemon startup so communicate() cannot wait forever for EOF.
    daemon_kwargs = {k: v for k, v in kwargs.items() if k != "capture_output"}
    daemon_kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    subprocess.run([pgctl, "-D", str(data), "-l", str(temp / "postgres.log"), "-o", options, "-w", "start"], **daemon_kwargs)
    try:
        @contextmanager
        def connect():
            with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user="auth_test", row_factory=dict_row) as conn:
                yield conn
        with connect() as conn:
            conn.execute("CREATE TABLE public.plants (plant_code text PRIMARY KEY)")
            conn.execute("INSERT INTO public.plants VALUES ('A'), ('B')")
            conn.execute((config.PROJECT_ROOT / "migrations/002_auth.sql").read_text(encoding="utf-8"))
        yield connect
    finally:
        subprocess.run([pgctl, "-D", str(data), "-m", "fast", "-w", "stop"], **daemon_kwargs)
