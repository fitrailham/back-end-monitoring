"""Real PostgreSQL auth tests in an isolated temporary cluster, never the app DB."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import os
from pathlib import Path
import shutil
import socket
import subprocess
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row
import pytest

from app import config, manage_auth
from app.auth import current_user
from app.errors import ApiException
from app.main import app
from app.repositories import auth_repository, fleet_repository, plant_admin_repository
from app.security import hash_password, token_hash, verify_password
from app.services.values import utc_now


@pytest.fixture(scope="module")
def auth_postgres(tmp_path_factory):
    # Use installed binaries, or set TEST_POSTGRES_BIN. No existing database is reused.
    fallback = "C:/Program Files/PostgreSQL/17/bin" if os.name == "nt" else ""
    binary = Path(os.getenv("TEST_POSTGRES_BIN", fallback))
    initdb = str(binary / ("initdb.exe" if os.name == "nt" else "initdb")) if binary.is_dir() else shutil.which("initdb")
    pgctl = str(binary / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")) if binary.is_dir() else shutil.which("pg_ctl")
    if not initdb or not pgctl or not Path(initdb).exists() or not Path(pgctl).exists():
        pytest.skip("PostgreSQL binaries unavailable; set TEST_POSTGRES_BIN for isolated integration tests")
    temp = tmp_path_factory.mktemp("auth-postgres")
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


@pytest.fixture(scope="module")
def encoded_password():
    return hash_password("A long test password 123!")


@pytest.fixture
def auth_client(monkeypatch, auth_postgres, encoded_password):
    monkeypatch.setattr(auth_repository, "open_db", auth_postgres)
    monkeypatch.setattr(manage_auth, "open_db", auth_postgres)
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(config, "FUSIONSOLAR_ALARMS_PATH", "")
    with auth_postgres() as conn:
        conn.execute("TRUNCATE public.auth_sessions, public.auth_user_plants, public.auth_users, public.auth_login_limits")
        for username in ("admin", "viewer"):
            user_id = uuid4()
            conn.execute("INSERT INTO public.auth_users (id, username, display_name, role, password_hash) VALUES (%s,%s,%s,%s,%s)",
                         (user_id, username, username.title(), username, encoded_password))
            if username == "viewer":
                conn.execute("INSERT INTO public.auth_user_plants VALUES (%s, 'A')", (user_id,))
    with TestClient(app) as client:
        yield client


def login(client, username="viewer", password="A long test password 123!"):
    csrf = client.get("/api/auth/csrf").json()["data"]["csrf_token"]
    return client.post("/api/auth/login", json={"username": username, "password": password},
                       headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:5173"})


def test_password_hash_is_salted_and_verifies(encoded_password):
    assert verify_password("A long test password 123!", encoded_password)
    assert not verify_password("wrong", encoded_password)
    assert not verify_password("password", "invalid")
    assert hash_password("A long test password 123!") != encoded_password


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/database/plants", "/api/database/plants/A",
    "/api/database/overview", "/api/dashboard/A", "/api/devices/A", "/api/alarms", "/api/alarms/A",
    "/api/database/history/A", "/api/database/yield-statistics", "/api/database/rankings",
    "/api/portfolio/summary", "/api/database/power-statistics", "/api/reports/inverters",
    "/api/devices/V1/history", "/api/database/sync-logs", "/api/database/test"])
def test_data_routes_require_session(auth_client, path):
    response = auth_client.get(path)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_login_me_logout_and_replay(auth_client, auth_postgres):
    result = login(auth_client)
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["user"]["plant_access"] == {"all_plants": False, "plant_codes": ["A"]}
    assert "password" not in result.text
    cookie = result.headers.get_list("set-cookie")[0]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    raw_token = auth_client.cookies[config.AUTH_COOKIE_NAME]
    with auth_postgres() as conn:
        row = conn.execute("SELECT * FROM public.auth_sessions").fetchone()
        assert row["token_hash"] == token_hash(raw_token) and raw_token not in str(row)
        lifetime = (row["expires_at"] - row["created_at"]).total_seconds()
        assert abs(lifetime - config.AUTH_SESSION_SECONDS) < 5
    me = auth_client.get("/api/auth/me")
    assert me.json()["data"] == data
    assert me.headers["Cache-Control"] == "no-store"
    result = auth_client.post("/api/auth/logout", headers={"X-CSRF-Token": data["csrf_token"]})
    assert result.status_code == 200 and config.AUTH_COOKIE_NAME not in auth_client.cookies
    auth_client.cookies.set(config.AUTH_COOKIE_NAME, raw_token)
    assert auth_client.get("/api/auth/me").status_code == 401


def test_login_csrf_and_origin_required(auth_client):
    payload = {"username": "admin", "password": "A long test password 123!"}
    assert auth_client.post("/api/auth/login", json=payload).status_code == 403
    csrf = auth_client.get("/api/auth/csrf").json()["data"]["csrf_token"]
    assert auth_client.post("/api/auth/login", json=payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    response = auth_client.post("/api/auth/login", json=payload,
        headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_DENIED"


def test_invalid_login_and_throttle(auth_client):
    for i in range(config.AUTH_LOGIN_MAX_ATTEMPTS):
        response = login(auth_client, password="wrong")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    response = login(auth_client)
    assert response.status_code == 429
    assert 0 < int(response.headers["Retry-After"]) <= config.AUTH_LOGIN_WINDOW_SECONDS
    assert response.json()["error"]["retry_after_seconds"] == int(response.headers["Retry-After"])


def test_unknown_and_inactive_have_same_error(auth_client, auth_postgres):
    unknown = login(auth_client, username="missing")
    with auth_postgres() as conn:
        conn.execute("UPDATE public.auth_users SET is_active = false WHERE username = 'viewer'")
    inactive = login(auth_client)
    assert unknown.status_code == inactive.status_code == 401
    assert unknown.json()["message"] == inactive.json()["message"]


def test_limit_is_atomic_across_workers(auth_client):
    def reserve(_):
        try:
            auth_repository.reserve_login("same-user", "same-ip")
            return 200
        except ApiException as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(reserve, range(8)))
    assert statuses.count(200) == config.AUTH_LOGIN_MAX_ATTEMPTS
    assert statuses.count(429) == 8 - config.AUTH_LOGIN_MAX_ATTEMPTS


def test_ip_limit_and_window_reset(auth_client, auth_postgres, monkeypatch):
    monkeypatch.setattr(config, "AUTH_LOGIN_IP_MAX_ATTEMPTS", 2)
    auth_repository.reserve_login("u1", "shared-ip")
    auth_repository.reserve_login("u2", "shared-ip")
    with pytest.raises(ApiException) as exc:
        auth_repository.reserve_login("u3", "shared-ip")
    assert exc.value.status == 429
    with auth_postgres() as conn:
        conn.execute("UPDATE public.auth_login_limits SET window_start = now() - interval '1 day'")
    auth_repository.reserve_login("u3", "shared-ip")


def test_expired_and_disabled_sessions(auth_client, auth_postgres):
    login(auth_client)
    with auth_postgres() as conn:
        conn.execute("UPDATE public.auth_sessions SET expires_at = now() - interval '1 second'")
    assert auth_client.get("/api/auth/me").status_code == 401
    login(auth_client)
    with auth_postgres() as conn:
        conn.execute("UPDATE public.auth_users SET is_active = false WHERE username = 'viewer'")
    assert auth_client.get("/api/auth/me").status_code == 401


def test_login_rotates_previous_session(auth_client):
    login(auth_client)
    previous = auth_client.cookies[config.AUTH_COOKIE_NAME]
    login(auth_client)
    assert auth_client.cookies[config.AUTH_COOKIE_NAME] != previous
    assert auth_repository.find_session(token_hash(previous)) is None


def test_csrf_and_role_enforced_on_write(auth_client, monkeypatch):
    monkeypatch.setattr(plant_admin_repository, "insert_plant", lambda _: pytest.fail("Unauthorized write"))
    data = login(auth_client).json()["data"]
    assert auth_client.post("/api/database/plants", json={}).status_code == 403
    response = auth_client.post("/api/database/plants", json={}, headers={"X-CSRF-Token": data["csrf_token"]})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    for path in ("/api/database/test", "/api/database/sync-logs"):
        assert auth_client.get(path).status_code == 403
    assert auth_client.post("/api/auth/logout").status_code == 403
    assert auth_client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("path", ["/api/database/plants/B", "/api/dashboard/B", "/api/devices/B", "/api/alarms/B",
    "/api/database/history/B", "/api/alarms?plant_code=B", "/api/database/power-statistics?plant_code=B",
    "/api/database/yield-statistics?plant_code=B", "/api/reports/inverters?plant_code=B",
    "/api/devices/V1/history?plant_code=B"])
def test_cross_plant_access_denied_before_data_queries(auth_client, monkeypatch, path):
    login(auth_client)
    monkeypatch.setattr(fleet_repository, "fleet", lambda *a, **k: pytest.fail("Must not query forbidden plant"))
    assert auth_client.get(path).status_code == 403


def test_fleet_scope_precedes_aggregation_and_pagination(auth_client, monkeypatch, auth_postgres):
    from test_frontend_api import sample_row
    seen = []
    def fleet(code=None, allowed_codes=None):
        seen.append(allowed_codes)
        return [sample_row("A", "Allowed"), sample_row("B", "Forbidden")]
    monkeypatch.setattr(fleet_repository, "fleet", fleet)
    login(auth_client)
    plants = auth_client.get("/api/database/plants").json()
    assert [r["plant_code"] for r in plants["data"]] == ["A"]
    page = auth_client.get("/api/database/overview?page_size=1").json()
    assert page["total"] == 1 and page["meta"]["coverage"]["capacity_kwp"]["expected"] == 1
    summary = auth_client.get("/api/portfolio/summary").json()
    assert summary["data"]["total_plants"] == 1
    assert summary["data"]["kpis"]["installed_pv_capacity_kwp"] == 100
    assert seen and all(codes == ["A"] for codes in seen)
    assert current_user.get() is None  # Request context never leaks into subsequent work.
    with auth_postgres() as conn:
        conn.execute("DELETE FROM public.auth_user_plants")
    assert auth_client.get("/api/database/plants").json()["data"] == []
    assert auth_client.get("/api/database/plants/A").status_code == 403
    login(auth_client, "admin")
    assert auth_client.get("/api/database/plants").json()["total"] == 2
    assert seen[-1] is None


def test_secure_cookie_and_cors(auth_client, monkeypatch):
    response = auth_client.options("/api/auth/login", headers={"Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-csrf-token"})
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert auth_client.options("/api/auth/login", headers={"Origin": "https://attacker.example",
        "Access-Control-Request-Method": "POST"}).status_code == 400
    csrf = auth_client.get("/api/auth/csrf").json()["data"]["csrf_token"]
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    response = auth_client.post("/api/auth/login", json={"username": "viewer", "password": "A long test password 123!"},
                                headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200 and "Secure" in response.headers.get_list("set-cookie")[0]


def test_seed_refuses_overwrite_and_validates_plants(auth_client, auth_postgres):
    with pytest.raises(ValueError, match="sudah ada"):
        manage_auth.seed(["A"], ".env.unused-test-file")
    with auth_postgres() as conn:
        conn.execute("TRUNCATE public.auth_users CASCADE")
    with pytest.raises(ValueError, match="tidak ditemukan"):
        manage_auth.seed(["missing"], ".env.unused-test-file")


def test_seed_and_migration_are_transactional(auth_client, auth_postgres, monkeypatch, tmp_path):
    with auth_postgres() as conn:
        conn.execute("TRUNCATE public.auth_users CASCADE")
    manage_auth.migrate()  # Safe to reapply without overwriting users or sessions.
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    private = tmp_path / ".env.auth-accounts.local"
    manage_auth.seed(["A"], str(private))
    import json
    accounts = json.loads(private.read_text())["accounts"]
    assert {a["username"] for a in accounts} == {"admin", "viewer"}
    with auth_postgres() as conn:
        users = conn.execute("SELECT username, password_hash FROM public.auth_users").fetchall()
        assert len(users) == 2
        for user in users:
            password = next(a["password"] for a in accounts if a["username"] == user["username"])
            assert verify_password(password, user["password_hash"])
    with pytest.raises(ValueError, match="sudah ada"):
        manage_auth.seed(["B"], str(private))
    assert private.exists()


def test_swagger_documents_cookie_and_auth_contract(auth_client):
    schema = auth_client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["APIKeyCookie"]["in"] == "cookie"
    for path in ("/api/auth/login", "/api/auth/me", "/api/auth/logout"):
        operation = schema["paths"][path]["get" if path.endswith("/me") else "post"]
        assert "429" in operation["responses"]
    for path in ("/api/database/plants", "/api/portfolio/summary", "/api/devices/{device_id}/history"):
        assert schema["paths"][path]["get"]["security"] == [{"APIKeyCookie": []}]
