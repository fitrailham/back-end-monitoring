from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128, examples=["admin"])
    password: SecretStr = Field(min_length=1, max_length=1024)


class PlantAccess(BaseModel):
    all_plants: bool
    plant_codes: list[str]


class AuthUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["admin", "viewer"]
    plant_access: PlantAccess


class SessionData(BaseModel):
    user: AuthUser
    csrf_token: str
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value):
        return value.astimezone(timezone.utc)


class SessionResponse(BaseModel):
    success: Literal[True] = True
    data: SessionData


class LogoutResponse(BaseModel):
    success: Literal[True] = True
    message: str = "Logout berhasil."


class CsrfData(BaseModel):
    csrf_token: str


class CsrfResponse(BaseModel):
    success: Literal[True] = True
    data: CsrfData
