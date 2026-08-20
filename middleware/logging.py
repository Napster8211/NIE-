import time
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from utils.config import settings

os.makedirs("logs", exist_ok=True)

# Replace basic logger with TimedRotatingFileHandler
log_handler = TimedRotatingFileHandler(
    filename="logs/gateway.log",
    when="midnight",
    interval=1,
    backupCount=settings.LOG_RETENTION_DAYS
)
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("gateway")
logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger.addHandler(log_handler)
logger.propagate = False

class GatewayLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        req_id = request.state.req_id if hasattr(request.state, "req_id") else "unknown"
        
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            status_code = 500
            raise e
        finally:
            latency = round((time.time() - start_time) * 1000, 2)
            logger.info(
                f"REQ:{req_id} | METHOD:{request.method} | PATH:{request.url.path} | "
                f"STATUS:{status_code} | LATENCY:{latency}ms"
            )
            
        return response