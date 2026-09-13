"""Authoritative browser authorization for Standard Chat engineering tools."""

import os
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.director_auth_service import (
    DIRECTOR_SESSION_COOKIE,
    DirectorAuthError,
    DirectorAuthService,
    get_director_auth_service,
    validate_trusted_origin,
    verify_firebase_identity,
)

_optional_bearer = HTTPBearer(auto_error=False)


def _bool_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def engineering_mode_enabled() -> bool:
    environment = (os.getenv("NIE_ENV") or os.getenv("ENVIRONMENT") or "development").strip().casefold()
    return _bool_setting("NIE_ENGINEERING_MODE_ENABLED", environment not in {"production", "prod"})


def engineering_owner_only() -> bool:
    return _bool_setting("NIE_ENGINEERING_OWNER_ONLY", True)


def _uid_set(name: str) -> set[str]:
    return {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}


@dataclass(frozen=True)
class EngineeringPrincipal:
    user_id: str
    firebase_uid: str
    email: str | None
    auth_method: str
    is_owner: bool
    director_session_id: str | None = None


class EngineeringAuthorizationError(Exception):
    def __init__(self, code: str, http_status: int):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def authorize_engineering_identity(
    uid: str,
    email: str | None,
    auth_method: str,
    session_id: str | None = None,
) -> EngineeringPrincipal:
    if not engineering_mode_enabled():
        raise EngineeringAuthorizationError("ENGINEERING_MODE_DISABLED", status.HTTP_403_FORBIDDEN)

    owner_uids = _uid_set("NIE_OWNER_FIREBASE_UIDS")
    is_owner = uid in owner_uids
    if engineering_owner_only() and not is_owner:
        raise EngineeringAuthorizationError("ENGINEERING_OWNER_ONLY", status.HTTP_403_FORBIDDEN)

    allowed_uids = _uid_set("NIE_ENGINEERING_ALLOWED_FIREBASE_UIDS")
    if not engineering_owner_only() and allowed_uids and uid not in allowed_uids and not is_owner:
        raise EngineeringAuthorizationError("ENGINEERING_USER_NOT_ALLOWED", status.HTTP_403_FORBIDDEN)

    return EngineeringPrincipal(
        user_id=f"firebase:{uid}",
        firebase_uid=uid,
        email=email,
        auth_method=auth_method,
        is_owner=is_owner,
        director_session_id=session_id,
    )


def _raise_http(error: Exception) -> None:
    if isinstance(error, EngineeringAuthorizationError):
        raise HTTPException(status_code=error.http_status, detail=error.code)
    if isinstance(error, DirectorAuthError):
        raise HTTPException(status_code=error.http_status, detail=error.code)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ENGINEERING_AUTH_REQUIRED")


async def require_engineering_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    director_auth: DirectorAuthService = Depends(get_director_auth_service),
) -> EngineeringPrincipal:
    """Resolve workspace identity only from server-verified credentials."""
    try:
        validate_trusted_origin(request)
        if credentials is not None:
            identity = await verify_firebase_identity(
                credentials.credentials,
                error_prefix="ENGINEERING",
            )
            return authorize_engineering_identity(identity.uid, identity.email, "firebase_bearer")

        director_token = request.cookies.get(DIRECTOR_SESSION_COOKIE, "")
        if director_token:
            principal = await director_auth.validate_session(director_token)
            return authorize_engineering_identity(
                principal.owner_uid,
                principal.owner_email,
                "director_session",
                principal.session_id,
            )
    except (EngineeringAuthorizationError, DirectorAuthError) as error:
        _raise_http(error)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ENGINEERING_AUTH_REQUIRED")


async def require_engineering_mutation(
    request: Request,
    principal: EngineeringPrincipal = Depends(require_engineering_access),
    director_auth: DirectorAuthService = Depends(get_director_auth_service),
) -> EngineeringPrincipal:
    # Firebase bearer credentials are explicit request credentials, not ambient
    # cookies. Director-cookie compatibility retains its existing CSRF binding.
    if principal.auth_method == "director_session":
        try:
            director_principal = await director_auth.validate_session(request.cookies.get(DIRECTOR_SESSION_COOKIE, ""))
            director_auth.validate_csrf(
                director_principal,
                request.headers.get("x-csrf-token", ""),
            )
        except DirectorAuthError as error:
            _raise_http(error)
    return principal
