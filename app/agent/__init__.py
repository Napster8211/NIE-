"""
NapsterTec AI Operating System - Agent Module & SDK
"""

# --- Existing Core Agent Loop Exports ---
from app.agent.execution_loop import AutonomousAgentLoop

# --- Sprint 1 Agent SDK Exports ---
from app.agent.agent_models import (
    AgentCapability,
    AgentPermission,
    ExecutionMode,
    AgentMetadata,
    ExecutionBudget,
    AgentContext,
    AgentResult,
)
from app.agent.base_agent import BaseAgent
from app.agent.agent_registry import AgentRegistry, agent_registry

__all__ = [
    # Backward Compatible Core
    "AutonomousAgentLoop",
    
    # Agent SDK Foundation
    "AgentCapability",
    "AgentPermission",
    "ExecutionMode",
    "AgentMetadata",
    "ExecutionBudget",
    "AgentContext",
    "AgentResult",
    "BaseAgent",
    "AgentRegistry",
    "agent_registry",
]