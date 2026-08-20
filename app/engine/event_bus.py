"""
NapsterTec AI - Enterprise Event Bus
Module: app/engine/event_bus.py
"""
import asyncio
import logging
from typing import Callable, Dict, List, Any
from pydantic import BaseModel, Field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class BusinessEvent(BaseModel):
    event_id: str
    event_type: str
    timestamp: str
    lead_id: str
    business_name: str
    communication_id: str
    conversation_id: str
    correlation_id: str
    workflow_id: str
    channel: str
    evidence: str
    confidence: float
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class EnterpriseEventBus:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EnterpriseEventBus, cls).__new__(cls)
            cls._instance.subscribers = {}
        return cls._instance

    def subscribe(self, event_type: str, callback: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)
        logger.info(f"[Enterprise Event Bus] Subscriber registered for event: {event_type}")

    async def publish(self, event: BusinessEvent):
        logger.info(f"[Enterprise Event Bus] Publishing '{event.event_type}' for {event.business_name}")
        if event.event_type in self.subscribers:
            for callback in self.subscribers[event.event_type]:
                asyncio.create_task(self._safe_execute(callback, event))

    async def publish_and_wait(self, event: BusinessEvent) -> List[Any]:
        """Deliver a control-plane event synchronously and fail closed.

        The legacy ``publish`` path intentionally remains fire-and-forget. Mission
        terminal events use this bounded path so a caller cannot report successful
        Director evaluation before every subscribed handler has completed.
        """
        logger.info(
            f"[Enterprise Event Bus] Publishing synchronously "
            f"'{event.event_type}' for {event.business_name}"
        )
        results: List[Any] = []
        for callback in self.subscribers.get(event.event_type, []):
            results.append(await callback(event))
        return results

    async def _safe_execute(self, callback: Callable, event: BusinessEvent):
        try:
            await callback(event)
        except Exception as e:
            logger.error(f"[Enterprise Event Bus] Subscriber execution failed: {e}")

# Global Singleton
event_bus = EnterpriseEventBus()

# --- Enterprise Subscriptions (Automatic Reactions) ---
async def sales_intelligence_reaction(event: BusinessEvent):
    logger.info(f"[Sales Intelligence Subscriber] Reaction triggered by '{event.event_type}'. Increasing Buying Intent for {event.business_name}.")

async def revenue_intelligence_reaction(event: BusinessEvent):
    logger.info(f"[Revenue Intelligence Subscriber] Reaction triggered by '{event.event_type}'. Updating pipeline forecast for {event.business_name}.")

async def crm_timeline_reaction(event: BusinessEvent):
    logger.info(f"[CRM Intelligence Subscriber] Chronological timeline appended for {event.business_name}: {event.evidence}")

async def business_operations_reaction(event: BusinessEvent):
    logger.info(f"[Business Ops (COO) Subscriber] OS Event logged for operational KPI tracking: {event.event_type}")

async def finance_intelligence_reaction(event: BusinessEvent):
    logger.info(f"[Finance (CFO) Subscriber] Financial state updated via event: {event.event_type}")

async def director_intelligence_reaction(event: BusinessEvent):
    if event.event_type == "MISSION_TERMINAL":
        from app.services.post_mission_evaluation import (
            post_mission_evaluation_coordinator,
        )

        return await post_mission_evaluation_coordinator.handle_business_event(event)
    logger.info(f"[Director (CEO) Subscriber] Executive alert logged for event: {event.event_type}")

async def agent_session_reaction(event: BusinessEvent):
    logger.info(f"[Session Manager] Lifecycle Event: {event.event_type} - {event.evidence}")

async def mission_engine_reaction(event: BusinessEvent):
    logger.info(f"[Mission Engine] Event processed for active mission correlation: {event.event_type}")

# Registering OS-wide reactions
event_bus.subscribe("proposal_opened", sales_intelligence_reaction)
event_bus.subscribe("proposal_opened", crm_timeline_reaction)
event_bus.subscribe("proposal_opened", business_operations_reaction)
event_bus.subscribe("proposal_opened", finance_intelligence_reaction)

event_bus.subscribe("demo_viewed", sales_intelligence_reaction)
event_bus.subscribe("demo_viewed", revenue_intelligence_reaction)
event_bus.subscribe("demo_viewed", crm_timeline_reaction)
event_bus.subscribe("demo_viewed", business_operations_reaction)
event_bus.subscribe("demo_viewed", finance_intelligence_reaction)

# Director specific critical events
event_bus.subscribe("critical_operational_risk", director_intelligence_reaction)
event_bus.subscribe("approval_required", director_intelligence_reaction)
event_bus.subscribe("MISSION_TERMINAL", director_intelligence_reaction)

# Agent Session events
event_bus.subscribe("AGENT_SESSION_CREATED", agent_session_reaction)
event_bus.subscribe("AGENT_SESSION_ACTIVATED", agent_session_reaction)
event_bus.subscribe("AGENT_SESSION_SUSPENDED", agent_session_reaction)
event_bus.subscribe("AGENT_SESSION_RESUMED", agent_session_reaction)

# Mission Engine Event Listeners
event_bus.subscribe("DEAL_WON", mission_engine_reaction)
event_bus.subscribe("DEPLOYMENT_COMPLETED", mission_engine_reaction)
event_bus.subscribe("MISSION_EXECUTION_BOOTSTRAPPED", mission_engine_reaction)
