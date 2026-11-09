import secrets

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import config
from app.errors import ApiException

bearer = HTTPBearer(auto_error=False)


def require_plant_write(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not config.PLANT_WRITE_TOKEN:
        raise ApiException(403, "PLANT_WRITE_DISABLED", "Operasi tulis PLTS belum diaktifkan administrator.")
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), config.PLANT_WRITE_TOKEN.encode()):
        raise ApiException(401, "UNAUTHORIZED", "Token izin tulis tidak valid.")
