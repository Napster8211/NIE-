import os
import re
import logging
import uuid
import json
import sys
import asyncio
from typing import Optional, Dict, Any, List, AsyncGenerator
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# --- Database Imports ---
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db_session
from app.models.document import Document
from app.models.memory_models import Message

# --- Engine & Router Imports ---
from app.engine.intent_detector import IntentDetector
from app.engine.skills import SkillRegistry
from app.router.engine import CapabilityRouter
from app.providers.registry import provider_registry
from app.monitoring.performance_profiler import PerformanceProfiler
from app.schemas.chat import Attachment

# --- Sprint 1 Task Planner Imports ---
from app.planner.planner_registry import PlannerRegistry, ToolDefinition
from app.planner.planner import TaskPlanner, StructuredLLMProvider
from app.planner.execution_plan import PlanExecutorState
from app.planner.planner_models import ExecutionPlan

# --- Sprint 5 & 6 Deep Research & Autonomous Agent Imports ---
from app.planner.deep_research_planner import DeepResearchPlanner
from app.agent.execution_loop import AutonomousAgentLoop
from app.agent.state_models import AgentState, ExecutionTrace, ToolExecution
from app.tools.tool_manager import ToolManager
from app.tools.tool_registry import tool_registry, ToolRegistry
from app.tools.tool_executor import ToolExecutor
from app.tools.workspace_reader import WorkspaceReaderTool
from app.tools.workspace_writer import WorkspaceWriterTool

# --- SPRINT 2 IMPORTS (Lead Intelligence) ---
from app.tools.plugins.business_discovery import BusinessDiscoveryTool
from app.tools.plugins.lead_upsert import LeadUpsertTool
from app.agent.definitions.lead_agent import LeadIntelligenceAgent

# --- SPRINT 3.1 IMPORTS (Website Intelligence) ---
from app.tools.plugins.website_tools import WebsiteContextBuilderTool, WebsiteInspectorTool, WebsiteArtifactSaverTool
from app.agent.definitions.website_agent import WebsiteIntelligenceAgent

# --- SPRINT 4 IMPORTS (Opportunity Intelligence) ---
from app.tools.plugins.opportunity_tools import OpportunityContextBuilderTool, OpportunityEvaluatorTool, OpportunitySaverTool
from app.agent.definitions.opportunity_agent import OpportunityIntelligenceAgent

# --- SPRINT 5 IMPORTS (Business Solution Architecture) ---
from app.tools.plugins.solution_tools import SolutionContextBuilderTool, SolutionEvaluatorTool, SolutionSaverTool
from app.agent.definitions.solution_agent import BusinessSolutionArchitectAgent

# --- SPRINT 6 IMPORTS (Proposal Intelligence) ---
from app.tools.plugins.proposal_tools import ProposalContextBuilderTool, ProposalEvaluatorTool, ProposalSaverTool
from app.agent.definitions.proposal_agent import ProposalIntelligenceAgent

# --- SPRINT 7 IMPORTS (Visualization Architecture) ---
from app.tools.plugins.visualization_tools import VisualizationContextBuilderTool, VisualizationEvaluatorTool, VisualizationSaverTool
from app.agent.definitions.visualization_agent import SolutionVisualizationArchitectAgent

# --- SPRINT 8 IMPORTS (Technical Architecture) ---
from app.tools.plugins.technical_tools import TechnicalContextBuilderTool, TechnicalEvaluatorTool, TechnicalSaverTool
from app.agent.definitions.technical_agent import TechnicalSolutionArchitectAgent

# --- SPRINT 9 IMPORTS (Coding Intelligence) ---
from app.tools.plugins.coding_tools import CodingContextBuilderTool, CodingEvaluatorTool, CodingSaverTool
from app.agent.definitions.coding_agent import CodingIntelligenceAgent

# --- SPRINT 10 IMPORTS (Engineering Review Intelligence) ---
from app.tools.plugins.review_tools import ReviewContextBuilderTool, ReviewEvaluatorTool, ReviewSaverTool
from app.agent.definitions.review_agent import EngineeringReviewAgent

# --- SPRINT 11 IMPORTS (Deployment Intelligence) ---
from app.tools.plugins.deployment_tools import DeploymentContextBuilderTool, DeploymentEvaluatorTool, DeploymentSaverTool
from app.agent.definitions.deployment_agent import DeploymentIntelligenceAgent

# --- SPRINT 12 IMPORTS (Client Acquisition Intelligence) ---
from app.tools.plugins.acquisition_tools import AcquisitionContextBuilderTool, AcquisitionEvaluatorTool, AcquisitionSaverTool
from app.agent.definitions.acquisition_agent import ClientAcquisitionIntelligenceAgent

# --- SPRINT 13 IMPORTS (Content Intelligence) ---
from app.tools.plugins.content_tools import ContentContextBuilderTool, ContentEvaluatorTool, ContentSaverTool
from app.agent.definitions.content_agent import ContentIntelligenceAgent

# --- SPRINT 14 IMPORTS (Social Intelligence) ---
from app.tools.plugins.social_tools import SocialContextBuilderTool, SocialEvaluatorTool, SocialSaverTool
from app.agent.definitions.social_agent import SocialIntelligenceAgent

# --- SPRINT 15 IMPORTS (Campaign Intelligence) ---
from app.tools.plugins.campaign_tools import CampaignContextBuilderTool, CampaignEvaluatorTool, CampaignSaverTool
from app.agent.definitions.campaign_agent import CampaignIntelligenceAgent

# --- SPRINT 16 IMPORTS (Marketing Analytics Intelligence) ---
from app.tools.plugins.marketing_analytics_tools import MarketingAnalyticsContextBuilderTool, MarketingAnalyticsEvaluatorTool, MarketingAnalyticsSaverTool
from app.agent.definitions.marketing_analytics_agent import MarketingAnalyticsIntelligenceAgent

# --- SPRINT 17 IMPORTS (Publishing Intelligence) ---
from app.tools.plugins.publishing_tools import PublishingContextBuilderTool, PublishingEvaluatorTool, PublishingSaverTool
from app.agent.definitions.publishing_agent import PublishingIntelligenceAgent

# --- SPRINT 18 IMPORTS (Sales Intelligence) ---
from app.tools.plugins.sales_tools import SalesContextBuilderTool, SalesEvaluatorTool, SalesSaverTool
from app.agent.definitions.sales_agent import SalesIntelligenceAgent

# --- SPRINT 19 IMPORTS (Revenue Intelligence) ---
from app.tools.plugins.revenue_tools import RevenueContextBuilderTool, RevenueEvaluatorTool, RevenueSaverTool
from app.agent.definitions.revenue_agent import RevenueIntelligenceAgent

# --- SPRINT 20.1 IMPORTS (Enterprise Communication Intelligence) ---
from app.tools.plugins.communication_tools import CommunicationContextBuilderTool, CommunicationEvaluatorTool, CommunicationSaverTool
from app.agent.definitions.communication_agent import CommunicationIntelligenceAgent

# --- SPRINT 21 IMPORTS (Customer Success Intelligence) ---
from app.tools.plugins.customer_success_tools import CustomerSuccessContextBuilderTool, CustomerSuccessEvaluatorTool, CustomerSuccessSaverTool
from app.agent.definitions.customer_success_agent import CustomerSuccessIntelligenceAgent

# --- SPRINT 22 IMPORTS (Business Operations Intelligence) ---
from app.tools.plugins.business_operations_tools import BusinessOperationsContextBuilderTool, BusinessOperationsEvaluatorTool, BusinessOperationsSaverTool
from app.agent.definitions.business_operations_agent import BusinessOperationsIntelligenceAgent

# --- SPRINT 23 IMPORTS (Finance Intelligence) ---
from app.tools.plugins.finance_tools import FinanceContextBuilderTool, FinanceEvaluatorTool, FinanceSaverTool
from app.agent.definitions.finance_agent import FinanceIntelligenceAgent

# --- SPRINT 24 IMPORTS (Director Intelligence) ---
from app.tools.plugins.director_tools import (
    DirectorContextBuilderTool,
    DirectorEvaluatorTool,
    DirectorSaverTool,
)
from app.services.director_command_resolver import resolve_director_command
from app.agent.definitions.director_agent import DirectorIntelligenceAgent

from app.agent.agent_registry import agent_registry
from app.agent.agent_models import AgentContext, AgentPermission

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Strict Domain & URL Extraction Regex ---
URL_REGEX = r'(?:https?://[^\s]+)|(?:\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|edu|gov|io|co|gh|ai|dev|app|tech|info|biz|site|me|xyz)\b(?:/[^\s]*)?)'

# --- Component Initialization ---
intent_detector = IntentDetector()
skill_registry = SkillRegistry()
capability_router = CapabilityRouter(provider_registry=provider_registry)

tool_executor = ToolExecutor()
tool_manager = ToolManager(registry=tool_registry, executor=tool_executor)

# --- Register Tools ---
tool_registry.register(WorkspaceReaderTool())
tool_registry.register(WorkspaceWriterTool())
tool_registry.register(BusinessDiscoveryTool())  
tool_registry.register(LeadUpsertTool())

# Sprints 3.1 -> 24 Tool Registrations
tool_registry.register(WebsiteContextBuilderTool())
tool_registry.register(WebsiteInspectorTool())
tool_registry.register(WebsiteArtifactSaverTool())

tool_registry.register(OpportunityContextBuilderTool())
tool_registry.register(OpportunityEvaluatorTool())
tool_registry.register(OpportunitySaverTool())

tool_registry.register(SolutionContextBuilderTool())
tool_registry.register(SolutionEvaluatorTool())
tool_registry.register(SolutionSaverTool())

tool_registry.register(ProposalContextBuilderTool())
tool_registry.register(ProposalEvaluatorTool())
tool_registry.register(ProposalSaverTool())

tool_registry.register(VisualizationContextBuilderTool())
tool_registry.register(VisualizationEvaluatorTool())
tool_registry.register(VisualizationSaverTool())

tool_registry.register(TechnicalContextBuilderTool())
tool_registry.register(TechnicalEvaluatorTool())
tool_registry.register(TechnicalSaverTool())

tool_registry.register(CodingContextBuilderTool())
tool_registry.register(CodingEvaluatorTool())
tool_registry.register(CodingSaverTool())

tool_registry.register(ReviewContextBuilderTool())
tool_registry.register(ReviewEvaluatorTool())
tool_registry.register(ReviewSaverTool())

tool_registry.register(DeploymentContextBuilderTool())
tool_registry.register(DeploymentEvaluatorTool())
tool_registry.register(DeploymentSaverTool())

tool_registry.register(AcquisitionContextBuilderTool())
tool_registry.register(AcquisitionEvaluatorTool())
tool_registry.register(AcquisitionSaverTool())

tool_registry.register(ContentContextBuilderTool())
tool_registry.register(ContentEvaluatorTool())
tool_registry.register(ContentSaverTool())

tool_registry.register(SocialContextBuilderTool())
tool_registry.register(SocialEvaluatorTool())
tool_registry.register(SocialSaverTool())

tool_registry.register(CampaignContextBuilderTool())
tool_registry.register(CampaignEvaluatorTool())
tool_registry.register(CampaignSaverTool())

tool_registry.register(MarketingAnalyticsContextBuilderTool())
tool_registry.register(MarketingAnalyticsEvaluatorTool())
tool_registry.register(MarketingAnalyticsSaverTool())

tool_registry.register(PublishingContextBuilderTool())
tool_registry.register(PublishingEvaluatorTool())
tool_registry.register(PublishingSaverTool())

tool_registry.register(SalesContextBuilderTool())
tool_registry.register(SalesEvaluatorTool())
tool_registry.register(SalesSaverTool())

tool_registry.register(RevenueContextBuilderTool())
tool_registry.register(RevenueEvaluatorTool())
tool_registry.register(RevenueSaverTool())

tool_registry.register(CommunicationContextBuilderTool())
tool_registry.register(CommunicationEvaluatorTool())
tool_registry.register(CommunicationSaverTool())

tool_registry.register(CustomerSuccessContextBuilderTool())
tool_registry.register(CustomerSuccessEvaluatorTool())
tool_registry.register(CustomerSuccessSaverTool())

tool_registry.register(BusinessOperationsContextBuilderTool())
tool_registry.register(BusinessOperationsEvaluatorTool())
tool_registry.register(BusinessOperationsSaverTool())

tool_registry.register(FinanceContextBuilderTool())
tool_registry.register(FinanceEvaluatorTool())
tool_registry.register(FinanceSaverTool())

tool_registry.register(DirectorContextBuilderTool())
tool_registry.register(DirectorEvaluatorTool())
tool_registry.register(DirectorSaverTool())

# --- Register Agents ---
agent_registry.register(LeadIntelligenceAgent()) 
agent_registry.register(WebsiteIntelligenceAgent()) 
agent_registry.register(OpportunityIntelligenceAgent()) 
agent_registry.register(BusinessSolutionArchitectAgent())
agent_registry.register(ProposalIntelligenceAgent()) 
agent_registry.register(SolutionVisualizationArchitectAgent()) 
agent_registry.register(TechnicalSolutionArchitectAgent())
agent_registry.register(CodingIntelligenceAgent()) 
agent_registry.register(EngineeringReviewAgent()) 
agent_registry.register(DeploymentIntelligenceAgent()) 
agent_registry.register(ClientAcquisitionIntelligenceAgent()) 
agent_registry.register(ContentIntelligenceAgent()) 
agent_registry.register(SocialIntelligenceAgent()) 
agent_registry.register(CampaignIntelligenceAgent()) 
agent_registry.register(MarketingAnalyticsIntelligenceAgent()) 
agent_registry.register(PublishingIntelligenceAgent()) 
agent_registry.register(SalesIntelligenceAgent()) 
agent_registry.register(RevenueIntelligenceAgent())
agent_registry.register(CommunicationIntelligenceAgent()) 
agent_registry.register(CustomerSuccessIntelligenceAgent()) 
agent_registry.register(BusinessOperationsIntelligenceAgent()) 
agent_registry.register(FinanceIntelligenceAgent()) 
agent_registry.register(DirectorIntelligenceAgent())

# --- 1. Router LLM Adapter for Task Planner & Autonomous Loop ---
class RouterLLMProvider:
    """
    Adapter between NIE planning/autonomous loops and CapabilityRouter.

    OpenRouter is the primary inference gateway. Other registered providers remain
    operational fallbacks if OpenRouter is unavailable or unhealthy.

    The adapter accepts NIE-only inference governance hints and forwards them to
    CapabilityRouter, which forwards them only to OpenRouterProvider.
    """

    DEFAULT_PROVIDER_PREFERENCES = [
        "openrouter",
        "gemini",
        "groq",
        "cerebras",
        "kimi",
        "auto",
    ]

    def __init__(
        self,
        router: CapabilityRouter,
        *,
        default_cost_preference: str = "balanced",
        default_reasoning_level: str = "medium",
        default_max_cost_per_request_usd: float = 0.03,
    ):
        self.router = router
        self.default_cost_preference = default_cost_preference
        self.default_reasoning_level = default_reasoning_level
        self.default_max_cost_per_request_usd = default_max_cost_per_request_usd

    def _extract_json(self, text: str) -> str:
        clean_text = (text or "").strip()

        if not clean_text:
            raise ValueError(
                "LLM returned an empty response. Provider may have dropped the connection."
            )

        safety_match = re.search(
            r"User Safety:\s*([^\r\n]+)",
            clean_text,
            flags=re.IGNORECASE,
        )

        if safety_match:
            safety_value = safety_match.group(1).strip().lower()
            blocked_values = (
                "unsafe",
                "blocked",
                "refused",
                "deny",
                "denied",
                "rejected",
                "violation",
            )

            if any(value in safety_value for value in blocked_values):
                raise ValueError(
                    f"LLM safety filter blocked the response: {safety_match.group(0)}"
                )

            clean_text = (
                clean_text[:safety_match.start()]
                + clean_text[safety_match.end():]
            ).strip()

        if not clean_text:
            raise ValueError(
                "Provider returned only metadata and no structured response."
            )

        if "```json" in clean_text:
            clean_text = clean_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```", 1)[1].split("```", 1)[0].strip()

        match = re.search(r"\{.*\}", clean_text, re.DOTALL)
        if not match:
            raise ValueError("Provider response did not contain a JSON object.")

        return match.group(0).strip()

    def _routing_kwargs(
        self,
        *,
        cost_preference: Optional[str] = None,
        reasoning_level: Optional[str] = None,
        model_override: Optional[str] = None,
        max_model_cost_per_request_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        routing = {
            "cost_preference": (
                cost_preference or self.default_cost_preference
            ),
            "reasoning_level": (
                reasoning_level or self.default_reasoning_level
            ),
            "max_model_cost_per_request_usd": (
                self.default_max_cost_per_request_usd
                if max_model_cost_per_request_usd is None
                else max_model_cost_per_request_usd
            ),
        }
        if model_override:
            routing["model_override"] = model_override
        return routing

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_class: Any,
        *,
        cost_preference: Optional[str] = None,
        reasoning_level: Optional[str] = None,
        model_override: Optional[str] = None,
        max_model_cost_per_request_usd: Optional[float] = None,
    ) -> Any:
        schema_json = json.dumps(schema_class.model_json_schema(), indent=2)

        full_prompt = (
            f"{system_prompt}\n\n"
            f"USER REQUEST: {user_prompt}\n\n"
            "OUTPUT REQUIREMENT:\n"
            "You are a strict JSON data generator. You MUST output a valid JSON "
            "instance matching the schema below.\n"
            "CRITICAL JSON RULES:\n"
            "1. Properly escape newlines and double quotes inside string values.\n"
            "2. Do NOT use raw multiline strings inside the JSON payload.\n"
            "3. DO NOT output the JSON schema itself. DO NOT wrap your output in "
            "'$defs' or 'properties'.\n"
            f"Schema Requirements:\n{schema_json}\n\n"
            "Return ONLY valid JSON starting with { and ending with }."
        )

        routing = self._routing_kwargs(
            cost_preference=cost_preference,
            reasoning_level=reasoning_level,
            model_override=model_override,
            max_model_cost_per_request_usd=max_model_cost_per_request_usd,
        )

        logger.info(
            "[RouterLLMProvider] Provider preference order: %s | "
            "cost=%s reasoning=%s",
            " -> ".join(self.DEFAULT_PROVIDER_PREFERENCES),
            routing["cost_preference"],
            routing["reasoning_level"],
        )

        response_text = ""
        async for chunk in self.router.route_skill_execution(
            prompt=full_prompt,
            required_capabilities=["chat"],
            preferences=self.DEFAULT_PROVIDER_PREFERENCES,
            is_structured=True,
            retry_attempt=0,
            **routing,
        ):
            response_text += chunk

        try:
            clean_text = self._extract_json(response_text)
            clean_text = clean_text.replace('"""', '"')
            parsed_data = json.loads(clean_text, strict=False)

            if isinstance(parsed_data, dict):
                def find_actual_payload(d):
                    if isinstance(d, dict):
                        if ("goal" in d or "title" in d) and "steps" in d:
                            return d
                        for _, value in d.items():
                            if isinstance(value, dict):
                                nested = find_actual_payload(value)
                                if nested:
                                    return nested
                    return d

                parsed_data = find_actual_payload(parsed_data)

                if "title" in parsed_data and "goal" not in parsed_data:
                    parsed_data["goal"] = parsed_data.pop("title")

                if "steps" in parsed_data and isinstance(parsed_data["steps"], list):
                    for step in parsed_data["steps"]:
                        if isinstance(step, dict):
                            if "name" in step and "tool" not in step:
                                step["tool"] = step.pop("name")
                            if "reason" not in step:
                                step["reason"] = (
                                    f"Execution step for {step.get('tool', 'action')}"
                                )
                            if isinstance(step.get("parameters"), list):
                                param_dict = {}
                                for item in step["parameters"]:
                                    if (
                                        isinstance(item, dict)
                                        and "name" in item
                                        and "value" in item
                                    ):
                                        param_dict[item["name"]] = item["value"]
                                step["parameters"] = param_dict if param_dict else None

            return schema_class.model_validate(parsed_data)
        except Exception as exc:
            logger.error(
                "[RouterLLMProvider] Validation Error: %s | Raw Output: %s",
                exc,
                response_text[:2000],
            )
            raise ValueError(
                f"Failed to parse structured JSON: {str(exc)}"
            ) from exc

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str,
        *,
        cost_preference: Optional[str] = None,
        reasoning_level: Optional[str] = None,
        model_override: Optional[str] = None,
        max_model_cost_per_request_usd: Optional[float] = None,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """
        Generate structured JSON for AutonomousAgentLoop.

        The additional keyword-only arguments are intentionally compatible with
        execution_loop.py's model_routing propagation.
        """
        full_prompt = (
            f"{system_prompt}\n\n"
            "Process the input and return exactly one valid JSON object.\n\n"
            f"Input: {prompt}\n\n"
            "Return ONLY the JSON object. Do not prepend markdown, commentary, "
            "or safety metadata."
        )

        routing = self._routing_kwargs(
            cost_preference=cost_preference,
            reasoning_level=reasoning_level,
            model_override=model_override,
            max_model_cost_per_request_usd=max_model_cost_per_request_usd,
        )

        logger.info(
            "[RouterLLMProvider] JSON routing: OpenRouter-first | "
            "cost=%s reasoning=%s",
            routing["cost_preference"],
            routing["reasoning_level"],
        )

        max_attempts = 2
        last_error = None
        last_response_text = ""

        for attempt in range(1, max_attempts + 1):
            response_text = ""
            try:
                async for chunk in self.router.route_skill_execution(
                    prompt=full_prompt,
                    required_capabilities=["chat"],
                    preferences=self.DEFAULT_PROVIDER_PREFERENCES,
                    is_structured=True,
                    retry_attempt=attempt - 1,
                    **routing,
                ):
                    if chunk:
                        response_text += str(chunk)

                last_response_text = response_text
                clean_text = self._extract_json(response_text)
                parsed = json.loads(clean_text)

                if not isinstance(parsed, dict):
                    raise ValueError(
                        "Structured response must be a JSON object."
                    )
                if not parsed:
                    raise ValueError(
                        "Structured response was an empty JSON object."
                    )

                return parsed

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[RouterLLMProvider] JSON attempt %s/%s failed: %s",
                    attempt,
                    max_attempts,
                    str(exc),
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.25 * attempt)

        logger.error(
            "[RouterLLMProvider] JSON generation failed after %s attempts. "
            "Last error: %s | Raw Output: %s",
            max_attempts,
            str(last_error),
            last_response_text[:2000],
        )
        raise ValueError(
            "Failed to obtain a valid structured JSON response after "
            f"{max_attempts} attempts: {last_error}"
        )


# --- 2. Planner Registry Setup ---
planner_registry = PlannerRegistry()

planner_registry.register_tool(ToolDefinition(name="llm", description="Direct conversational response or synthesis."))
planner_registry.register_tool(ToolDefinition(name="web_search", description="Search the web for real-time or recent information."))
planner_registry.register_tool(ToolDefinition(name="url_reader", description="Scrape and extract text content from specified URLs."))
planner_registry.register_tool(ToolDefinition(name="python_executor", description="Run Python code for mathematical calculations."))
planner_registry.register_tool(ToolDefinition(name="image_analyzer", description="Analyze and extract insights from images."))
planner_registry.register_tool(ToolDefinition(name="document_retrieval", description="Search and pull content from documents."))
planner_registry.register_tool(ToolDefinition(name="workspace_reader", description="Inspect local project directories."))
planner_registry.register_tool(ToolDefinition(name="workspace_writer", description="Write, overwrite, or append code directly."))
planner_registry.register_tool(ToolDefinition(name="business_discovery", description="Discovers raw business leads."))
planner_registry.register_tool(ToolDefinition(name="lead_upsert", description="Persists canonical business leads to the database."))

# Sprints 3.1 -> 24 Tool Registrations
planner_registry.register_tool(ToolDefinition(name="website_context_builder", description="Retrieves isolated subset of Lead data."))
planner_registry.register_tool(ToolDefinition(name="website_inspector", description="Deterministically inspects a website for SEO, Accessibility, and Tech Stack."))
planner_registry.register_tool(ToolDefinition(name="website_artifact_saver", description="Persists the website intelligence audit to the database."))

planner_registry.register_tool(ToolDefinition(name="opportunity_context_builder", description="Merges Lead and Website artifacts into an isolated OpportunityContext."))
planner_registry.register_tool(ToolDefinition(name="opportunity_evaluator", description="Runs deterministic rules to map evidence to recommended services."))
planner_registry.register_tool(ToolDefinition(name="opportunity_artifact_saver", description="Persists OpportunityArtifact."))

planner_registry.register_tool(ToolDefinition(name="solution_context_builder", description="Merges Lead and Opportunity artifacts into an isolated SolutionContext."))
planner_registry.register_tool(ToolDefinition(name="solution_evaluator", description="Generates a deterministic digital architecture blueprint."))
planner_registry.register_tool(ToolDefinition(name="solution_artifact_saver", description="Persists BusinessSolutionArtifact and registers it with the Registry."))

planner_registry.register_tool(ToolDefinition(name="proposal_context_builder", description="Merges verified artifacts into an isolated ProposalContext."))
planner_registry.register_tool(ToolDefinition(name="proposal_evaluator", description="Generates a deterministic proposal architecture."))
planner_registry.register_tool(ToolDefinition(name="proposal_artifact_saver", description="Persists ProposalArtifact and registers it with the Registry."))

planner_registry.register_tool(ToolDefinition(name="visualization_context_builder", description="Merges verified artifacts into an isolated VisualizationContext."))
planner_registry.register_tool(ToolDefinition(name="visualization_evaluator", description="Generates UX and UI architecture blueprint."))
planner_registry.register_tool(ToolDefinition(name="visualization_artifact_saver", description="Persists VisualizationArtifact and registers it with the Registry."))

planner_registry.register_tool(ToolDefinition(name="technical_context_builder", description="Merges Business and Visualization artifacts into an isolated TechnicalContext."))
planner_registry.register_tool(ToolDefinition(name="technical_evaluator", description="Generates a deterministic system architecture blueprint."))
planner_registry.register_tool(ToolDefinition(name="technical_artifact_saver", description="Persists TechnicalArchitectureArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="coding_context_builder", description="Merges Technical and Visualization artifacts into an isolated CodingContext."))
planner_registry.register_tool(ToolDefinition(name="coding_evaluator", description="Generates a deterministic project codebase implementation artifact."))
planner_registry.register_tool(ToolDefinition(name="coding_artifact_saver", description="Persists ImplementationArtifact and registers it with the Registry."))

planner_registry.register_tool(ToolDefinition(name="review_context_builder", description="Merges Technical and Implementation artifacts for Governance Review."))
planner_registry.register_tool(ToolDefinition(name="review_evaluator", description="Audits software implementation against technical architecture."))
planner_registry.register_tool(ToolDefinition(name="review_artifact_saver", description="Persists ReviewArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="deployment_context_builder", description="Merges Review and Implementation artifacts to validate deployment readiness."))
planner_registry.register_tool(ToolDefinition(name="deployment_evaluator", description="Executes the deployment pipeline and generates preview metrics."))
planner_registry.register_tool(ToolDefinition(name="deployment_artifact_saver", description="Persists DeploymentArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="acquisition_context_builder", description="Merges Deployments and Business artifacts for outreach preparation."))
planner_registry.register_tool(ToolDefinition(name="acquisition_evaluator", description="Generates CRM strategy and outreach personalization."))
planner_registry.register_tool(ToolDefinition(name="acquisition_artifact_saver", description="Persists ClientAcquisitionArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="content_context_builder", description="Loads company state, milestones, and CRM data to build a strategic content context."))
planner_registry.register_tool(ToolDefinition(name="content_evaluator", description="Generates a deterministic marketing strategy and content calendar."))
planner_registry.register_tool(ToolDefinition(name="content_artifact_saver", description="Persists ContentArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="social_context_builder", description="Loads the Content Strategy Artifact to build a Social Context."))
planner_registry.register_tool(ToolDefinition(name="social_evaluator", description="Generates deterministic, platform-specific social media assets."))
planner_registry.register_tool(ToolDefinition(name="social_artifact_saver", description="Persists SocialArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="campaign_context_builder", description="Loads Content and Social Artifacts to build a Campaign Context."))
planner_registry.register_tool(ToolDefinition(name="campaign_evaluator", description="Orchestrates marketing assets into a measurable publishing sequence."))
planner_registry.register_tool(ToolDefinition(name="campaign_artifact_saver", description="Persists CampaignArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="marketing_analytics_context_builder", description="Loads Campaign Artifact and telemetry data for performance analysis."))
planner_registry.register_tool(ToolDefinition(name="marketing_analytics_evaluator", description="Calculates campaign performance and generates optimization insights."))
planner_registry.register_tool(ToolDefinition(name="marketing_analytics_artifact_saver", description="Persists MarketingAnalyticsArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="publishing_context_builder", description="Loads Campaign and Social Artifacts for publishing readiness."))
planner_registry.register_tool(ToolDefinition(name="publishing_evaluator", description="Executes multi-channel publishing operations."))
planner_registry.register_tool(ToolDefinition(name="publishing_artifact_saver", description="Persists PublishingArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="sales_context_builder", description="Aggregates Lead, Opportunity, Proposal, and Deployment artifacts for sales intelligence."))
planner_registry.register_tool(ToolDefinition(name="sales_evaluator", description="Evaluates opportunity buying intent and prepares meeting agendas."))
planner_registry.register_tool(ToolDefinition(name="sales_artifact_saver", description="Persists SalesArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="revenue_context_builder", description="Loads Sales, CRM, and Pipeline artifacts to build an executive revenue context."))
planner_registry.register_tool(ToolDefinition(name="revenue_evaluator", description="Computes revenue forecasts, pipeline health, and executive KPIs."))
planner_registry.register_tool(ToolDefinition(name="revenue_artifact_saver", description="Persists RevenueArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="communication_context_builder", description="Aggregates verified business artifacts and validates governance gates for outbound delivery."))
planner_registry.register_tool(ToolDefinition(name="communication_evaluator", description="Personalizes communication, monitors engagement, and publishes events."))
planner_registry.register_tool(ToolDefinition(name="communication_artifact_saver", description="Persists CommunicationArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="customer_success_context_builder", description="Loads post-sale artifacts and CRM timeline to evaluate customer health."))
planner_registry.register_tool(ToolDefinition(name="customer_success_evaluator", description="Calculates health scores, onboarding progress, and expansion opportunities."))
planner_registry.register_tool(ToolDefinition(name="customer_success_artifact_saver", description="Persists CustomerSuccessArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="business_operations_context_builder", description="Loads OS-wide telemetry, artifact counts, and agent registries for COO analysis."))
planner_registry.register_tool(ToolDefinition(name="business_operations_evaluator", description="Evaluates department health, workflows, and operational bottlenecks."))
planner_registry.register_tool(ToolDefinition(name="business_operations_artifact_saver", description="Persists BusinessOperationsArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="finance_context_builder", description="Loads Revenue, Operations, and Expense repositories for CFO analysis."))
planner_registry.register_tool(ToolDefinition(name="finance_evaluator", description="Evaluates runway, profitability, budgets, and ROI."))
planner_registry.register_tool(ToolDefinition(name="finance_artifact_saver", description="Persists FinanceArtifact and registers it."))

planner_registry.register_tool(ToolDefinition(name="director_context_builder", description="Loads Operations, Finance, and Revenue artifacts to build an executive briefing context."))
planner_registry.register_tool(ToolDefinition(name="director_evaluator", description="Synthesizes executive reports, delegates tasks, and records strategic decisions."))
planner_registry.register_tool(ToolDefinition(name="director_artifact_saver", description="Persists DirectorArtifact and registers it."))


llm_provider = RouterLLMProvider(router=capability_router)
task_planner = TaskPlanner(registry=planner_registry, llm_provider=llm_provider)

NAPSTERTEC_SYSTEM_PROMPT = (
    "[System Instruction]\n"
    "You are NapsterTec AI, an intelligent AI assistant powered by the NapsterTec Intelligence Engine (NIE).\n\n"
)

# --- Request / Response Models ---
class ChatRequest(BaseModel):
    prompt: str
    stream: Optional[bool] = True
    context: Optional[Dict[str, Any]] = None
    attachments: Optional[List[Attachment]] = Field(default_factory=list)
    think: Optional[bool] = False
    search: Optional[bool] = False

class ResearchRequest(BaseModel):
    topic: str = Field(..., description="The complex topic or query to research.")
    max_iterations: int = Field(default=3, description="Maximum number of research loops.")

class AgentExecutionRequest(BaseModel):
    goal: str = Field(..., description="The objective for the autonomous agent to achieve.")
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique session identifier.")

class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

# --- Endpoints ---
@router.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "online", "engine": "NapsterTec Intelligence Engine (NIE) Active"}

@router.put("/memory/conversations/{conversation_id}")
async def update_conversation_memory(conversation_id: str, request: Optional[UpdateConversationRequest] = None):
    logger.info(f"[Memory API] Syncing metadata for conversation: {conversation_id}")
    return {"status": "success", "conversation_id": conversation_id, "message": "Conversation state synchronized."}

@router.post("/chat")
async def chat_endpoint(request: ChatRequest, db: AsyncSession = Depends(get_db_session)):
    request_id = str(uuid.uuid4())[:8]
    profiler = PerformanceProfiler(request_id=request_id)

    try:
        profiler.start("Request Received")
        profiler.end("Request Received")

        profiler.start("Authentication")
        profiler.end("Authentication")

        profiler.start("Request Validation")
        profiler.end("Request Validation")

        profiler.start("Conversation Loading")
        conversation_id = request.context.get("conversation_id", "conv_default") if request.context else "conv_default"
        profiler.set_conversation_id(conversation_id)

        history_context = ""
        try:
            stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.desc()).limit(10)
            result = await db.execute(stmt)
            messages = list(result.scalars().all())
            messages.reverse()

            if messages:
                history_context = "\n[CONVERSATION HISTORY]\n"
                for msg in messages:
                    role = getattr(msg, "role", "user").capitalize()
                    content = getattr(msg, "content", "")
                    if len(content) > 8000:
                        content = content[:8000] + "... [Truncated for Context Limits]"
                    history_context += f"{role}: {content}\n"
                history_context += "[END CONVERSATION HISTORY]\n\n"
        except Exception as db_err:
            logger.warning(f"[Memory Warning] Could not fetch conversation history: {db_err}")

        profiler.end("Conversation Loading")

        web_context = ""
        raw_matches = re.findall(URL_REGEX, request.prompt)
        extracted_urls = []
        IGNORE_PATTERNS = {"e.g", "i.e", "etc", "ex", "v1.0", "v2.0", "v3.0", "vs"}
        for m in raw_matches:
            clean_u = m.rstrip('.,?!();:').strip()
            if not clean_u or clean_u.lower() in IGNORE_PATTERNS:
                continue
            parts = clean_u.replace("http://", "").replace("https://", "").split("/")[0].split(".")
            if all(p.isdigit() for p in parts):
                continue
            if clean_u not in extracted_urls:
                extracted_urls.append(clean_u)

        if request.search or extracted_urls:
            for raw_url in extracted_urls:
                fetch_url = raw_url if raw_url.startswith("http") else f"https://{raw_url}"
                try:
                    tool_result = await tool_manager.run_step("url_reader", parameters={"url": fetch_url})
                    extracted_text = ""
                    if hasattr(tool_result, "data"):
                        if isinstance(tool_result.data, dict):
                            extracted_text = tool_result.data.get("extracted_text", str(tool_result.data))
                        elif tool_result.data:
                            extracted_text = str(tool_result.data)
                    elif isinstance(tool_result, dict):
                        extracted_text = tool_result.get("extracted_text", str(tool_result))
                    else:
                        extracted_text = str(tool_result)

                    if extracted_text and extracted_text.strip() not in ["None", ""]:
                        compressed_text = extracted_text.strip()[:15000]
                        web_context += f"\n\n[LIVE FETCHED WEB CONTENT FROM {fetch_url}]\n{compressed_text}\n[END OF LIVE WEB CONTENT]\n\n"
                except Exception as scrape_err:
                    web_context += f"\n\n[Web Fetch Error for {fetch_url}: {str(scrape_err)}]\n\n"

            if request.search and not extracted_urls:
                try:
                    search_result = await tool_manager.run_step("web_search", parameters={"query": request.prompt})
                    search_text = ""
                    if hasattr(search_result, "data"):
                        search_text = str(search_result.data)
                    elif isinstance(search_result, dict):
                        search_text = search_result.get("results", str(search_result))
                    else:
                        search_text = str(search_result)

                    if search_text:
                        compressed_search = search_text.strip()[:15000]
                        web_context += f"\n\n[LIVE WEB SEARCH RESULTS]\n{compressed_search}\n[END OF WEB SEARCH RESULTS]\n\n"
                except Exception as search_err:
                    pass

        think_instruction = ""
        if request.think:
            think_instruction = (
                "\n\n[SYSTEM INSTRUCTION: DEEP REASONING MODE]\n"
                "Use deeper internal reasoning and verification before answering. "
                "Return only the useful final answer; do not expose private chain-of-thought.\n\n"
            )

        profiler.start("Intent Detection")
        intent = await intent_detector.classify_intent(
            prompt=f"{history_context}Current Request: {request.prompt}",
            attachments=request.attachments
        ) if hasattr(intent_detector.classify_intent, '__code__') and 'attachments' in intent_detector.classify_intent.__code__.co_varnames else await intent_detector.classify_intent(f"{history_context}Current Request: {request.prompt}")
        profiler.end("Intent Detection")

        profiler.start("Task Planning")
        intent_meta = {
            "primary_capability": intent.primary_capability.value if hasattr(intent, 'primary_capability') else "chat",
            "attachment_count": len(request.attachments or []),
            "think_enabled": bool(request.think),
            "search_enabled": bool(request.search or extracted_urls)
        }
        execution_plan: ExecutionPlan = await task_planner.generate_plan(
            prompt=request.prompt, 
            intent_metadata=intent_meta
        )
        profiler.end("Task Planning")

        profiler.start("Skill Selection")
        skill = skill_registry.get_skill_for_intent(intent)
        execution_prompt = await skill.format_execution_prompt(intent)
        profiler.end("Skill Selection")

        document_context = ""
        if request.attachments:
            document_context += "\n\n[USER ATTACHED FILES]\n"
            for attachment in request.attachments:
                if isinstance(attachment, dict):
                    doc_id = attachment.get("id") or attachment.get("document_id")
                else:
                    doc_id = getattr(attachment, "id", None) or getattr(attachment, "document_id", None)
                
                if doc_id:
                    stmt = select(Document).where(Document.id == doc_id)
                    result = await db.execute(stmt)
                    doc = result.scalar_one_or_none()
                    
                    if doc:
                        content = doc.extracted_text
                        if not content and doc.file_path and os.path.exists(doc.file_path):
                            try:
                                with open(doc.file_path, "r", encoding="utf-8", errors="ignore") as f:
                                    content = f.read(50000)
                            except Exception as e:
                                logger.error(f"Could not read local file {doc.file_path}: {e}")
                        
                        if content:
                            doc_text = content[:20000]
                            document_context += f"--- Start of File: {doc.filename} ---\n{doc_text}\n--- End of File ---\n\n"
                        else:
                            document_context += f"--- File: {doc.filename} (No readable text found) ---\n\n"

        if isinstance(execution_prompt, str):
            branded_prompt = f"{NAPSTERTEC_SYSTEM_PROMPT}{think_instruction}{history_context}{web_context}{document_context}{execution_prompt}"
        else:
            branded_prompt = execution_prompt

        profiler.start("Capability Routing")
        profiler.end("Capability Routing")

        profiler.start("Provider Selection")
        primary_provider = "Groq"
        
        profiler.set_metadata({
            "provider_name": primary_provider.capitalize(),
            "model_id": "Router Selected",
            "capability_used": intent.primary_capability.value if hasattr(intent, 'primary_capability') else "chat",
            "skill_used": skill.name,
            "planner_goal": execution_plan.goal,
            "estimated_steps": execution_plan.estimated_steps,
            "requires_tools": execution_plan.requires_tools,
            "requires_research": execution_plan.requires_research,
            "streaming_enabled": bool(request.stream),
            "fallback_used": False,
            "retry_count": 0,
            "attachment_count": len(request.attachments or [])
        })
        profiler.end("Provider Selection")

        profiler.start("Provider Request Sent")

        async def event_generator():
            profiler.start("Waiting For First Token")
            first_chunk = True

            prefs = [
                "openrouter",
                "gemini",
                "groq",
                "cerebras",
                "kimi",
                "auto",
            ]

            # Normal chat stays on the cheap reliable tier. Explicit "think"
            # requests increase reasoning depth without blindly selecting the
            # most expensive model. Vision/document work is handled by the
            # OpenRouter capability/model router.
            cost_preference = "balanced"
            reasoning_level = "high" if request.think else "medium"
            per_request_cost_limit = 0.03 if request.think else 0.015

            route_kwargs = {
                "prompt": branded_prompt,
                "required_capabilities": skill.required_capabilities,
                "preferences": prefs,
                "cost_preference": cost_preference,
                "reasoning_level": reasoning_level,
                "max_model_cost_per_request_usd": per_request_cost_limit,
            }
            if request.attachments:
                route_kwargs["attachments"] = request.attachments

            async for chunk in capability_router.route_skill_execution(**route_kwargs):
                if first_chunk:
                    profiler.end("Provider Request Sent")
                    profiler.end("Waiting For First Token")
                    profiler.start("First Token Received")
                    profiler.end("First Token Received")
                    profiler.start("Streaming Started")
                    profiler.end("Streaming Started")
                    first_chunk = False

                profiler.record_chunk(token_count=1)
                yield chunk

        if request.stream:
            async def profiled_stream_wrapper():
                async for chunk in event_generator():
                    yield chunk

            return StreamingResponse(profiled_stream_wrapper(), media_type="text/event-stream")
        else:
            full_response = ""
            async for chunk in event_generator():
                full_response += chunk

            return {
                "response": full_response,
                "skill_used": skill.name,
                "primary_capability": intent.primary_capability.value if hasattr(intent, 'primary_capability') else "chat",
                "execution_plan": execution_plan.model_dump()
            }

    except Exception as e:
        logger.error(f"[Endpoint Error]: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/research")
async def execute_deep_research(request: ResearchRequest):
    logger.info(f"Received deep research request for topic: {request.topic}")
    try:
        planner = DeepResearchPlanner(llm_client=llm_provider, max_iterations=request.max_iterations)
        enterprise_report = await planner.execute_research(topic=request.topic)
        return enterprise_report.model_dump()
    except Exception as e:
        logger.error(f"Deep Research execution failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Research Engine Error: {str(e)}")

@router.post("/agent/stream")
async def stream_agent_execution(request: AgentExecutionRequest):
    """
    Executes the Autonomous Agent Loop with strict Runtime Tool Enforcement.
    """
    logger.info(f"Received agent execution request for goal: {request.goal} (Session: {request.session_id})")

    async def sse_generator() -> AsyncGenerator[str, None]:
        try:
            state = AgentState(
                session_id=request.session_id,
                goal=request.goal,
            )

            goal_lower = request.goal.lower()

            read_only_signals = [
                "read-only", "read only", "inspect", "audit", "review the codebase",
                "review codebase", "summarize the file", "show me the file",
                "show the project structure", "search the codebase", "find in the codebase",
                "analyze", "review this source file", "explain the codebase", "show the tests", "search the repository"
            ]

            # Strict external mutation negation signals
            external_negation_signals = [
                "do not deploy", "no external mutation", "no external mutations",
                "do not modify production data", "do not send outreach",
            ]

            mutation_verbs = [
                r"\bmodify\b", r"\bedit\b", r"\bpatch\b", r"\bupdate\b", r"\bfix\b", r"\bwrite\b", 
                r"\bcreate\b", r"\badd\b", r"\bdelete\b", r"\bremove\b", r"\brefactor\b", 
                r"\bimplement\b", r"\breplace\b"
            ]

            repo_targets = [
                "repository", "repo", "workspace", "codebase", "source code", "source file",
                "project file", "project files", "test file", "tests", ".py", ".js", ".jsx",
                ".ts", ".tsx", "app/", "src/", "tests/"
            ]

            negated_local_mutation_pattern = r"(do not|don't)\s+(modify|edit|update|change|mutate|write|patch)\s+(the\s+)?(repository|repo|codebase|workspace|source|code|files?)"
            
            explicit_local_repo_mutation_phrases = [
                "repository development", "implement this sprint", "implement sprint", 
                "use workspace_writer", "repository write authorized"
            ]

            has_mutation_verb = any(re.search(v, goal_lower) for v in mutation_verbs)
            has_repo_target = any(t in goal_lower for t in repo_targets)
            
            explicit_local_repo_mutation = any(p in goal_lower for p in explicit_local_repo_mutation_phrases)
            negated_local_mutation = bool(re.search(negated_local_mutation_pattern, goal_lower))
            
            explicit_read_only = any(ro in goal_lower for ro in read_only_signals)
            explicit_external_negation = any(en in goal_lower for en in external_negation_signals)

            is_repository_development = (
                (explicit_local_repo_mutation or (has_repo_target and has_mutation_verb))
                and not negated_local_mutation
            )
            
            is_general_mutation_allowed = has_mutation_verb and not explicit_read_only and not negated_local_mutation

            # If it's repo development, it overrides general read-only.
            # If it's not repo dev and not general mutation, it's read only.
            read_only_resolved = explicit_read_only and not is_repository_development
            if not is_repository_development and not is_general_mutation_allowed:
                read_only_resolved = True

            command_context = {
                "read_only": read_only_resolved,
                "mutation_allowed": is_general_mutation_allowed or is_repository_development,
                "local_repository_mutation_allowed": is_repository_development,
                "access_mode": "WRITE_APPROVED" if (is_general_mutation_allowed or is_repository_development) else "READ_ONLY",
                "external_side_effect_allowed": not explicit_external_negation if is_general_mutation_allowed else False,
                "command_class": "REPOSITORY_DEVELOPMENT" if is_repository_development else "GENERAL_TASK",
                "source": "agent_stream",
                "session_id": request.session_id,
            }

            workspace_task_signals = (
                "workspace",
                "codebase",
                "project structure",
                "project directory",
                "read app/",
                "read src/",
                "read file",
                ".py",
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
                "search the codebase",
                "find in the codebase",
                "inspect the file",
                "summarize the file",
            )

            is_workspace_task = any(
                signal in goal_lower for signal in workspace_task_signals
            )

            routing_signals = {
                "finance_intelligence": (
                    "financial health",
                    "finance intelligence",
                    "cfo",
                ),
                "business_operations_intelligence": (
                    "operational health",
                    "business operations intelligence",
                    "coo",
                ),
                "customer_success_intelligence": (
                    "customer success intelligence",
                    "customer health",
                    "onboarding status",
                    "churn risk",
                ),
                "communication_intelligence": (
                    "communication intelligence",
                    "deliver the approved proposal",
                    "deliver proposal",
                    "monitor customer engagement",
                    "outbound communication",
                ),
                "revenue_intelligence": (
                    "revenue intelligence",
                    "revenue pipeline",
                    "revenue forecast",
                    "revops",
                ),
                "sales_intelligence": (
                    "sales intelligence",
                    "evaluate the sales opportunity",
                    "sales opportunity",
                ),
                "publishing_intelligence": (
                    "publishing intelligence",
                    "publish the ",
                    "publish campaign",
                ),
                "marketing_analytics": (
                    "marketing analytics intelligence",
                    "campaign performance",
                    "marketing performance",
                ),
                "campaign_intelligence": (
                    "campaign intelligence",
                    "marketing campaign",
                    "campaign management",
                ),
                "social_intelligence": (
                    "social intelligence",
                    "social media assets",
                ),
                "content_intelligence": (
                    "content intelligence",
                    "content strategy",
                ),
                "client_acquisition": (
                    "client acquisition intelligence",
                    "acquisition intelligence",
                    "prepare outreach",
                    "acquisition strategy",
                ),
                "deployment_intelligence": (
                    "deployment intelligence",
                    "deploy the demo",
                    "deploy project",
                    "launch deployment",
                ),
                "engineering_review": (
                    "engineering review intelligence",
                    "governance review",
                    "review implementation artifact",
                    "review the implementation artifact",
                ),
                "coding_intelligence": (
                    "coding intelligence",
                    "implement the approved technical architecture",
                    "generate project codebase",
                ),
                "technical_solution_architect": (
                    "technical solution architect",
                    "technical architecture",
                ),
                "solution_visualization_architect": (
                    "solution visualization architect",
                    "visualization architecture",
                    "ux architecture",
                ),
                "proposal_intelligence": (
                    "proposal intelligence",
                    "generate proposal",
                    "create proposal",
                ),
                "business_solution_architect": (
                    "business solution architect",
                    "design business solution",
                    "solution architecture",
                ),
                "opportunity_intelligence": (
                    "opportunity intelligence",
                    "evaluate opportunity",
                    "evaluate the opportunity",
                ),
                "website_intelligence": (
                    "website intelligence",
                    "analyze website",
                    "inspect website",
                ),
                "lead_intelligence": (
                    "lead intelligence",
                    "discover qualified",
                    "find qualified leads",
                    "find restaurant leads",
                    "discover restaurant prospects",
                ),
            }

            target_agent_name = None
            director_resolution = resolve_director_command(request.goal)
            if not is_workspace_task and (
                director_resolution["director_addressed"]
                or director_resolution["mission_intent"]
                or director_resolution.get("objective_intent", False)
            ):
                target_agent_name = "director_intelligence"

            if not is_workspace_task and target_agent_name is None:
                for candidate_agent, phrases in routing_signals.items():
                    if any(phrase in goal_lower for phrase in phrases):
                        target_agent_name = candidate_agent
                        break

            if target_agent_name == "director_intelligence":
                # Carry the exact canonical resolution into the Director tools;
                # no endpoint-local or tool-local reclassification is allowed.
                merged_context = dict(director_resolution)
                merged_context.update({
                    "source": "agent_stream",
                    "session_id": request.session_id,
                    "local_repository_mutation_allowed": command_context.get("local_repository_mutation_allowed", False),
                    # FIX: Prioritize canonical Director resolution over local workspace fallback
                    "command_class": director_resolution.get("command_class", command_context.get("command_class"))
                })
                command_context = merged_context

            target_agent = (
                agent_registry.get_agent(target_agent_name)
                if target_agent_name
                else None
            )

            # 2. Fast-Path Intercept for Deterministic SDK Agents
            if target_agent:
                yield f"data: {json.dumps({'type': 'initialization', 'state': state.model_dump()})}\n\n"
                await asyncio.sleep(0.2)
                trace = ExecutionTrace(
                    step_number=1, 
                    thought=f"Intent Confirmed. Routing to deterministic SDK agent: {target_agent.metadata.display_name}..."
                )
                yield f"data: {json.dumps({'type': 'trace_update', 'phase': 'think', 'trace': trace.model_dump()})}\n\n"
                await asyncio.sleep(0.5) 
                
                trace.action = ToolExecution(tool_name="strict_pipeline_execution", arguments={})
                yield f"data: {json.dumps({'type': 'trace_update', 'phase': 'act', 'trace': trace.model_dump()})}\n\n"
                
                context = AgentContext(
                    task=request.goal,
                    session_id=request.session_id,
                    runtime_metadata={"command_context": command_context},
                )

                granted_permissions = {AgentPermission.READ}
                if command_context.get("mutation_allowed", False):
                    granted_permissions.add(AgentPermission.WRITE)
                if command_context.get("local_repository_mutation_allowed", False):
                    granted_permissions.add(AgentPermission.LOCAL_REPOSITORY_WRITE)
                if command_context.get("external_side_effect_allowed", False):
                    granted_permissions.add(AgentPermission.EXTERNAL_API)

                context.granted_permissions = granted_permissions

                target_agent.inject_dependencies(tool_manager=tool_manager)
                
                result = await target_agent.run(context)
                
                trace.observation = "Operations complete. Formatting execution report..."
                yield f"data: {json.dumps({'type': 'trace_update', 'phase': 'observe', 'trace': trace.model_dump()})}\n\n"
                await asyncio.sleep(0.5)
                
                state.status = "completed"
                state.final_result = result.final_output
                yield f"data: {json.dumps({'type': 'completion', 'state': state.model_dump()})}\n\n"
                return

            # 3. Fallback for Autonomous Tool Loop
            intent = await intent_detector.classify_intent(f"Current Request: {request.goal}")

            scoped_registry = ToolRegistry()
            all_tools = getattr(
                tool_registry,
                "tools",
                getattr(tool_registry, "_tools", {}),
            )

            for name, tool in all_tools.items():
                if (
                    name == "workspace_writer"
                    and not command_context.get("local_repository_mutation_allowed", False)
                ):
                    continue

                scoped_registry.register(tool)

            scoped_tool_manager = ToolManager(
                registry=scoped_registry,
                executor=tool_executor,
            )

            agent_loop = AutonomousAgentLoop(
                primary_llm=llm_provider,
                fallback_llm=llm_provider,
                tool_manager=scoped_tool_manager,
                command_context=command_context,
                model_routing={
                    # Autonomous loops can multiply model calls rapidly, so use
                    # the cheap reliable tier by default and escalate reasoning
                    # only when the loop explicitly needs it.
                    "cost_preference": "balanced",
                    "reasoning_level": "medium",
                    "max_model_cost_per_request_usd": 0.02,
                },
            )

            async for telemetry_event in agent_loop.execute_goal(
                session_id=request.session_id, 
                goal=request.goal
            ):
                event_data = json.dumps(telemetry_event)
                yield f"data: {event_data}\n\n"
                
                if telemetry_event.get("type") in ["completion", "failure"]:
                    break
                    
        except asyncio.CancelledError:
            logger.warning(f"Client disconnected during session {request.session_id}.")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Client disconnected prematurely. (Frontend Timeout)'})}\n\n"
        except Exception as e:
            logger.error(f"Agent execution stream failed: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': f'Fatal Loop Error: {str(e)}'})}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )