import sys
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from utils.config import settings
from utils.response import api_error_response, generate_request_id
from utils.health import get_uptime
from utils.schemas import ChatCompletionRequest
from middleware.auth import verify_api_key
from middleware.logging import GatewayLoggingMiddleware, logger
from providers.provider_manager import ProviderManager
from providers.registry import register_all_providers

app = FastAPI(
    title="NapsterTec AI Gateway",
    version="2.0.0",
    description="Enterprise OpenAI-compatible Multi-Provider AI Gateway"
)

# CORS Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
app.add_middleware(GatewayLoggingMiddleware)

# Application Global State
global_executor: ThreadPoolExecutor = None
provider_manager = ProviderManager()

@app.on_event("startup")
async def startup_event():
    global global_executor
    # Create reusable ThreadPoolExecutor sized from settings
    global_executor = ThreadPoolExecutor(max_workers=settings.THREAD_POOL_SIZE)
    
    # Register all providers and model mappings via registry
    register_all_providers(provider_manager, global_executor)
    await provider_manager.initialize_all()
    
    logger.info(f"NapsterTec AI Gateway v2.0 initialized with {settings.THREAD_POOL_SIZE} worker threads.")

@app.on_event("shutdown")
async def shutdown_event():
    global global_executor
    if global_executor:
        global_executor.shutdown(wait=False)
        logger.info("Global ThreadPoolExecutor shut down successfully.")

@app.middleware("http")
async def add_request_id_and_limits(request: Request, call_next):
    request.state.req_id = generate_request_id()
    
    # Enforce Payload Size Limit
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.MAX_REQUEST_SIZE:
        return api_error_response(413, f"Payload exceeds maximum allowed size of {settings.MAX_REQUEST_SIZE} bytes.", req_id=request.state.req_id)
        
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.req_id
        return response
    except Exception as e:
        logger.error(f"REQ:{request.state.req_id} | Unhandled Exception: {str(e)}")
        return api_error_response(500, "Internal Gateway Error", req_id=request.state.req_id)

# ==================== PUBLIC METRICS & INFO ENDPOINTS ====================

@app.get("/health")
async def health_check():
    health_data = await provider_manager.check_health()
    overall = "healthy" if any(p.get("healthy") for p in health_data.values()) else "unhealthy"
    return {
        "status": overall,
        "uptime_seconds": round(get_uptime(), 2),
        "providers": health_data
    }

@app.get("/info")
async def gateway_info():
    return {
        "name": "NapsterTec AI Gateway",
        "version": "2.0.0",
        "python_version": sys.version.split()[0],
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": round(get_uptime(), 2),
        "default_provider": settings.DEFAULT_PROVIDER,
        "default_model": settings.DEFAULT_MODEL,
        "thread_pool_size": settings.THREAD_POOL_SIZE
    }

@app.get("/providers", dependencies=[Depends(verify_api_key)])
async def list_providers():
    return {"providers": provider_manager.providers}

@app.get("/metrics", dependencies=[Depends(verify_api_key)])
async def get_admin_metrics():
    return {
        "uptime_seconds": round(get_uptime(), 2),
        "metrics": provider_manager.get_metrics()
    }

# ==================== OPENAI COMPATIBLE ROUTES ====================

@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models(request: Request):
    req_id = request.state.req_id
    try:
        provider = provider_manager.get_ordered_providers()[0][1]
        models = await provider.list_models()
        return {"object": "list", "data": models}
    except Exception as e:
        logger.error(f"REQ:{req_id} | Model List Error: {str(e)}")
        return api_error_response(502, str(e), req_id=req_id)

@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(request: Request, payload: ChatCompletionRequest):
    req_id = request.state.req_id

    # Enforce max message limit
    if len(payload.messages) > settings.MAX_MESSAGE_COUNT:
        return api_error_response(400, f"Context length exceeds maximum of {settings.MAX_MESSAGE_COUNT} messages.", req_id=req_id)

    raw_messages = [msg.dict() for msg in payload.messages]
    extra_params = payload.dict(exclude={"messages", "model", "provider", "stream"})

    logger.info(f"REQ:{req_id} | Target Model: {payload.model} | Stream: {payload.stream}")

    try:
        response, serving_provider = await provider_manager.execute_with_failover(
            messages=raw_messages,
            requested_model=payload.model,
            stream=payload.stream,
            **extra_params
        )

        if payload.stream:
            return StreamingResponse(response, media_type="text/event-stream")
        else:
            response["serving_provider"] = serving_provider
            return response

    except Exception as e:
        logger.error(f"REQ:{req_id} | Routing Failure: {str(e)}")
        return api_error_response(502, f"Provider Error: {str(e)}", req_id=req_id)