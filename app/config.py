import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

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