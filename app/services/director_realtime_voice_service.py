"""
NapsterTec AI - Realtime Streaming Voice Runtime Service
Module: app/services/director_realtime_voice_service.py
"""
import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.services.director_interaction_service import (
    SpeechChunkAccumulator,
    director_interaction_service,
)
from app.services.director_voice_service import director_voice_service

logger = logging.getLogger(__name__)


class DirectorVoiceLatencyTrace:
    """Tracks granular latency across turn phases without exposing secrets or transcripts."""
    def __init__(self, turn_id: str, correlation_id: Optional[str] = None):
        self.turn_id = turn_id
        self.correlation_id = correlation_id or turn_id
        self.turn_received_time = time.perf_counter()
        
        self.llm_start_time: Optional[float] = None
        self.llm_first_token_time: Optional[float] = None
        self.first_tts_enqueued_time: Optional[float] = None
        self.tts_request_start_time: Optional[float] = None
        self.tts_first_audio_time: Optional[float] = None
        self.first_audio_sent_time: Optional[float] = None
        self.llm_complete_time: Optional[float] = None
        self.tts_complete_time: Optional[float] = None
        self.turn_complete_time: Optional[float] = None

    def mark_llm_start(self):
        self.llm_start_time = time.perf_counter()

    def mark_llm_first_token(self):
        if self.llm_first_token_time is None:
            self.llm_first_token_time = time.perf_counter()

    def mark_first_tts_enqueued(self):
        if self.first_tts_enqueued_time is None:
            self.first_tts_enqueued_time = time.perf_counter()

    def mark_tts_request_start(self):
        if self.tts_request_start_time is None:
            self.tts_request_start_time = time.perf_counter()

    def mark_tts_first_audio(self):
        if self.tts_first_audio_time is None:
            self.tts_first_audio_time = time.perf_counter()

    def mark_first_audio_sent(self):
        if self.first_audio_sent_time is None:
            self.first_audio_sent_time = time.perf_counter()

    def mark_llm_complete(self):
        self.llm_complete_time = time.perf_counter()

    def mark_tts_complete(self):
        self.tts_complete_time = time.perf_counter()

    def mark_turn_complete(self):
        self.turn_complete_time = time.perf_counter()

    def log_summary(self):
        now = self.turn_complete_time or time.perf_counter()
        total_turn_ms = (now - self.turn_received_time) * 1000

        backend_to_llm_first_token_ms = (
            (self.llm_first_token_time - self.turn_received_time) * 1000
            if self.llm_first_token_time
            else 0.0
        )
        llm_first_token_to_tts_request_ms = (
            (self.tts_request_start_time - self.llm_first_token_time) * 1000
            if self.tts_request_start_time and self.llm_first_token_time
            else 0.0
        )
        tts_request_to_first_audio_ms = (
            (self.tts_first_audio_time - self.tts_request_start_time) * 1000
            if self.tts_first_audio_time and self.tts_request_start_time
            else 0.0
        )
        backend_to_first_audio_ms = (
            (self.first_audio_sent_time - self.turn_received_time) * 1000
            if self.first_audio_sent_time
            else 0.0
        )

        logger.info(
            "[DirectorVoiceLatency] turn=%s transcript_received=0ms llm_first_token=%.0fms "
            "tts_first_audio=%.0fms first_audio_sent=%.0fms backend_to_first_audio=%.0fms "
            "llm_to_tts_req=%.0fms tts_req_to_audio=%.0fms total=%.0fms",
            self.correlation_id,
            backend_to_llm_first_token_ms,
            (self.tts_first_audio_time - self.turn_received_time) * 1000 if self.tts_first_audio_time else 0.0,
            backend_to_first_audio_ms,
            backend_to_first_audio_ms,
            llm_first_token_to_tts_request_ms,
            tts_request_to_first_audio_ms,
            total_turn_ms,
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
        self.tts_queue: Optional[asyncio.Queue] = None

    def cancel_active_turn(self):
        """Barge-in cancellation: cleanly halts active LLM and TTS tasks."""
        self.status = "INTERRUPTED"
        for task in self.active_tasks:
            if not task.done():
                task.cancel()
        self.active_tasks.clear()
        self.tts_queue = None


class DirectorRealtimeVoiceService:
    def __init__(self):
        self.sessions: Dict[str, DirectorVoiceSession] = {}

    async def handle_connection(self, websocket: WebSocket, owner_id: str, already_accepted: bool = False):
        if not already_accepted:
            await websocket.accept(subprotocol="nie-director-v1")

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
                        client_session_id = self._safe_correlation_id(
                            message.get("client_session_id")
                        )
                        session.current_turn_id = turn_id
                        session.current_trace = DirectorVoiceLatencyTrace(
                            turn_id,
                            client_session_id,
                        )
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
        if trace:
            trace.mark_llm_start()

        # Bounded asynchronous queue to decouple LLM token streaming from TTS synthesis
        tts_queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=5)
        session.tts_queue = tts_queue

        tts_worker_task = asyncio.create_task(
            self._tts_consumer_worker(session, turn_id, tts_queue)
        )
        session.active_tasks.append(tts_worker_task)

        accumulator = SpeechChunkAccumulator(
            first_chunk_min_chars=20,
            first_chunk_max_chars=48,
            max_chars=110,
        )
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
            # LLM Producer: streams tokens without blocking on TTS HTTP responses
            async for token in director_interaction_service.stream_interaction(
                user_message=transcript,
                conversation_id=session.session_id,
                on_proposal=send_proposal,
                correlation_id=trace.correlation_id if trace else turn_id,
            ):
                if session.status == "INTERRUPTED" or session.current_turn_id != turn_id:
                    break

                if trace:
                    trace.mark_llm_first_token()

                full_text.append(token)
                await session.websocket.send_json({
                    "type": "director.text.delta",
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "delta": token,
                })

                chunks = accumulator.add_token(token)
                for chunk in chunks:
                    if trace:
                        trace.mark_first_tts_enqueued()
                    await tts_queue.put(chunk)

            # Flush accumulator tail
            rem_chunk = accumulator.flush()
            if rem_chunk and session.status != "INTERRUPTED" and session.current_turn_id == turn_id:
                if trace:
                    trace.mark_first_tts_enqueued()
                await tts_queue.put(rem_chunk)

            if trace:
                trace.mark_llm_complete()

            # Sentinel to notify TTS consumer that LLM output is complete
            await tts_queue.put(None)
            await tts_worker_task

            if session.status != "INTERRUPTED" and session.current_turn_id == turn_id:
                await session.websocket.send_json({
                    "type": "director.text.final",
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "text": "".join(full_text),
                })
                if trace:
                    trace.mark_turn_complete()
                    trace.log_summary()

        except asyncio.CancelledError:
            logger.info("[DirectorRealtimeVoice] Turn %s cancelled by barge-in", turn_id)
        except Exception as exc:
            logger.error("[DirectorRealtimeVoice] Turn execution error: %s", exc, exc_info=True)
            await session.websocket.send_json({
                "type": "error",
                "session_id": session.session_id,
                "detail": "STREAMING_ERROR",
            })

    async def _tts_consumer_worker(
        self,
        session: DirectorVoiceSession,
        turn_id: str,
        queue: asyncio.Queue[Optional[str]],
    ):
        """Sequential TTS worker consuming speech chunks in guaranteed order."""
        try:
            while True:
                chunk_text = await queue.get()
                if chunk_text is None:
                    queue.task_done()
                    break

                if session.status == "INTERRUPTED" or session.current_turn_id != turn_id:
                    queue.task_done()
                    break

                await self._stream_synthesize_chunk(session, turn_id, chunk_text)
                queue.task_done()

            if session.current_trace:
                session.current_trace.mark_tts_complete()

        except asyncio.CancelledError:
            pass

    async def _stream_synthesize_chunk(
        self,
        session: DirectorVoiceSession,
        turn_id: str,
        text: str,
    ):
        """Synthesize one speech chunk as WAV through the internal Piper gateway."""
        if not text.strip():
            return

        trace = session.current_trace
        if trace:
            trace.mark_tts_request_start()

        piper_started_at = time.perf_counter()
        try:
            audio = await director_voice_service.synthesize_text(text)
        except ValueError as exc:
            logger.warning(
                "[DirectorRealtimeVoice] Voice gateway synthesis skipped code=%s",
                str(exc),
            )
            return
        except Exception:
            logger.warning("[DirectorRealtimeVoice] Voice gateway synthesis failed")
            return

        # A completed request from an interrupted or superseded turn is discarded.
        if session.status == "INTERRUPTED" or session.current_turn_id != turn_id:
            return
        if not audio.audio_bytes:
            return

        logger.info(
            "[TTS][%s] piper_request_ms=%.0f audio_bytes=%d",
            trace.correlation_id if trace else turn_id,
            (time.perf_counter() - piper_started_at) * 1000,
            len(audio.audio_bytes),
        )

        if trace:
            trace.mark_tts_first_audio()
            trace.mark_first_audio_sent()

        session.status = "SPEAKING"
        b64_audio = base64.b64encode(audio.audio_bytes).decode("utf-8")
        await session.websocket.send_json({
            "type": "audio.output.chunk",
            "session_id": session.session_id,
            "turn_id": turn_id,
            "audio_base64": b64_audio,
            "audio_format": audio.audio_format,
            "sample_rate": audio.sample_rate,
            "channels": audio.channels,
            "text": text,
        })

    @staticmethod
    def _safe_correlation_id(value: Any) -> Optional[str]:
        candidate = str(value or "").strip()
        if candidate and len(candidate) <= 64 and all(
            character.isalnum() or character in {"_", "-"}
            for character in candidate
        ):
            return candidate
        return None


director_realtime_voice_service = DirectorRealtimeVoiceService()
