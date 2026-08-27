"""
NapsterTec AI - Realtime Streaming Voice Runtime Service
Module: app/services/director_realtime_voice_service.py
"""
import asyncio
import base64
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from fastapi import WebSocket, WebSocketDisconnect

from app.services.director_interaction_service import (
    SpeechChunkAccumulator,
    director_interaction_service,
)

logger = logging.getLogger(__name__)


class DirectorVoiceLatencyTrace:
    """Tracks latency across turn phases to identify bottlenecks."""
    def __init__(self, turn_id: str):
        self.turn_id = turn_id
        self.speech_end_ms: Optional[float] = None
        self.stt_final_ms: Optional[float] = None
        self.llm_first_token_ms: Optional[float] = None
        self.tts_first_audio_ms: Optional[float] = None
        self.playback_start_ms: Optional[float] = None
        self.turn_start_time = time.perf_counter()

    def mark_speech_end(self):
        self.speech_end_ms = (time.perf_counter() - self.turn_start_time) * 1000

    def mark_stt_final(self):
        self.stt_final_ms = (time.perf_counter() - self.turn_start_time) * 1000

    def mark_llm_first_token(self):
        self.llm_first_token_ms = (time.perf_counter() - self.turn_start_time) * 1000

    def mark_tts_first_audio(self):
        self.tts_first_audio_ms = (time.perf_counter() - self.turn_start_time) * 1000

    def log_summary(self):
        total = (time.perf_counter() - self.turn_start_time) * 1000
        perceived = (self.tts_first_audio_ms - self.speech_end_ms) if (self.tts_first_audio_ms and self.speech_end_ms) else total
        logger.info(
            "[DirectorVoiceLatency] turn=%s vad=%.0fms stt_final=%.0fms llm_first_token=%.0fms tts_first_audio=%.0fms perceived_response=%.0fms total=%.0fms",
            self.turn_id,
            self.speech_end_ms or 0,
            self.stt_final_ms or 0,
            self.llm_first_token_ms or 0,
            self.tts_first_audio_ms or 0,
            perceived,
            total,
        )


class DirectorVoiceSession:
    def __init__(self, session_id: str, owner_id: str, websocket: WebSocket):
        self.session_id = session_id
        self.owner_id = owner_id
        self.websocket = websocket
        self.status = "LISTENING"
        self.current_turn_id: Optional[str] = None
        self.current_trace: Optional[DirectorVoiceLatencyTrace] = None
        self.active_tasks: list[asyncio.Task] = []

    def cancel_active_turn(self):
        """Barge-in cancellation: cleanly halts active LLM and TTS tasks."""
        self.status = "INTERRUPTED"
        for task in self.active_tasks:
            if not task.done():
                task.cancel()
        self.active_tasks.clear()


class DirectorRealtimeVoiceService:
    def __init__(self):
        self.sessions: Dict[str, DirectorVoiceSession] = {}

    async def handle_connection(self, websocket: WebSocket, owner_id: str):
        # CRITICAL FIX: We MUST pass the subprotocol back, otherwise the browser severs the connection!
        await websocket.accept(subprotocol=owner_id)
        
        session_id = f"vses_{uuid.uuid4().hex[:8]}"
        session = DirectorVoiceSession(session_id, owner_id, websocket)
        self.sessions[session_id] = session

        await websocket.send_json({
            "type": "session.ready",
            "session_id": session_id,
            "status": session.status,
            "timestamp": time.time(),
        })

        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                msg_type = message.get("type")

                if msg_type == "audio.input.commit":
                    transcript = message.get("transcript", "").strip()
                    if transcript:
                        session.cancel_active_turn()
                        turn_id = f"trn_{uuid.uuid4().hex[:6]}"
                        session.current_turn_id = turn_id
                        session.current_trace = DirectorVoiceLatencyTrace(turn_id)
                        session.current_trace.mark_speech_end()
                        session.current_trace.mark_stt_final()
                        session.status = "PROCESSING"

                        await websocket.send_json({
                            "type": "session.state",
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "status": "PROCESSING",
                        })

                        task = asyncio.create_task(
                            self._execute_streaming_turn(session, turn_id, transcript)
                        )
                        session.active_tasks.append(task)

                elif msg_type == "turn.interrupted":
                    session.cancel_active_turn()
                    session.status = "LISTENING"
                    await websocket.send_json({
                        "type": "session.state",
                        "session_id": session_id,
                        "status": "LISTENING",
                    })

                elif msg_type == "heartbeat":
                    await websocket.send_json({"type": "heartbeat.ack", "timestamp": time.time()})

        except WebSocketDisconnect:
            session.cancel_active_turn()
            self.sessions.pop(session_id, None)
            logger.info("[DirectorRealtimeVoice] Session disconnected: %s", session_id)
        except Exception as exc:
            logger.error("[DirectorRealtimeVoice] WebSocket error: %s", exc, exc_info=True)
            session.cancel_active_turn()
            self.sessions.pop(session_id, None)

    async def _execute_streaming_turn(self, session: DirectorVoiceSession, turn_id: str, transcript: str):
        trace = session.current_trace
        accumulator = SpeechChunkAccumulator(max_chars=120)
        first_token_marked = False
        full_text = []

        async def send_proposal(proposal):
            try:
                await session.websocket.send_json({
                    "type": "director.proposal",
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "proposal": proposal.model_dump(),
                })
            except Exception:
                pass

        try:
            # Stream LLM tokens using your custom Capability Router
            async for token in director_interaction_service.stream_interaction(
                user_message=transcript,
                conversation_id=session.session_id,
                on_proposal=send_proposal,
            ):
                if session.status == "INTERRUPTED":
                    break

                if not first_token_marked and trace:
                    trace.mark_llm_first_token()
                    first_token_marked = True

                full_text.append(token)
                await session.websocket.send_json({
                    "type": "director.text.delta",
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "delta": token,
                })

                # Buffer tokens into spoken sentence chunks for fluid TTS
                chunks = accumulator.add_token(token)
                for chunk in chunks:
                    await self._synthesize_and_stream_chunk(session, turn_id, chunk)

            # Flush any remaining text
            rem_chunk = accumulator.flush()
            if rem_chunk and session.status != "INTERRUPTED":
                await self._synthesize_and_stream_chunk(session, turn_id, rem_chunk)

            if session.status != "INTERRUPTED":
                await session.websocket.send_json({
                    "type": "director.text.final",
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "text": "".join(full_text),
                })
                if trace:
                    trace.log_summary()

        except asyncio.CancelledError:
            logger.info("[DirectorRealtimeVoice] Turn %s cancelled (barge-in)", turn_id)
        except Exception as exc:
            logger.error("[DirectorRealtimeVoice] Error in turn %s: %s", turn_id, exc, exc_info=True)
            await session.websocket.send_json({
                "type": "error",
                "session_id": session.session_id,
                "detail": "STREAMING_ERROR",
            })

    async def _synthesize_and_stream_chunk(self, session: DirectorVoiceSession, turn_id: str, text: str):
        api_key = os.getenv("ELEVENLABS_API_KEY")
        voice_id = os.getenv("ELEVENLABS_VOICE_ID")
        model_id = os.getenv("ELEVENLABS_LIVE_TTS_MODEL_ID", "eleven_turbo_v2_5")

        if not api_key or not voice_id or not text.strip():
            return

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        }
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "optimize_streaming_latency": 3,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code == 200 and response.content:
                    if session.current_trace and session.current_trace.tts_first_audio_ms is None:
                        session.current_trace.mark_tts_first_audio()

                    session.status = "SPEAKING"
                    b64_audio = base64.b64encode(response.content).decode("utf-8")
                    await session.websocket.send_json({
                        "type": "audio.output.chunk",
                        "session_id": session.session_id,
                        "turn_id": turn_id,
                        "audio_base64": b64_audio,
                        "text": text,
                    })
        except Exception as e:
            logger.warning("[DirectorRealtimeVoice] Chunk TTS failed: %s", e)


director_realtime_voice_service = DirectorRealtimeVoiceService()