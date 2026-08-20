from fastapi import Request
from fastapi.responses import JSONResponse
import uuid

def generate_request_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"

def api_error_response(status_code: int, message: str, provider: str = "gateway", req_id: str = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": status_code,
                "message": message,
                "request_id": req_id or generate_request_id(),
                "provider": provider
            }
        }
    )