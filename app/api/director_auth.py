from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas.director_auth import (
    DirectorIdentity,
    DirectorLogoutResponse,
    DirectorPrincipal,
    DirectorRealtimeTicketResponse,
    DirectorSessionResponse,
)
from app.services.director_auth_service import (
    DIRECTOR_REALTIME_PURPOSE,
    DIRECTOR_SESSION_COOKIE,
    DirectorAuthError,
    DirectorAuthService,
    get_director_auth_service,
    require_browser_mutation,
    require_director_session,
    session_cookie_samesite,
    session_cookie_secure,
    session_ttl_seconds,
    validate_trusted_origin,
)


router = APIRouter(prefix="/director/auth", tags=["Director Authentication"])
identity_bearer = HTTPBearer(auto_error=False)


def _raise_http(error: DirectorAuthError) -> None:
    raise HTTPException(status_code=error.http_status, detail=error.code)


def _session_response(principal: DirectorPrincipal, csrf_token: Optional[str] = None) -> DirectorSessionResponse:
    return DirectorSessionResponse(
        identity=DirectorIdentity(uid=principal.owner_uid, email=principal.owner_email),
        session_id=principal.session_id or "",
        expires_at=principal.expires_at,
        csrf_token=csrf_token,
    )


@router.post("/session", response_model=DirectorSessionResponse)
async def create_director_session(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(identity_bearer),
    service: DirectorAuthService = Depends(get_director_auth_service),
):
    try:
        validate_trusted_origin(request)
        if credentials is None:
            raise DirectorAuthError("DIRECTOR_IDENTITY_REQUIRED", status.HTTP_401_UNAUTHORIZED)
        issued = await service.create_session(credentials.credentials)
        response.set_cookie(
            key=DIRECTOR_SESSION_COOKIE,
            value=issued.token,
            httponly=True,
            secure=session_cookie_secure(),
            samesite=session_cookie_samesite(),
            max_age=session_ttl_seconds(),
            path="/api/v1/director",
        )
        response.headers["Cache-Control"] = "no-store"
        return _session_response(issued.principal, issued.csrf_token)
    except DirectorAuthError as error:
        _raise_http(error)


@router.get("/session", response_model=DirectorSessionResponse)
async def inspect_director_session(
    principal: DirectorPrincipal = Depends(require_director_session),
):
    return _session_response(principal)


@router.delete("/session", response_model=DirectorLogoutResponse)
async def logout_director_session(
    response: Response,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    service: DirectorAuthService = Depends(get_director_auth_service),
):
    try:
        await service.revoke_session(principal)
        response.delete_cookie(
            key=DIRECTOR_SESSION_COOKIE,
            path="/api/v1/director",
            secure=session_cookie_secure(),
            samesite=session_cookie_samesite(),
        )
        response.headers["Cache-Control"] = "no-store"
        return DirectorLogoutResponse()
    except DirectorAuthError as error:
        _raise_http(error)


@router.post("/realtime-ticket", response_model=DirectorRealtimeTicketResponse)
async def create_realtime_ticket(
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    service: DirectorAuthService = Depends(get_director_auth_service),
):
    try:
        issued = await service.issue_realtime_ticket(principal)
        return DirectorRealtimeTicketResponse(
            ticket=issued.ticket,
            expires_at=issued.expires_at,
            purpose=DIRECTOR_REALTIME_PURPOSE,
        )
    except DirectorAuthError as error:
        _raise_http(error)
