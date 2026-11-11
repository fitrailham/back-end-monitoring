-- Supporting indexes for the normalized schema. The tables are created separately.
BEGIN;
CREATE INDEX IF NOT EXISTS ix_realtime_plant_latest
    ON public.plant_realtime (plant_code, recorded_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_raw_plant_endpoint_latest
    ON public.fusionsolar_raw (plant_code, api_endpoint, collected_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_devices_plant_inventory
    ON public.devices (plant_code, inventory_collected_at DESC, id);
CREATE INDEX IF NOT EXISTS ix_device_realtime_plant_latest
    ON public.device_realtime (plant_code, collected_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_device_realtime_device_latest
    ON public.device_realtime (device_id, collected_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_alarms_plant_state_time
    ON public.alarms (plant_code, state, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_energy_statistics_lookup
    ON public.plant_energy_statistics (plant_code, grain, time_zone, start_at, end_at);
CREATE INDEX IF NOT EXISTS ix_source_state_endpoint
    ON public.plant_source_state (api_endpoint, configured, updated_at DESC);
COMMIT;
