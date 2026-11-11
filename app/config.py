import os
import json
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

FUSIONSOLAR_ALARMS_PATH = os.getenv("FUSIONSOLAR_ALARMS_PATH", "").strip()
FUSIONSOLAR_BASE_URL = os.getenv("FUSIONSOLAR_BASE_URL", "").strip()
FUSIONSOLAR_SOURCE_SERVER = os.getenv("FUSIONSOLAR_SOURCE_SERVER", "").strip() or (
    urlparse(FUSIONSOLAR_BASE_URL).hostname or "fusionsolar"
)
FUSIONSOLAR_ALARM_LANGUAGE = os.getenv("FUSIONSOLAR_ALARM_LANGUAGE", "en_US").strip()
FUSIONSOLAR_ALARM_LOOKBACK_HOURS = max(1, int(os.getenv("FUSIONSOLAR_ALARM_LOOKBACK_HOURS", "24")))
FUSIONSOLAR_DEVICE_REALTIME_ENABLED = os.getenv("FUSIONSOLAR_DEVICE_REALTIME_ENABLED", "false").lower() == "true"
FUSIONSOLAR_DEVICE_REALTIME_TYPE_IDS = tuple(
    item.strip() for item in os.getenv("FUSIONSOLAR_DEVICE_REALTIME_TYPE_IDS", "1,17,39,41,47").split(",")
    if item.strip()
)
FUSIONSOLAR_POWER_DIRECTION_CONVENTION = os.getenv(
    "FUSIONSOLAR_POWER_DIRECTION_CONVENTION", "unverified"
).strip()
if FUSIONSOLAR_POWER_DIRECTION_CONVENTION not in (
    "unverified", "grid_import_battery_discharge_positive"
):
    raise ValueError("FUSIONSOLAR_POWER_DIRECTION_CONVENTION tidak valid")
INVENTORY_SYNC_INTERVAL_HOURS = max(1, int(os.getenv("INVENTORY_SYNC_INTERVAL_HOURS", "24")))
INVENTORY_STALE_AFTER_SECONDS = max(
    INVENTORY_SYNC_INTERVAL_HOURS * 3600,
    int(os.getenv("INVENTORY_STALE_AFTER_SECONDS", str(26 * 3600))),
)
SYNC_INTERVAL_MINUTES = max(10, int(os.getenv("SYNC_INTERVAL_MINUTES", "10")))
EXPECTED_INTERVAL_SECONDS = SYNC_INTERVAL_MINUTES * 60
PLANT_WRITE_TOKEN = os.getenv("PLANT_WRITE_TOKEN", "")
# Extend only using the NBI reference for the deployed vendor version.
DEVICE_TYPE_MAP = {
    "1": "inverter", "38": "inverter", "39": "ess", "41": "ess",
    "10": "emi", "17": "meter", "47": "meter", "2": "logger",
    "62": "dongle", "63": "logger",
}
DEVICE_TYPE_MAP.update(json.loads(os.getenv("FUSIONSOLAR_DEVICE_TYPE_MAP", "{}")))

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")

DB_PORT = int(os.getenv("DB_PORT", "15432"))

DB_NAME = os.getenv(
    "DB_NAME",
    "icongreen_monitoring"
)

DB_USER = os.getenv(
    "DB_USER",
    "icongreen_monitor_dev"
)

DB_PASSWORD = os.getenv("DB_PASSWORD", "")

STALE_AFTER_MINUTES = int(
    os.getenv("STALE_AFTER_MINUTES", "15")
)

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
