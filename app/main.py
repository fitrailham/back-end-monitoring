import logging

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
)

from fastapi.middleware.cors import (
    CORSMiddleware
)

from app.config import CORS_ORIGINS
from app.auth import require_admin, require_data_access
from app.database import fetch_rows
from app.errors import install_error_handlers
from app.openapi import install_examples
from app.logging_config import configure_logging

from app.api import (
    auth,
    plants,
    monitoring,
    devices,
    alarms,
    portfolio,
    analytics,
    dashboard_home,
)


configure_logging()


app = FastAPI(
    title="IconGreen Solar Monitoring",
    version="1.2"
)

install_error_handlers(app)
install_examples(app)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


# Register API routers
app.include_router(auth.router)
for module in (
    plants,
    monitoring,
    devices,
    alarms,
    portfolio,
    analytics,
    dashboard_home,
):
    app.include_router(module.router, dependencies=[Depends(require_data_access)])


@app.get("/")
def home():
    return {
        "success": True,
        "status": "running",
        "backend": "FastAPI"
    }


@app.get("/api/database/test", dependencies=[Depends(require_admin)])
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
