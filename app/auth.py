"""Browser sessions and request-scoped plant authorization."""
from contextvars import ContextVar
import secrets

from fastapi import Depends, Header, Request, Response
from fastapi.security import APIKeyCookie

from app import config
from app.errors import ApiException
from app.repositories import auth_repository
from app.security import token_hash

session_cookie = APIKeyCookie(name=config.AUTH_COOKIE_NAME, auto_error=False)
current_user: ContextVar[dict | None] = ContextVar("dashboard_user", default=None)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token(session_token):
    return token_hash("csrf:" + session_token)


def public_user(user):
    return {"id": str(user["id"]), "username": user["username"],
            "display_name": user["display_name"], "role": user["role"],
            "plant_access": {"all_plants": user["role"] == "admin",
                             "plant_codes": user.get("plant_codes", [])}}


def check_origin(request):
    origin = request.headers.get("origin")
    if origin is not None and origin not in config.CORS_ORIGINS:
        raise ApiException(403, "ORIGIN_DENIED", "Origin request tidak diizinkan.")


def check_csrf(request, expected_hash):
    check_origin(request)
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secrets.compare_digest(token_hash(supplied), expected_hash):
        raise ApiException(403, "CSRF_INVALID", "Token CSRF tidak valid.")


def require_session(request: Request, response: Response, token: str | None = Depends(session_cookie),
                    csrf_header: str | None = Header(None, alias="X-CSRF-Token",
                        description="Required for POST/PATCH/PUT/DELETE with a session; obtain from login or /api/auth/me.")):
    if not token or len(token) > 256:
        raise ApiException(401, "UNAUTHORIZED", "Sesi tidak valid atau sudah berakhir.")
    user = auth_repository.find_session(token_hash(token))
    if not user:
        raise ApiException(401, "UNAUTHORIZED", "Sesi tidak valid atau sudah berakhir.")
    if request.method in UNSAFE_METHODS:
        check_csrf(request, user["csrf_hash"])
    response.headers["Cache-Control"] = "no-store"
    return user


async def require_data_access(request: Request, user: dict = Depends(require_session)):
    """Sync handlers inherit this event-loop context in their worker thread."""
    code = request.path_params.get("plant_code") or request.query_params.get("plant_code")
    if code is not None and user["role"] != "admin" and code not in user.get("plant_codes", []):
        raise ApiException(403, "PLANT_ACCESS_DENIED", "Anda tidak memiliki akses ke PLTS ini.")
    marker = current_user.set(user)
    try:
        yield user
    finally:
        current_user.reset(marker)


def allowed_plant_codes():
    user = current_user.get()
    # Internal jobs have no HTTP context. Data routers always require a session.
    return None if user is None or user["role"] == "admin" else user.get("plant_codes", [])


def require_admin(user: dict = Depends(require_session)):
    if user["role"] != "admin":
        raise ApiException(403, "FORBIDDEN", "Fitur ini hanya tersedia untuk admin.")
    return user


def require_plant_write(user: dict = Depends(require_admin)):
    return user
