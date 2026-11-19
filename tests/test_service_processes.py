"""Exercise API and scheduler isolation against a disposable PostgreSQL cluster."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
import socket
import subprocess
import sys
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import httpx
import psycopg
import pytest

from app.database import db_session
from app.repositories import normalized_repository, realtime_repository, sync_repository
from app.scheduler import worker_lock
from app.security import hash_password
from app.sync_runtime import ShutdownRequested, WorkerConnectionLost, job_budget


API = """
import sys
from threading import Thread

from uvicorn import Config, Server

config = Config("app.main:app", host="127.0.0.1", port=int(sys.argv[1]))
config.load()
assert "app.scheduler.scheduler" not in sys.modules
server = Server(config)

def stop():
    sys.stdin.readline()
    server.should_exit = True

Thread(target=stop, daemon=True).start()
server.run()
"""

WORKER = """
from contextlib import nullcontext
from pathlib import Path
import sys
from threading import Event, Thread

from app.scheduler import scheduler, terminal_logging, worker_lock

class Vendor:
    def get_stations(self):
        return {"data": [{"stationCode": "TEST", "stationName": "Isolated PLTS"}]}

stop = Event()

def stop_on_stdin():
    sys.stdin.readline()
    stop.set()

Thread(target=stop_on_stdin, daemon=True).start()
terminal_logging.AUDIT_PATH = Path(sys.argv[1]) / "scheduler.audit.jsonl"
terminal_logging.configure_scheduler_logging()
scheduler.scheduler_lock = lambda _: worker_lock.scheduler_lock(Path(sys.argv[1]) / sys.argv[2])
scheduler.FusionSolarClient = lambda: nullcontext(Vendor())
for job in (scheduler.inventory_sync, scheduler.realtime_sync, scheduler.device_realtime_sync, scheduler.alarm_sync):
    job.run = lambda client: 0
scheduler.main(stop)
"""


@pytest.fixture(scope="module")
def worker_schema(auth_postgres):
    with auth_postgres() as conn:
        conn.execute("""
            ALTER TABLE public.plants ADD COLUMN plant_name text, ADD COLUMN capacity_kwp numeric,
                ADD COLUMN address text, ADD COLUMN is_demo bool DEFAULT FALSE,
                ADD COLUMN updated_at timestamptz;
            CREATE TABLE public.sync_logs (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_name text, plant_code text,
                started_at timestamptz DEFAULT NOW(), finished_at timestamptz, status text,
                records_inserted int DEFAULT 0, error_message text
            );
            CREATE TABLE public.plant_source_state (
                plant_code text, source_server text, api_endpoint text, last_attempt_at timestamptz,
                last_success_at timestamptz, last_result_status text, last_result_complete bool,
                stale_after_seconds int, PRIMARY KEY (plant_code, source_server, api_endpoint)
            );
            CREATE TABLE public.devices (
                plant_code text, source_server text, vendor_device_type_id text, inventory_collected_at timestamptz
            );
            CREATE TABLE public.plant_realtime (
                plant_code text, recorded_at timestamptz, pv_power_kw numeric, load_power_kw numeric,
                grid_power_kw numeric, yield_today_kwh numeric, total_yield_kwh numeric,
                battery_power_kw numeric, battery_soc_percent numeric, ess_charge_today_kwh numeric,
                ess_discharge_today_kwh numeric, data_source text, raw_data jsonb,
                PRIMARY KEY (plant_code, recorded_at)
            );
            CREATE TABLE public.device_realtime (
                device_id bigint, plant_code text, source_endpoint text, measured_at timestamptz,
                collected_at timestamptz, measurement_time_verified bool, expected_interval_seconds int,
                active_power_kw numeric, pv_input_power_kw numeric, load_power_kw numeric, grid_power_kw numeric,
                battery_power_kw numeric, battery_soc_percent numeric, yield_today_kwh numeric, total_yield_kwh numeric,
                ess_charge_today_kwh numeric, ess_discharge_today_kwh numeric, irradiance_w_m2 numeric,
                yield_business_date date, power_direction_convention text, status text, status_reason text,
                field_quality jsonb, raw_response_id bigint, raw_data jsonb,
                PRIMARY KEY (device_id, source_endpoint, measured_at)
            );
            INSERT INTO public.plant_source_state VALUES
                ('A','test-source','getStationRealKpi',NOW(),NOW(),'success',true,900),
                ('B','test-source','getStationRealKpi',NOW(),NOW()-INTERVAL '1 hour','success',true,900);
        """)
        conn.execute("""
            INSERT INTO public.auth_users (id,username,display_name,role,password_hash)
            VALUES (%s,'operator','Test operator','admin',%s)
        """, (uuid4(), hash_password("isolated test account password")))
        port = conn.info.port
    return auth_postgres, port


@contextmanager
def process(script, args, env, output):
    with output.open("w", encoding="utf-8") as destination:
        child = subprocess.Popen(
            [sys.executable, "-c", script, *map(str, args)], stdin=subprocess.PIPE, stdout=destination,
            stderr=subprocess.STDOUT, env=env, text=True,
        )
        try:
            yield child
        finally:
            if child.poll() is None:
                try:
                    child.communicate(input="\n", timeout=12)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate(timeout=5)
            assert child.returncode == 0, output.read_text(encoding="utf-8")


def eventually(check, output, timeout=25):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            if check():
                return
        except httpx.RequestError:
            pass
        sleep(0.15)
    pytest.fail(output.read_text(encoding="utf-8"))


def test_uvicorn_processes_and_scheduler_restart_are_independent(worker_schema, tmp_path):
    connect, port = worker_schema
    env = {**os.environ, "DB_HOST": "127.0.0.1", "DB_PORT": str(port), "DB_NAME": "postgres",
           "DB_USER": "auth_test", "DB_PASSWORD": "isolated-fixture", "FUSIONSOLAR_ENABLED": "true",
           "FUSIONSOLAR_BASE_URL": "https://127.0.0.1", "FUSIONSOLAR_SOURCE_SERVER": "test-source",
           "FUSIONSOLAR_ALARMS_PATH": "", "FUSIONSOLAR_DEVICE_REALTIME_ENABLED": "false",
           "AUTH_COOKIE_SECURE": "false", "APP_ENV": "development", "LOG_LEVEL": "INFO",
           "SCHEDULER_JOB_TIMEOUT_SECONDS": "600", "SCHEDULER_DB_TIMEOUT_SECONDS": "5",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        http_port, other_port = first.getsockname()[1], second.getsockname()[1]
    server_log = tmp_path / "api.log"
    other_api_log = tmp_path / "api-other.log"
    worker_log = tmp_path / "worker.log"

    def runs():
        with connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM public.sync_logs WHERE job_name='plant_sync' AND status='SUCCESS'"
            ).fetchone()["n"]

    with process(API, (http_port,), env, server_log), process(API, (other_port,), env, other_api_log), httpx.Client(
        base_url=f"http://127.0.0.1:{http_port}", timeout=2, trust_env=False
    ) as http:
        eventually(lambda: http.get("/").status_code == 200, server_log)
        other_api = f"http://127.0.0.1:{other_port}/"
        eventually(lambda: http.get(other_api).status_code == 200, other_api_log)
        initial = runs()
        assert http.get("/api/database/sync-health").status_code == 401
        csrf = http.get("/api/auth/csrf").json()["data"]["csrf_token"]
        login = http.post("/api/auth/login", headers={"X-CSRF-Token": csrf},
                          json={"username": "operator", "password": "isolated test account password"})
        assert login.status_code == 200, login.text
        assert http.get("/api/database/test").json()["data"]["database"] == "postgres"
        health = {row["api_endpoint"]: row for row in http.get("/api/database/sync-health").json()["data"]}
        assert (health["getStationRealKpi"]["fresh"], health["getStationRealKpi"]["stale"]) == (1, 1)
        assert runs() == initial

        with process(WORKER, (tmp_path, "first.lock"), env, worker_log):
            eventually(lambda: runs() == initial + 1, worker_log)
            duplicate_log = tmp_path / "duplicate.log"
            with process(WORKER, (tmp_path, "other-checkout.lock"), env, duplicate_log) as duplicate:
                assert duplicate.wait(timeout=12) == 0
            assert "WORKER_DUPLICATE" in duplicate_log.read_text(encoding="utf-8")
            assert runs() == initial + 1
            assert http.get("/api/database/test").status_code == 200
        assert http.get("/").status_code == 200
        assert http.get(other_api).status_code == 200
        with process(WORKER, (tmp_path, "first.lock"), env, worker_log):
            eventually(lambda: runs() == initial + 2, worker_log)
            assert http.get("/api/database/test").status_code == 200
            assert http.get("/api/database/sync-logs").json()["data"][0]["status"] == "SUCCESS"
        with connect() as conn:
            assert conn.execute("SELECT count(*) AS n FROM public.plants WHERE plant_code='TEST'").fetchone()["n"] == 1


def test_deadline_shutdown_and_interrupted_history_use_the_locked_session(worker_schema, monkeypatch):
    connect, port = worker_schema

    def raw_connection():
        from psycopg.rows import dict_row
        return psycopg.connect(host="127.0.0.1", port=port, user="auth_test", dbname="postgres", row_factory=dict_row)

    monkeypatch.setattr(worker_lock, "open_db", raw_connection)
    stop = Event()
    with worker_lock.postgres_scheduler_lock(5):
        with pytest.raises(psycopg.errors.QueryCanceled), job_budget(stop, 0.2), db_session() as conn:
            conn.execute("SELECT pg_sleep(2)")

        with job_budget(stop, 5):
            job = sync_repository.start_job("alarm_sync")
            with pytest.raises(ShutdownRequested), db_session() as conn:
                conn.execute("INSERT INTO public.plants (plant_code) VALUES ('ABORTED')")
                stop.set()
            sync_repository.finish_job(job, False, error="Shutdown")
            stop.clear()
        with db_session() as conn:
            assert conn.execute("SELECT 1 FROM public.plants WHERE plant_code='ABORTED'").fetchone() is None
            assert conn.execute("SELECT status FROM public.sync_logs WHERE id=%s", (job,)).fetchone()["status"] == "FAILED"
            old = sync_repository.start_job("realtime_sync")
            conn.execute("UPDATE public.sync_logs SET started_at=NOW()-INTERVAL '1 hour' WHERE id=%s", (old,))
            pid = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
        with connect() as conn:
            conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
        with pytest.raises(WorkerConnectionLost), db_session() as conn:
            conn.execute("SELECT 1")

    with worker_lock.postgres_scheduler_lock(5):
        assert sync_repository.recover_interrupted_jobs(600) >= 1
        with db_session() as conn:
            row = conn.execute("SELECT status,error_message FROM public.sync_logs WHERE id=%s", (old,)).fetchone()
        assert row["status"] == "FAILED" and row["error_message"].startswith("WORKER_INTERRUPTED")


def test_snapshot_replay_preserves_missing_fields_and_explicit_zero(worker_schema):
    connect, _ = worker_schema
    now = datetime.now(timezone.utc)
    plant = {"plant_code": "A", "recorded_at": now, "pv_power_kw": 12, "yield_today_kwh": 40}
    device = {"device_id": 1, "plant_code": "A", "source_endpoint": "getDevRealKpi",
              "measured_at": now, "collected_at": now, "measurement_time_verified": True,
              "active_power_kw": 12, "yield_today_kwh": 40, "power_direction_convention": "unverified",
              "status": "normal", "field_quality": {"active_power_kw": "VALID", "yield_today_kwh": "VALID"}}
    with connect() as conn:
        realtime_repository.save_snapshot(plant, conn)
        realtime_repository.save_snapshot({**plant, "pv_power_kw": None, "yield_today_kwh": None}, conn)
        normalized_repository.save_device_realtime(device, None, conn)
        normalized_repository.save_device_realtime({
            **device, "collected_at": now + timedelta(seconds=1), "active_power_kw": None,
            "yield_today_kwh": None, "field_quality": {},
        }, None, conn)
        assert conn.execute("SELECT pv_power_kw, yield_today_kwh FROM public.plant_realtime").fetchone() == {
            "pv_power_kw": 12, "yield_today_kwh": 40,
        }
        assert conn.execute("SELECT active_power_kw,yield_today_kwh FROM public.device_realtime").fetchone() == {
            "active_power_kw": 12, "yield_today_kwh": 40,
        }
        realtime_repository.save_snapshot({**plant, "pv_power_kw": 0}, conn)
        normalized_repository.save_device_realtime({
            **device, "collected_at": now + timedelta(seconds=2), "active_power_kw": 0,
        }, None, conn)
        assert conn.execute("SELECT pv_power_kw FROM public.plant_realtime").fetchone()["pv_power_kw"] == 0
        assert conn.execute("SELECT active_power_kw FROM public.device_realtime").fetchone()["active_power_kw"] == 0
