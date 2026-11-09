-- Additive migration. Existing reads work before this migration is applied.
BEGIN;
ALTER TABLE public.plants ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS ix_realtime_plant_latest
    ON public.plant_realtime (plant_code, recorded_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_raw_plant_endpoint_latest
    ON public.fusionsolar_raw (plant_code, api_endpoint, collected_at DESC, id DESC);
COMMIT;
