"""PostgreSQL session storage and atomic, multi-worker login throttling."""
from datetime import timedelta
import math

from app import config
from app.database import open_db
from app.errors import ApiException


def reserve_login(username_key, ip_key):
    retry_after = 0
    with open_db() as conn:
        for key, limit in (("user:" + username_key, config.AUTH_LOGIN_MAX_ATTEMPTS),
                           ("ip:" + ip_key, config.AUTH_LOGIN_IP_MAX_ATTEMPTS)):
            row = conn.execute("""
                INSERT INTO public.auth_login_limits (key) VALUES (%s)
                ON CONFLICT (key) DO UPDATE SET
                    attempts = CASE WHEN auth_login_limits.window_start <= now() - %s
                        THEN 1 ELSE LEAST(auth_login_limits.attempts + 1, %s) END,
                    window_start = CASE WHEN auth_login_limits.window_start <= now() - %s
                        THEN now() ELSE auth_login_limits.window_start END
                RETURNING attempts, EXTRACT(EPOCH FROM (window_start + %s - now())) AS remaining
            """, (key, timedelta(seconds=config.AUTH_LOGIN_WINDOW_SECONDS), limit + 1,
                  timedelta(seconds=config.AUTH_LOGIN_WINDOW_SECONDS),
                  timedelta(seconds=config.AUTH_LOGIN_WINDOW_SECONDS))).fetchone()
            if row["attempts"] > limit:
                retry_after = max(retry_after, math.ceil(row["remaining"]))
    # Raise after commit so rejected attempts cannot roll counters back.
    if retry_after:
        raise ApiException(429, "RATE_LIMITED", "Terlalu banyak percobaan login. Coba kembali nanti.",
                           True, retry_after_seconds=retry_after)


def user_by_username(username):
    with open_db() as conn:
        return conn.execute("SELECT * FROM public.auth_users WHERE username = %s", (username,)).fetchone()


def grants(conn, user_id):
    return [r["plant_code"] for r in conn.execute(
        "SELECT plant_code FROM public.auth_user_plants WHERE user_id = %s ORDER BY plant_code",
        (user_id,)).fetchall()]


def create_session(user_id, token_hash, csrf_hash, expires_at, expected_password_hash, previous_hash=None):
    with open_db() as conn:
        user = conn.execute("SELECT * FROM public.auth_users WHERE id = %s AND is_active AND password_hash = %s FOR UPDATE",
                            (user_id, expected_password_hash)).fetchone()
        if user is None:
            raise ApiException(401, "INVALID_CREDENTIALS", "Username atau password tidak valid.")
        if previous_hash:
            conn.execute("UPDATE public.auth_sessions SET revoked_at = now() WHERE token_hash = %s",
                         (previous_hash,))
        conn.execute("""
            INSERT INTO public.auth_sessions (token_hash, user_id, csrf_hash, expires_at)
            VALUES (%s, %s, %s, %s)
        """, (token_hash, user_id, csrf_hash, expires_at))
        user["plant_codes"] = grants(conn, user_id)
        return user


def find_session(token_hash):
    with open_db() as conn:
        row = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.role, s.csrf_hash, s.expires_at
            FROM public.auth_sessions s JOIN public.auth_users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now() AND u.is_active
        """, (token_hash,)).fetchone()
        if row:
            row["plant_codes"] = grants(conn, row["id"])
        return row


def revoke_session(token_hash):
    with open_db() as conn:
        conn.execute("UPDATE public.auth_sessions SET revoked_at = now() WHERE token_hash = %s",
                     (token_hash,))
