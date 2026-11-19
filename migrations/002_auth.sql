-- Apply explicitly with python -m app.manage_auth migrate.
CREATE TABLE IF NOT EXISTS public.auth_users (
    id uuid PRIMARY KEY,
    username text NOT NULL UNIQUE CHECK (username = lower(username)),
    display_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'viewer')),
    password_hash text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.auth_user_plants (
    user_id uuid NOT NULL REFERENCES public.auth_users(id) ON DELETE CASCADE,
    plant_code text NOT NULL REFERENCES public.plants(plant_code) ON DELETE CASCADE,
    PRIMARY KEY (user_id, plant_code)
);
CREATE TABLE IF NOT EXISTS public.auth_sessions (
    token_hash text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES public.auth_users(id) ON DELETE CASCADE,
    csrf_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user ON public.auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires ON public.auth_sessions(expires_at);
-- Persistent counters are shared by API workers. Keys contain only hashes.
CREATE TABLE IF NOT EXISTS public.auth_login_limits (
    key text PRIMARY KEY,
    attempts integer NOT NULL DEFAULT 1,
    window_start timestamptz NOT NULL DEFAULT now()
);
