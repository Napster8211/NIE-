import json
import asyncio
from fastapi import APIRouter, HTTPException, Depends, WebSocket
from fastapi.responses import StreamingResponse, Response
from typing import List, Optional

from app.services.executive_state_service import ExecutiveStateService
from app.schemas.director_desktop import (
    DesktopBootstrapState, ExecutiveOverview, DirectorStatusView, 
    ObjectiveView, MissionView, DepartmentView, ApprovalView, FinancialSummaryView
)
from app.services.executive_event_service import executive_event_service

# --- SPRINT 6D IMPORTS ---
from app.services.authorization import verify_owner
from app.schemas.owner_controls import (
    OwnerApprovalDecisionRequest, 
    OwnerObjectiveControlRequest, 
    OwnerMissionControlRequest
)
from app.services.owner_control_service import owner_control_service

# --- SPRINT 6E.2 IMPORTS ---
from app.schemas.executive_briefing import ExecutiveBriefing
from app.services.executive_briefing_service import executive_briefing_service

# --- SPRINT 6E.3 IMPORTS ---
from app.schemas.director_voice import DirectorVoiceRequest
from app.services.director_voice_service import director_voice_service

# --- SPRINT 6F IMPORTS ---
from app.schemas.director_interaction import DirectorInteractionRequest, DirectorInteractionResponse
from app.services.director_interaction_service import director_interaction_service

# --- SPRINT 6G IMPORTS ---
from fastapi import UploadFile, File
from app.schemas.director_speech import DirectorTranscriptionResponse
from app.services.director_speech_service import director_speech_service

# --- SPRINT 6G.2 IMPORTS ---
from app.services.director_realtime_voice_service import director_realtime_voice_service

router = APIRouter(prefix="/director", tags=["Director Desktop"])

# Initialize the event subscriptions when the router is loaded
executive_event_service.initialize_subscriptions()

def get_state_service() -> ExecutiveStateService:
    return ExecutiveStateService()

# ==========================================
# PREVIOUS SPRINT ENDPOINTS (6A / 6B)
# ==========================================

@router.get("/desktop/bootstrap", response_model=DesktopBootstrapState)
async def get_bootstrap_state(service: ExecutiveStateService = Depends(get_state_service)):
    return service.get_bootstrap_state()

@router.get("/overview", response_model=ExecutiveOverview)
async def get_overview(service: ExecutiveStateService = Depends(get_state_service)):
    return service.get_overview()

@router.get("/status", response_model=DirectorStatusView)
async def get_status(service: ExecutiveStateService = Depends(get_state_service)):
    return service.get_director_status()

@router.get("/objectives", response_model=List[ObjectiveView])
async def list_objectives(service: ExecutiveStateService = Depends(get_state_service)):
    return service.list_objectives()

@router.get("/objectives/{objective_id}", response_model=ObjectiveView)
async def get_objective(objective_id: str, service: ExecutiveStateService = Depends(get_state_service)):
    obj = service.get_objective_detail(objective_id)
    if not obj:
        raise HTTPException(status_code=404, detail="OBJECTIVE_NOT_FOUND")
    return obj

@router.get("/missions", response_model=List[MissionView])
async def list_missions(limit: int = 25, service: ExecutiveStateService = Depends(get_state_service)):
    if limit > 100: limit = 100
    return service.list_missions(limit=limit)

@router.get("/departments", response_model=List[DepartmentView])
async def list_departments(service: ExecutiveStateService = Depends(get_state_service)):
    return service.list_departments()

@router.get("/approvals", response_model=List[ApprovalView])
async def list_approvals(service: ExecutiveStateService = Depends(get_state_service)):
    return service.list_pending_approvals()

@router.get("/finance", response_model=FinancialSummaryView)
async def get_financial_summary(service: ExecutiveStateService = Depends(get_state_service)):
    return service._get_finance_summary()


# ==========================================
# SPRINT 6C: LIVE EVENT STREAM
# ==========================================

@router.get("/events/stream")
async def stream_executive_events(
    last_event_id: Optional[str] = None
):
    queue = executive_event_service.subscribe(last_event_id)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"id: {event.event_id}\n"
                    yield f"event: {event.event_type}\n"
                    yield f"data: {event.model_dump_json()}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            executive_event_service.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==========================================
# SPRINT 6D: OWNER CONTROL SURFACE
# ==========================================

@router.post("/approvals/{approval_id}/resolve")
async def resolve_approval(approval_id: str, request: OwnerApprovalDecisionRequest, owner_id: str = Depends(verify_owner)):
    return await owner_control_service.resolve_approval(owner_id, approval_id, request)

@router.post("/objectives/{objective_id}/pause")
async def pause_objective(objective_id: str, request: OwnerObjectiveControlRequest, owner_id: str = Depends(verify_owner)):
    return await owner_control_service.pause_objective(owner_id, objective_id, request)

@router.post("/objectives/{objective_id}/resume")
async def resume_objective(objective_id: str, request: OwnerObjectiveControlRequest, owner_id: str = Depends(verify_owner)):
    return await owner_control_service.resume_objective(owner_id, objective_id, request)

@router.post("/objectives/{objective_id}/cancel")
async def cancel_objective(objective_id: str, request: OwnerObjectiveControlRequest, owner_id: str = Depends(verify_owner)):
    return await owner_control_service.cancel_objective(owner_id, objective_id, request)

@router.post("/missions/{mission_id}/pause")
async def pause_mission(mission_id: str, request: OwnerMissionControlRequest, owner_id: str = Depends(verify_owner)):
    return await owner_control_service.pause_mission(owner_id, mission_id, request)

@router.post("/missions/{mission_id}/resume")
async def resume_mission(mission_id: str, request: OwnerMissionControlRequest, owner_id: str = Depends(verify_owner)):
    return await owner_control_service.resume_mission(owner_id, mission_id, request)

@router.post("/missions/{mission_id}/cancel")
async def cancel_mission(mission_id: str, request: OwnerMissionControlRequest, owner_id: str = Depends(verify_owner)):
    return await owner_control_service.cancel_mission(owner_id, mission_id, request)


# ==========================================
# SPRINT 6E.2: EXECUTIVE BRIEFING ENGINE
# ==========================================

@router.get("/briefings/status", response_model=ExecutiveBriefing)
async def get_company_status_briefing():
    return executive_briefing_service.generate_company_status_briefing()

@router.get("/briefings/daily", response_model=ExecutiveBriefing)
async def get_daily_briefing():
    return executive_briefing_service.generate_daily_briefing()

@router.get("/briefings/objectives/{objective_id}", response_model=ExecutiveBriefing)
async def get_objective_briefing(objective_id: str):
    try:
        return executive_briefing_service.generate_objective_briefing(objective_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/briefings/departments/{department_id}", response_model=ExecutiveBriefing)
async def get_department_briefing(department_id: str):
    try:
        return executive_briefing_service.generate_department_briefing(department_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/briefings/finance", response_model=ExecutiveBriefing)
async def get_finance_briefing():
    return executive_briefing_service.generate_finance_briefing()


# ==========================================
# SPRINT 6E.3: DIRECTOR VOICE SYNTHESIS
# ==========================================

@router.post("/voice/speak")
async def speak_briefing(request: DirectorVoiceRequest, owner_id: str = Depends(verify_owner)):
    try:
        audio_bytes = await director_voice_service.generate_briefing_audio(request)
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )
    except ValueError as e:
        code = str(e)
        status_map = {
            "VOICE_NOT_CONFIGURED": 503,
            "INVALID_BRIEFING_TYPE": 422,
            "BRIEFING_GENERATION_FAILED": 500,
            "EMPTY_SPEECH_TEXT": 422,
            "SPEECH_TOO_LONG": 413,
            "VOICE_GATEWAY_UNAUTHORIZED": 502,
            "VOICE_GATEWAY_RATE_LIMITED": 429,
            "VOICE_GATEWAY_TIMEOUT": 504,
            "VOICE_GATEWAY_UNAVAILABLE": 503,
            "VOICE_GENERATION_FAILED": 502,
        }
        raise HTTPException(status_code=status_map.get(code, 400), detail=code)


# ==========================================
# SPRINT 6F: EXECUTIVE INTERACTION LAYER
# ==========================================

@router.post("/interact", response_model=DirectorInteractionResponse)
async def interact_with_director(
    request: DirectorInteractionRequest, 
    owner_id: str = Depends(verify_owner)
):
    return await director_interaction_service.process_interaction(request, owner_id)

# ==========================================
# SPRINT 6G: DIRECTOR BATCH SPEECH-TO-TEXT
# ==========================================

@router.post("/voice/transcribe", response_model=DirectorTranscriptionResponse)
async def transcribe_voice(
    file: UploadFile = File(...),
    owner_id: str = Depends(verify_owner),
):
    try:
        return await director_speech_service.transcribe(file)
    except ValueError as e:
        code = str(e)
        status_map = {
            "STT_NOT_CONFIGURED": 503,
            "EMPTY_AUDIO": 422,
            "EMPTY_TRANSCRIPT": 422,
            "AUDIO_TOO_LARGE": 413,
            "STT_INVALID_AUDIO": 422,
            "STT_UNAUTHORIZED": 502,
            "STT_PAYMENT_REQUIRED": 402,
            "STT_RATE_LIMITED": 429,
            "STT_TIMEOUT": 504,
            "STT_UNAVAILABLE": 503,
            "TRANSCRIPTION_FAILED": 502,
        }
        raise HTTPException(status_code=status_map.get(code, 400), detail=code)

# ==========================================
# SPRINT 6G.2: REALTIME STREAMING VOICE RUNTIME
# ==========================================

@router.websocket("/voice/realtime")
async def realtime_voice_session(websocket: WebSocket):
    """
    Full-duplex WebSocket establishing the Director Voice Session.
    Bypasses standard REST latency for continuous streaming interaction.
    """
    token = None
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        
    if not token and websocket.scope.get("subprotocols"):
        token = websocket.scope["subprotocols"][0]
        
    try:
        if not token:
            await websocket.close(code=1008, reason="UNAUTHORIZED")
            return
            
        owner_id = token 
        await director_realtime_voice_service.handle_connection(websocket, owner_id)
        
    except Exception as e:
        try:
            await websocket.close(code=1011, reason="INTERNAL_ERROR")
        except:
            pass
