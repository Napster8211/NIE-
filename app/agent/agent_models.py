"""
NapsterTec AI Operating System - Agent SDK Models
Module: app/agent/agent_models.py
Description: Core Pydantic models, enums, context representations, and standardized
             result objects for the NapsterTec Agent SDK.
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# 1. Capabilities & Permissions Enums
# ============================================================================

class AgentCapability(str, Enum):
    """
    Strongly-typed capabilities discoverable across the NapsterTec OS.
    Extensible and provider-agnostic.
    """
    CHAT = "chat"
    CODING = "coding"
    RESEARCH = "research"
    DOCUMENTS = "documents"
    IMAGES = "images"
    LEAD_GENERATION = "lead_generation"
    WEBSITE_ANALYSIS = "website_analysis"
    PROPOSAL_GENERATION = "proposal_generation"
    SOCIAL_MEDIA = "social_media"
    AUTOMATION = "automation"
    PLANNING = "planning"
    KNOWLEDGE = "knowledge"


class AgentPermission(str, Enum):
    """
    Granular security permissions for agent operations.
    Enforces authorization constraints prior to tool execution.
    """
    READ = "read"
    WRITE = "write"
    LOCAL_REPOSITORY_WRITE = "local_repository_write"
    EXTERNAL_API = "external_api"
    READ_EXTERNAL_DISCOVERY = "read_external_discovery"
    WRITE_EXTERNAL = "write_external"
    OUTREACH = "outreach"
    FILESYSTEM = "filesystem"
    EMAIL = "email"
    DEPLOYMENT = "deployment"
    PUBLISHING = "publishing"
    CRM = "crm"
    WORKSPACE = "workspace"
    EXECUTION = "execution"
    FINANCIAL_COMMITMENT = "financial_commitment"
    DESTRUCTIVE_ACTION = "destructive_action"


class ExecutionMode(str, Enum):
    """Execution lifecycle modes for agent tasks."""
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"
    BACKGROUND = "background"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"


# ============================================================================
# 2. Agent Metadata Model
# ============================================================================

class AgentMetadata(BaseModel):
    """
    Complete architectural identity and operating constraints of an intelligent agent.
    """
    name: str = Field(..., description="Unique technical identifier for the agent (e.g., 'coding_agent').")
    display_name: str = Field(..., description="Human-readable title (e.g., 'Lead System Architect').")
    description: str = Field(..., description="Detailed description of the agent's purpose and specialization.")
    version: str = Field(default="1.0.0", description="SemVer version of the agent specification.")
    category: str = Field(default="general", description="Operational category (e.g., 'engineering', 'sales').")
    
    # --- NEW SPRINT 6B.2B CANONICAL DEPARTMENT FIELDS ---
    department_id: Optional[str] = Field(default=None, description="Canonical executive department machine identifier.")
    department_name: Optional[str] = Field(default=None, description="Human-readable executive department name.")
    
    capabilities: Set[AgentCapability] = Field(default_factory=set, description="Capabilities provided by this agent.")
    supported_task_types: List[str] = Field(default_factory=list, description="Task intent strings this agent can satisfy.")
    allowed_tools: Set[str] = Field(default_factory=set, description="Set of tool names this agent is authorized to use.")
    allowed_providers: Set[str] = Field(default_factory=set, description="Allowed provider preferences (e.g., 'groq', 'gemini').")
    required_permissions: Set[AgentPermission] = Field(default_factory=set, description="Security permissions required.")
    
    priority: int = Field(default=10, description="Selection priority (higher values selected first during lookup).")
    max_steps: int = Field(default=10, description="Maximum execution loop steps per task.")
    max_tool_calls: int = Field(default=25, description="Maximum allowed tool invocations per session.")
    max_runtime_seconds: float = Field(default=300.0, description="Maximum execution timeout in seconds.")
    
    cost_preference: str = Field(
        default="balanced",
        description="Inference cost profile: 'low', 'balanced', 'performance'.",
    )
    reasoning_level: str = Field(
        default="medium",
        description="Reasoning depth required: 'low', 'medium', 'high', 'deep'.",
    )

    # OpenRouter model-governance hints. These are preferences only; they never
    # create tool/financial/owner authority.
    model_profile: str = Field(
        default="auto",
        description="Model routing profile. 'auto' delegates selection to the capability router.",
    )
    max_model_cost_per_request_usd: float = Field(
        default=0.03,
        ge=0.0,
        description="Soft inference-spend ceiling for a single model request.",
    )
    allow_free_model_first: bool = Field(
        default=True,
        description="Whether low-cost routing may try OpenRouter's free router before paid fallbacks.",
    )
    tags: List[str] = Field(default_factory=list, description="Search and taxonomy tags.")

    model_config = ConfigDict(use_enum_values=True)


# ============================================================================
# 3. Execution Budget Model
# ============================================================================

class ExecutionBudget(BaseModel):
    """
    Resource consumption limits enforced for a specific execution context.
    """
    max_steps: int = Field(default=10)
    max_tool_calls: int = Field(default=20)
    max_tokens: int = Field(default=100000)
    max_cost_usd: float = Field(default=2.00)
    timeout_seconds: float = Field(default=300.0)
    
    current_steps: int = Field(default=0)
    current_tool_calls: int = Field(default=0)
    current_tokens: int = Field(default=0)
    current_cost_usd: float = Field(default=0.0)

    def is_exhausted(self) -> bool:
        return (
            self.current_steps >= self.max_steps or
            self.current_tool_calls >= self.max_tool_calls or
            self.current_tokens >= self.max_tokens or
            self.current_cost_usd >= self.max_cost_usd
        )


# ============================================================================
# 4. Agent Context Model
# ============================================================================

class AgentContext(BaseModel):
    """
    Isolated, unified execution context passed into an agent during execution.
    Contains environment state, user context, runtime dependencies, and memory.
    """
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = Field(default="local_user")
    conversation_id: str = Field(default="conv_default")
    workspace_path: Optional[str] = Field(default=None, description="Absolute local project workspace directory.")
    
    task: str = Field(..., description="The high-level user goal or prompt.")
    execution_plan: Optional[Any] = Field(default=None, description="Structured plan generated by TaskPlanner.")
    planner_output: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    available_tools: List[str] = Field(default_factory=list)
    available_providers: List[str] = Field(default_factory=list)
    granted_permissions: Set[AgentPermission] = Field(default_factory=set)
    
    shared_state: Dict[str, Any] = Field(default_factory=dict, description="Transient state shared across multi-agent steps.")
    artifacts: List[Dict[str, Any]] = Field(default_factory=list, description="Code, documents, or reports generated.")
    execution_budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    runtime_metadata: Dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ============================================================================
# 5. Standardized Agent Result Model
# ============================================================================

class AgentResult(BaseModel):
    """
    Standardized result contract returned by all NapsterTec agents.
    Strictly prohibits arbitrary dictionary responses.
    """
    success: bool = Field(..., description="Whether the agent achieved its assigned goal.")
    agent_name: str = Field(..., description="Technical identifier of the agent that produced this result.")
    session_id: str = Field(..., description="Unique session trace ID.")
    
    final_output: str = Field(default="", description="Polished response text or report for the user.")
    execution_summary: str = Field(default="", description="High-level summary of steps executed.")
    
    messages: List[Dict[str, Any]] = Field(default_factory=list, description="Telemetry logs and agent thoughts.")
    artifacts: List[Dict[str, Any]] = Field(default_factory=list, description="Output files, code, or structured entities.")
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list, description="Audit log of all tools executed.")
    
    duration_seconds: float = Field(default=0.0)
    token_usage: Dict[str, int] = Field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    cost_estimate_usd: float = Field(default=0.0)
    
    warnings: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(use_enum_values=True)