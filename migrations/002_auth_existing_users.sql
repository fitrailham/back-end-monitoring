-- Alternative setup when public.auth_users already exists.
-- Reads existing key types; does not create or alter auth_users or plants.
-- Run this complete script in a PostgreSQL SQL editor.
BEGIN;

DO $auth$
DECLARE
    user_id_type TEXT;
    plant_code_type TEXT;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod)
    INTO user_id_type
    FROM pg_attribute a
    WHERE a.attrelid = to_regclass('public.auth_users')
      AND a.attname = 'id'
      AND a.attnum > 0
      AND NOT a.attisdropped;

    SELECT format_type(a.atttypid, a.atttypmod)
    INTO plant_code_type
    FROM pg_attribute a
    WHERE a.attrelid = to_regclass('public.plants')
      AND a.attname = 'plant_code'
      AND a.attnum > 0
      AND NOT a.attisdropped;

    IF user_id_type IS NULL THEN
        RAISE EXCEPTION 'Kolom public.auth_users.id tidak ditemukan.';
    END IF;
    IF plant_code_type IS NULL THEN
        RAISE EXCEPTION 'Kolom public.plants.plant_code tidak ditemukan.';
    END IF;

    -- 1. Hak akses pengguna ke PLTS
    EXECUTE format($ddl$
        CREATE TABLE IF NOT EXISTS public.auth_user_plants (
            user_id %s NOT NULL,
            plant_code %s NOT NULL,
            PRIMARY KEY (user_id, plant_code),
            FOREIGN KEY (user_id)
                REFERENCES public.auth_users(id) ON DELETE CASCADE,
            FOREIGN KEY (plant_code)
                REFERENCES public.plants(plant_code) ON DELETE CASCADE
        )
    $ddl$, user_id_type, plant_code_type);

    -- 2. Sesi login pengguna
    EXECUTE format($ddl$
        CREATE TABLE IF NOT EXISTS public.auth_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id %s NOT NULL
                REFERENCES public.auth_users(id) ON DELETE CASCADE,
            csrf_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ
        )
    $ddl$, user_id_type);
END;
$auth$;

-- 3. Pembatasan percobaan login
CREATE TABLE IF NOT EXISTS public.auth_login_limits (
    key TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 1,
    window_start TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_auth_sessions_user
    ON public.auth_sessions(user_id);

CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires
    ON public.auth_sessions(expires_at);

COMMIT;
