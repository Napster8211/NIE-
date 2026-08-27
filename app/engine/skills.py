from abc import ABC, abstractmethod
from typing import List, Dict
import logging

from app.engine.models import Capability, Intent

logger = logging.getLogger(__name__)


class Skill(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def required_capabilities(self) -> List[Capability]:
        """Strict capabilities required from a provider to execute this skill."""
        pass

    @property
    @abstractmethod
    def provider_preferences(self) -> List[str]:
        """Preferred providers in priority order. Router uses this to break ties and execute failover."""
        pass

    @abstractmethod
    async def format_execution_prompt(self, intent: Intent) -> str:
        """Transforms raw prompt into an agent-level prompt context."""
        pass


class FullStackArchitectSkill(Skill):
    @property
    def name(self) -> str:
        return "FullStackArchitect"

    @property
    def required_capabilities(self) -> List[Capability]:
        return [Capability.CODING]

    @property
    def provider_preferences(self) -> List[str]:
        return ["openrouter", "kimi", "groq", "gemini", "ollama_free_api"]

    async def format_execution_prompt(self, intent: Intent) -> str:
        system_instructions = (
            "You are the FullStackArchitect Skill. Focus on software design, modular architecture, "
            "clean code, and production readiness.\n\n"
        )
        return f"{system_instructions}Task: {intent.original_prompt}"


class SecurityAuditorSkill(Skill):
    @property
    def name(self) -> str:
        return "SecurityAuditor"

    @property
    def required_capabilities(self) -> List[Capability]:
        return [Capability.SYSTEM_INSPECTION]

    @property
    def provider_preferences(self) -> List[str]:
        return ["openrouter", "kimi", "groq", "gemini", "ollama_free_api"]

    async def format_execution_prompt(self, intent: Intent) -> str:
        system_instructions = (
            "You are the SecurityAuditor Skill. Analyze system security, identify potential "
            "vulnerabilities, and provide actionable remediation steps.\n\n"
        )
        return f"{system_instructions}Target: {intent.original_prompt}"


class ResearchSkill(Skill):
    @property
    def name(self) -> str:
        return "Research"

    @property
    def required_capabilities(self) -> List[Capability]:
        return [Capability.RESEARCH]

    @property
    def provider_preferences(self) -> List[str]:
        return ["openrouter", "kimi", "groq", "gemini", "ollama_free_api"]

    async def format_execution_prompt(self, intent: Intent) -> str:
        system_instructions = (
            "You are the Research Skill. Gather, compare, explain, and synthesize information clearly. "
            "Be accurate, structured, and concise when possible.\n\n"
        )
        return f"{system_instructions}Research task: {intent.original_prompt}"


class VisionSkill(Skill):
    @property
    def name(self) -> str:
        return "Vision"

    @property
    def required_capabilities(self) -> List[Capability]:
        return [Capability.VISION]

    @property
    def provider_preferences(self) -> List[str]:
        # Kimi vision remains disabled until the provider normalizes image
        # inputs for Moonshot's multimodal chat-completions format.
        return ["openrouter", "gemini", "groq", "ollama_free_api"]

    async def format_execution_prompt(self, intent: Intent) -> str:
        system_instructions = (
            "You are the Vision Skill. Analyze images, screenshots, charts, diagrams, and visual content. "
            "Describe what is present, extract useful details, and answer questions grounded in the image.\n\n"
        )
        return f"{system_instructions}Visual task: {intent.original_prompt}"


class GeneralChatSkill(Skill):
    @property
    def name(self) -> str:
        return "GeneralChat"

    @property
    def required_capabilities(self) -> List[Capability]:
        return [Capability.CHAT]

    @property
    def provider_preferences(self) -> List[str]:
        return ["openrouter", "kimi", "groq", "gemini", "ollama_free_api"]

    async def format_execution_prompt(self, intent: Intent) -> str:
        return intent.original_prompt


class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self.register_skill(FullStackArchitectSkill())
        self.register_skill(SecurityAuditorSkill())
        self.register_skill(ResearchSkill())
        self.register_skill(VisionSkill())
        self.register_skill(GeneralChatSkill())

    def register_skill(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        logger.info(f"[SkillRegistry] Registered skill: {skill.name}")

    def get_skill_for_intent(self, intent: Intent) -> Skill:
        for skill in self._skills.values():
            if intent.primary_capability in skill.required_capabilities:
                logger.info(
                    f"[SkillRegistry] Mapped capability '{intent.primary_capability.value}' to Skill '{skill.name}'"
                )
                return skill

        logger.warning(
            f"[SkillRegistry] No specific skill found for '{intent.primary_capability.value}'. Falling back to GeneralChat."
        )
        return self._skills["GeneralChat"]
