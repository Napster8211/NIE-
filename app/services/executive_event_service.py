"""
NapsterTec AI - Executive Event Projection Service
Module: app/services/executive_event_service.py
"""
import asyncio
import logging
from collections import deque
from typing import Dict, Any, AsyncGenerator
from datetime import datetime, timezone

from app.engine.event_bus import event_bus, BusinessEvent
from app.schemas.executive_events import ExecutiveLiveEvent

logger = logging.getLogger(__name__)

# Bounded buffer to retain the last N events for SSE client reconnection/replay
MAX_EVENT_BUFFER_SIZE = 200

class ExecutiveEventService:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ExecutiveEventService, cls).__new__(cls)
            cls._instance._event_buffer = deque(maxlen=MAX_EVENT_BUFFER_SIZE)
            cls._instance._queues = []  # List of asyncio.Queue for active SSE subscribers
            cls._instance._is_subscribed = False
        return cls._instance

    def initialize_subscriptions(self):
        """Bind to the canonical internal event bus."""
        if self._is_subscribed:
            return
            
        # We subscribe to a catch-all or specific known events.
        # Currently binding to known mission engine emitted events.
        event_bus.subscribe("MISSION_DELEGATION_CREATED", self._handle_internal_event)
        event_bus.subscribe("MISSION_EXECUTION_REQUEST_READY", self._handle_internal_event)
        event_bus.subscribe("DEAL_WON", self._handle_internal_event)
        
        self._is_subscribed = True
        logger.info("[ExecutiveEventService] Subscribed to canonical Event Bus.")

    async def _handle_internal_event(self, event: BusinessEvent):
        """Safely project an internal BusinessEvent into a safe ExecutiveLiveEvent."""
        
        # Determine Severity
        severity = "INFO"
        if event.event_type == "DEAL_WON":
            severity = "SUCCESS"
        elif "FAILED" in event.event_type or "BLOCKED" in event.event_type:
            severity = "WARNING"
            
        # Extract Safe Correlation IDs
        metadata = event.execution_metadata or {}
        mission_id = metadata.get("mission_id") or (event.correlation_id if event.correlation_id.startswith("mis_") else None)
        
        # Create Safe Projection
        safe_event = ExecutiveLiveEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            timestamp=event.timestamp or datetime.now(timezone.utc).isoformat(),
            severity=severity,
            entity_type="MISSION", 
            entity_id=mission_id or "unknown",
            mission_id=mission_id,
            headline=event.event_type.replace("_", " ").title(),
            summary=event.evidence or "System event processed.",
            metadata_safe={"workflow_id": event.workflow_id} if event.workflow_id else {}
        )
        
        # Buffer and Dispatch
        self._event_buffer.append(safe_event)
        await self._dispatch_to_subscribers(safe_event)

    async def _dispatch_to_subscribers(self, safe_event: ExecutiveLiveEvent):
        """Push the projected event to all active SSE queues."""
        dead_queues = []
        for q in self._queues:
            try:
                # Use put_nowait to drop events if a client is frozen (Backpressure)
                q.put_nowait(safe_event)
            except asyncio.QueueFull:
                logger.warning("[ExecutiveEventService] Dropping event for slow subscriber.")
                dead_queues.append(q)
                
        # Clean up dead connections
        for dq in dead_queues:
            if dq in self._queues:
                self._queues.remove(dq)

    def subscribe(self, last_event_id: str = None) -> asyncio.Queue:
        """Register a new SSE client and optionally replay missed events."""
        q = asyncio.Queue(maxsize=100) # Local subscriber buffer
        
        # Replay logic if Last-Event-ID is provided
        if last_event_id:
            replay = False
            for evt in self._event_buffer:
                if replay:
                    try:
                        q.put_nowait(evt)
                    except asyncio.QueueFull:
                        break
                if evt.event_id == last_event_id:
                    replay = True
                    
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._queues:
            self._queues.remove(q)

# Global Singleton
executive_event_service = ExecutiveEventService()