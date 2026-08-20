"""
NapsterTec AI Operating System - Agent Registry
Module: app/agent/agent_registry.py
Description: Centralized registry for agent registration, discovery, and lookup
             by capability, category, task, or name.
"""

import logging
from typing import Dict, List, Optional, Set
from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentCapability

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Centralized discovery service for all intelligent agents in the NapsterTec OS.
    """

    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Registers a new agent instance into the platform ecosystem."""
        agent_name = agent.metadata.name
        if agent_name in self._agents:
            logger.warning(f"[AgentRegistry] Overwriting existing registered agent: {agent_name}")
        
        self._agents[agent_name] = agent
        logger.info(f"[AgentRegistry] Successfully registered agent: '{agent_name}' ({agent.metadata.display_name})")

    def unregister(self, name: str) -> bool:
        """Removes an agent from the ecosystem."""
        if name in self._agents:
            del self._agents[name]
            logger.info(f"[AgentRegistry] Unregistered agent: '{name}'")
            return True
        return False

    def get_agent(self, name: str) -> Optional[BaseAgent]:
        """Looks up an exact agent by its unique technical name."""
        return self._agents.get(name)

    def discover_by_capability(self, capability: AgentCapability | str) -> List[BaseAgent]:
        """Discovers all enabled agents providing a specific capability, ordered by priority."""
        cap_str = capability.value if isinstance(capability, AgentCapability) else str(capability)
        
        matching_agents = [
            agent for agent in self._agents.values()
            if cap_str in [c if isinstance(c, str) else c.value for c in agent.metadata.capabilities]
        ]
        
        matching_agents.sort(key=lambda a: a.metadata.priority, reverse=True)
        return matching_agents

    def discover_by_task(self, task_type: str) -> List[BaseAgent]:
        """Discovers agents capable of satisfying a specific task intent string."""
        clean_task = task_type.lower().strip()
        matching_agents = [
            agent for agent in self._agents.values()
            if any(clean_task in t.lower() for t in agent.metadata.supported_task_types)
        ]
        matching_agents.sort(key=lambda a: a.metadata.priority, reverse=True)
        return matching_agents

    def discover_by_category(self, category: str) -> List[BaseAgent]:
        """Discovers all agents under a specific operational domain category."""
        clean_cat = category.lower().strip()
        matching_agents = [
            agent for agent in self._agents.values()
            if agent.metadata.category.lower() == clean_cat
        ]
        matching_agents.sort(key=lambda a: a.metadata.priority, reverse=True)
        return matching_agents

    def list_all_metadata(self) -> List[AgentMetadata]:
        """Returns metadata listings for all registered platform agents."""
        return [agent.metadata for agent in self._agents.values()]


# Global Singleton Registry Instance
agent_registry = AgentRegistry()