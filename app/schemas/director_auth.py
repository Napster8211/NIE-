from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DirectorIdentity(BaseModel):
    uid: str
    email: Optional[str] = None


class DirectorSessionResponse(BaseModel):
    authenticated: bool = True
    identity: DirectorIdentity
    session_id: str
    expires_at: datetime
    csrf_token: Optional[str] = Field(default=None, exclude=False)


class DirectorLogoutResponse(BaseModel):
    authenticated: bool = False


class DirectorRealtimeTicketResponse(BaseModel):
    ticket: str
    expires_at: datetime
    purpose: str = "director_realtime"


class DirectorPrincipal(BaseModel):
    owner_id: str
    owner_uid: str
    owner_email: Optional[str] = None
    auth_method: str
    session_id: Optional[str] = None
    session_token_hash: Optional[str] = None
    csrf_token_hash: Optional[str] = None
    expires_at: Optional[datetime] = None

