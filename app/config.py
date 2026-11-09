import os
import json
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

FUSIONSOLAR_ALARMS_PATH = os.getenv("FUSIONSOLAR_ALARMS_PATH", "").strip()
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
