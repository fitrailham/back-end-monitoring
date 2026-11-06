import logging

from fastapi import (
    FastAPI,
    HTTPException
)

from fastapi.middleware.cors import (
    CORSMiddleware
)

from app.config import CORS_ORIGINS
from app.database import fetch_rows

from app.api import (
    plants,
    monitoring,
    devices,
    alarms
)


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    )
)


app = FastAPI(
    title="IconGreen Solar Monitoring",
    version="1.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"]
)


# Register API routers
for module in (
    plants,
    monitoring,
    devices,
    alarms
):
    app.include_router(module.router)


@app.get("/")
def home():
    return {
        "success": True,
        "status": "running",
        "backend": "FastAPI"
    }


@app.get("/api/database/test")
def test_database():

    try:
        result = fetch_rows("""
            SELECT
                current_database() AS database,
                current_user AS username,
                NOW() AS server_time
        """)

        return {
            "success": True,
            "data": result[0]
        }

    except Exception:

        logging.exception(
            "[DB] Connection failed"
        )

        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        ) from None