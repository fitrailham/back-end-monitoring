from datetime import timedelta
import secrets
import time

from fastapi import APIRouter, Depends, Header, Request, Response

from app import config
from app.auth import check_csrf, check_origin, csrf_token, public_user, require_session
from app.errors import ApiException, ERROR_RESPONSES
from app.repositories import auth_repository as repository
from app.schemas.auth import CsrfResponse, LoginRequest, LogoutResponse, SessionResponse
from app.security import hash_password, token_hash, verify_password
from app.services.values import utc_now

router = APIRouter(prefix="/api/auth", tags=["Authentication"], responses=ERROR_RESPONSES)
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


@router.get("/csrf", response_model=CsrfResponse,
            description="Obtain a 10-minute pre-login CSRF token. Send it as X-CSRF-Token on login; include cookies.")
def login_csrf(response: Response):
    token = f"{int(time.time())}." + secrets.token_urlsafe(32)
    response.set_cookie("icongreen_login_csrf", token, max_age=600, path="/api/auth",
                        httponly=True, secure=config.AUTH_COOKIE_SECURE, samesite=config.AUTH_COOKIE_SAMESITE)
    response.headers["Cache-Control"] = "no-store"
    return {"success": True, "data": {"csrf_token": token}}


@router.post("/login", response_model=SessionResponse,
             description="JSON username/password. Requires pre-login CSRF cookie and X-CSRF-Token. Creates an HttpOnly session cookie, valid for AUTH_SESSION_SECONDS (8 hours by default).")
def login(payload: LoginRequest, request: Request, response: Response,
          csrf_header: str | None = Header(None, alias="X-CSRF-Token",
              description="Required: the data.csrf_token from GET /api/auth/csrf.")):
    check_origin(request)
    prelogin = request.cookies.get("icongreen_login_csrf", "")
    if not prelogin or len(prelogin) > 256:
        raise ApiException(403, "CSRF_INVALID", "Ambil token CSRF sebelum login.")
    try:
        age = time.time() - int(prelogin.split(".", 1)[0])
    except ValueError:
        age = 601
    if not 0 <= age <= 600:
        raise ApiException(403, "CSRF_INVALID", "Token CSRF login berakhir. Ambil token baru.")
    check_csrf(request, token_hash(prelogin))
    username = payload.username.strip().lower()
    peer = request.client.host if request.client else "unknown"
    repository.reserve_login(token_hash(username), token_hash(peer))
    user = repository.user_by_username(username)
    valid = verify_password(payload.password.get_secret_value(),
                            user["password_hash"] if user else DUMMY_PASSWORD_HASH)
    if not valid or not user or not user["is_active"]:
        raise ApiException(401, "INVALID_CREDENTIALS", "Username atau password tidak valid.")
    token = secrets.token_urlsafe(32)
    csrf = csrf_token(token)
    expires = utc_now() + timedelta(seconds=config.AUTH_SESSION_SECONDS)
    previous = request.cookies.get(config.AUTH_COOKIE_NAME)
    user = repository.create_session(user["id"], token_hash(token), token_hash(csrf), expires,
                                     user["password_hash"], token_hash(previous) if previous else None)
    response.set_cookie(config.AUTH_COOKIE_NAME, token, max_age=config.AUTH_SESSION_SECONDS,
                        expires=expires, path="/", httponly=True,
                        secure=config.AUTH_COOKIE_SECURE, samesite=config.AUTH_COOKIE_SAMESITE)
    response.delete_cookie("icongreen_login_csrf", path="/api/auth", secure=config.AUTH_COOKIE_SECURE,
                           httponly=True, samesite=config.AUTH_COOKIE_SAMESITE)
    response.headers["Cache-Control"] = "no-store"
    return {"success": True, "data": {"user": public_user(user), "csrf_token": csrf, "expires_at": expires}}


@router.get("/me", response_model=SessionResponse)
def me(request: Request, user: dict = Depends(require_session)):
    return {"success": True, "data": {"user": public_user(user),
            "csrf_token": csrf_token(request.cookies[config.AUTH_COOKIE_NAME]), "expires_at": user["expires_at"]}}


@router.post("/logout", response_model=LogoutResponse)
def logout(request: Request, response: Response, user: dict = Depends(require_session)):
    repository.revoke_session(token_hash(request.cookies[config.AUTH_COOKIE_NAME]))
    response.delete_cookie(config.AUTH_COOKIE_NAME, path="/", httponly=True,
                           secure=config.AUTH_COOKIE_SECURE, samesite=config.AUTH_COOKIE_SAMESITE)
    return {"success": True, "message": "Logout berhasil."}
