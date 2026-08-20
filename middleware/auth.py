from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from utils.config import settings
from utils.response import api_error_response

security = HTTPBearer(auto_error=False)

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not settings.API_KEY:
        return True # Auth disabled for local dev if no key is set

    if not credentials or credentials.scheme != "Bearer":
        raise HTTPException(status_code=401, detail="Invalid authentication scheme")
    
    if credentials.credentials != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    
    return True