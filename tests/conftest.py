from datetime import timedelta

import pytest

from app.auth import csrf_token
from app.repositories import auth_repository
from app.security import token_hash
from app.services.values import utc_now


@pytest.fixture
def authenticated_admin(monkeypatch):
    token = "test-session-token"
    csrf = csrf_token(token)
    user = {"id": "00000000-0000-0000-0000-000000000001", "username": "admin",
            "display_name": "Test admin", "role": "admin", "plant_codes": [],
            "csrf_hash": token_hash(csrf), "expires_at": utc_now() + timedelta(hours=8)}
    monkeypatch.setattr(auth_repository, "find_session", lambda hashed: user if hashed == token_hash(token) else None)
    return {"cookies": {"icongreen_session": token}, "headers": {"X-CSRF-Token": csrf}}
