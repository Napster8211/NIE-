"""
NapsterTec AI - Shared Agent Artifacts Foundation
Module: app/schemas/shared_artifacts.py
"""
from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field, model_validator

class BaseArtifact(BaseModel):
    artifact_id: str = Field(..., description="Permanent identifier for this artifact.")
    artifact_type: str = Field(..., description="The concrete type of artifact.")
    lead_id: str = Field(..., description="The ID of the target business.")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_run_id: str = Field(..., description="The session or trace ID.")
    version: int = Field(default=1, description="Version number for historical tracking.")
    
class Evidence(BaseModel):
    source: str = Field(...)
    verification_method: str = Field(...)
    confidence: float = Field(...)
    detail: str = Field(...)

class WebsiteAgentContext(BaseModel):
    lead_id: str
    business_name: str
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    category: Optional[str] = None
    place_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'WebsiteAgentContext':
        suspicious = ["act as", "find me", "analyze", "discover", "agent", "prompt", "i need you to"]
        lower_name = self.business_name.lower()
        if any(phrase in lower_name for phrase in suspicious) or len(self.business_name) > 80:
            raise ValueError("Context Contamination Detected: User prompt leaked into structured Agent Context.")
        return self

class RecommendedService(BaseModel):
    service_name: str = Field(...)
    evidence_chain: List[str] = Field(...)
    confidence: str = Field(..., description="High, Medium, Low")

class OpportunityArtifact(BaseArtifact):
    artifact_type: str = "OpportunityArtifact"
    opportunity_level: str = Field(..., description="Very High, High, Medium, Low, Very Low")
    verified_issues: List[str] = Field(default_factory=list)
    business_signals: List[Dict[str, Any]] = Field(default_factory=list)
    opportunity_drivers: List[str] = Field(default_factory=list)
    recommended_services: List[RecommendedService] = Field(default_factory=list)
    recommended_next_step: str = Field(...)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class OpportunityAgentContext(BaseModel):
    lead_id: str
    business_identity: Dict[str, Any]
    website_status: str
    technology: List[Dict[str, Any]] = Field(default_factory=list)
    seo_findings: Dict[str, Any] = Field(default_factory=dict)
    accessibility_findings: Dict[str, Any] = Field(default_factory=dict)
    business_signals: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'OpportunityAgentContext':
        suspicious = ["act as", "evaluate", "opportunity", "agent", "prompt"]
        b_name = self.business_identity.get("business_name", "").lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Opportunity Context.")
        return self

class SolutionModule(BaseModel):
    name: str = Field(...)
    justification: str = Field(...)

class SolutionFeature(BaseModel):
    name: str = Field(...)
    linked_need: str = Field(...)

class SolutionIntegration(BaseModel):
    name: str = Field(...)
    purpose: str = Field(...)

class ImplementationPhase(BaseModel):
    phase_number: int = Field(...)
    name: str = Field(...)
    focus: str = Field(...)

class BusinessSolutionArtifact(BaseArtifact):
    artifact_type: str = "BusinessSolutionArtifact"
    solution_type: str = Field(..., description="e.g., Restaurant Management Platform")
    complexity: str = Field(..., description="Very Small, Small, Medium, Large, Enterprise")
    
    modules: List[SolutionModule] = Field(default_factory=list)
    features: List[SolutionFeature] = Field(default_factory=list)
    integrations: List[SolutionIntegration] = Field(default_factory=list)
    technology_stack: List[str] = Field(default_factory=list)
    implementation_phases: List[ImplementationPhase] = Field(default_factory=list)
    business_benefits: List[str] = Field(default_factory=list)
    
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class SolutionAgentContext(BaseModel):
    lead_id: str
    business_name: str
    category: str
    opportunity_level: str
    verified_issues: List[str] = Field(default_factory=list)
    recommended_services: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'SolutionAgentContext':
        suspicious = ["design", "solution", "architect", "evaluate", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Solution Context.")
        return self

class ProposalDeliverable(BaseModel):
    name: str = Field(...)
    description: str = Field(...)
    included: bool = Field(..., description="True if included, False if excluded or future phase.")

class ProposalPhase(BaseModel):
    phase_number: int = Field(...)
    name: str = Field(...)
    description: str = Field(...)

class ProposalRisk(BaseModel):
    description: str = Field(...)
    mitigation: str = Field(...)

class ProposalAssumption(BaseModel):
    description: str = Field(...)

class ProposalArtifact(BaseArtifact):
    artifact_type: str = "ProposalArtifact"
    proposal_type: str = Field(..., description="e.g., Digital Transformation Proposal")
    
    executive_summary: str = Field(...)
    business_context: str = Field(...)
    verified_problems: List[str] = Field(default_factory=list)
    solution_overview: str = Field(...)
    
    deliverables: List[ProposalDeliverable] = Field(default_factory=list)
    implementation_phases: List[ProposalPhase] = Field(default_factory=list)
    business_benefits: List[str] = Field(default_factory=list)
    roi_narrative: str = Field(...)
    
    assumptions: List[ProposalAssumption] = Field(default_factory=list)
    risks: List[ProposalRisk] = Field(default_factory=list)
    
    call_to_action: str = Field(...)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class ProposalAgentContext(BaseModel):
    lead_id: str
    business_name: str
    category: str
    verified_issues: List[str] = Field(default_factory=list)
    solution_type: str
    modules: List[Dict[str, Any]] = Field(default_factory=list)
    features: List[Dict[str, Any]] = Field(default_factory=list)
    benefits: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'ProposalAgentContext':
        suspicious = ["generate", "proposal", "evaluate", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Proposal Context.")
        return self

class UserRole(BaseModel):
    role_name: str = Field(...)
    permissions: List[str] = Field(...)
    primary_interface: str = Field(...)

class UserJourney(BaseModel):
    journey_name: str = Field(...)
    actor: str = Field(...)
    steps: List[str] = Field(...)

class DashboardSection(BaseModel):
    section_name: str = Field(...)
    widgets: List[str] = Field(...)

class ComponentSpec(BaseModel):
    name: str = Field(...)
    purpose: str = Field(...)
    reusable: bool = Field(default=True)

class VisualizationArtifact(BaseArtifact):
    artifact_type: str = "VisualizationArtifact"
    
    information_architecture: List[str] = Field(default_factory=list)
    page_hierarchy: List[str] = Field(default_factory=list)
    navigation_strategy: Dict[str, Any] = Field(default_factory=dict)
    
    user_roles: List[UserRole] = Field(default_factory=list)
    user_journeys: List[UserJourney] = Field(default_factory=list)
    
    dashboard_architecture: List[DashboardSection] = Field(default_factory=list)
    component_architecture: List[ComponentSpec] = Field(default_factory=list)
    
    mobile_strategy: Dict[str, Any] = Field(default_factory=dict)
    accessibility_strategy: List[str] = Field(default_factory=list)
    design_system: Dict[str, Any] = Field(default_factory=dict)
    ux_recommendations: List[str] = Field(default_factory=list)
    
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class VisualizationAgentContext(BaseModel):
    lead_id: str
    business_name: str
    category: str
    solution_type: str
    modules: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'VisualizationAgentContext':
        suspicious = ["visualize", "design", "wireframe", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Visualization Context.")
        return self

class TechnologyDecision(BaseModel):
    technology: str = Field(...)
    reason: str = Field(...)
    confidence: float = Field(..., ge=0, le=1)
    alternative_options: List[str] = Field(default_factory=list)
    trade_offs: str = Field(...)

class TechnicalArchitectureArtifact(BaseArtifact):
    artifact_type: str = "TechnicalArchitectureArtifact"
    
    architecture_pattern: str = Field(...)
    frontend_architecture: Dict[str, Any] = Field(default_factory=dict)
    backend_architecture: Dict[str, Any] = Field(default_factory=dict)
    database_architecture: Dict[str, Any] = Field(default_factory=dict)
    api_architecture: Dict[str, Any] = Field(default_factory=dict)
    
    authentication_strategy: Dict[str, Any] = Field(default_factory=dict)
    authorization_strategy: Dict[str, Any] = Field(default_factory=dict)
    security_architecture: List[str] = Field(default_factory=list)
    
    integrations: List[Dict[str, Any]] = Field(default_factory=list)
    scalability_architecture: Dict[str, Any] = Field(default_factory=dict)
    deployment_architecture: Dict[str, Any] = Field(default_factory=dict)
    testing_architecture: List[str] = Field(default_factory=list)
    observability_strategy: Dict[str, Any] = Field(default_factory=dict)
    
    technology_decisions: List[TechnologyDecision] = Field(default_factory=list)
    
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class TechnicalAgentContext(BaseModel):
    lead_id: str
    business_name: str
    category: str
    solution_type: str
    modules: List[str] = Field(default_factory=list)
    integrations: List[str] = Field(default_factory=list)
    user_roles: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'TechnicalAgentContext':
        suspicious = ["architect", "technical", "design", "evaluate", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Technical Context.")
        return self

class CodeFile(BaseModel):
    path: str = Field(...)
    purpose: str = Field(...)
    action: str = Field(..., description="Generated, Updated, or Preserved")

class ImplementationArtifact(BaseArtifact):
    artifact_type: str = "ImplementationArtifact"
    
    files_generated: List[CodeFile] = Field(default_factory=list)
    files_updated: List[CodeFile] = Field(default_factory=list)
    
    modules_created: List[str] = Field(default_factory=list)
    components_created: List[str] = Field(default_factory=list)
    apis_created: List[str] = Field(default_factory=list)
    tests_generated: List[str] = Field(default_factory=list)
    documentation_generated: List[str] = Field(default_factory=list)
    
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class CodingAgentContext(BaseModel):
    lead_id: str
    business_name: str
    architecture_pattern: str
    frontend_stack: str
    backend_stack: str
    database: str
    modules: List[str] = Field(default_factory=list)
    pages: List[str] = Field(default_factory=list)
    api_architecture: Dict[str, Any] = Field(default_factory=dict)
    
    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'CodingAgentContext':
        suspicious = ["implement", "code", "generate", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Coding Context.")
        return self

class ReviewFinding(BaseModel):
    category: str = Field(...)
    severity: str = Field(...)
    affected_files: List[str] = Field(default_factory=list)
    evidence: str = Field(...)
    recommendation: str = Field(...)
    required_action: str = Field(...)

class ReviewScorecard(BaseModel):
    architecture_compliance: str = Field(...)
    security: str = Field(...)
    performance: str = Field(...)
    accessibility: str = Field(...)
    maintainability: str = Field(...)
    documentation: str = Field(...)
    testing: str = Field(...)
    deployment_readiness: str = Field(...)

class ReviewArtifact(BaseArtifact):
    artifact_type: str = "ReviewArtifact"
    approval_status: str = Field(..., description="Approved, Approved with Warnings, Changes Required, Blocked")
    findings: List[ReviewFinding] = Field(default_factory=list)
    scorecard: ReviewScorecard = Field(...)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class ReviewAgentContext(BaseModel):
    lead_id: str
    business_name: str
    architecture_pattern: str
    implemented_files: int
    implemented_components: int
    implemented_apis: int
    
    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'ReviewAgentContext':
        suspicious = ["review", "audit", "govern", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Review Context.")
        return self

class HealthCheckResult(BaseModel):
    service: str = Field(...)
    status: str = Field(...)
    latency_ms: int = Field(...)

class SmokeTestResult(BaseModel):
    flow: str = Field(...)
    passed: bool = Field(...)

class PerformanceMetrics(BaseModel):
    load_time_ms: int = Field(...)
    fcp_ms: int = Field(...)
    lcp_ms: int = Field(...)
    bundle_size_kb: int = Field(...)

class PreviewPackage(BaseModel):
    preview_url: str = Field(...)
    deployment_url: str = Field(...)
    homepage_screenshot_ref: str = Field(...)
    dashboard_screenshot_ref: str = Field(...)
    deployment_timestamp: str = Field(...)

class DeploymentArtifact(BaseArtifact):
    artifact_type: str = "DeploymentArtifact"
    provider: str = Field(...)
    environment: str = Field(...)
    preview_package: PreviewPackage = Field(...)
    deployment_status: str = Field(...)
    health_checks: List[HealthCheckResult] = Field(default_factory=list)
    smoke_tests: List[SmokeTestResult] = Field(default_factory=list)
    performance: PerformanceMetrics = Field(...)
    warnings: List[str] = Field(default_factory=list)
    rollback_reference: Optional[str] = Field(default=None)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class DeploymentAgentContext(BaseModel):
    lead_id: str
    business_name: str
    frontend_stack: str
    backend_stack: str
    approval_status: str
    
    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'DeploymentAgentContext':
        suspicious = ["deploy", "host", "preview", "launch", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Deployment Context.")
        return self

class ContactValidation(BaseModel):
    has_website: bool
    has_email: bool
    has_phone: bool
    has_social: bool
    missing_critical: List[str] = Field(default_factory=list)

class ChannelStrategy(BaseModel):
    primary_channel: str
    secondary_channel: Optional[str] = None
    reasoning: str

class PersonalizationSummary(BaseModel):
    business_name: str
    industry: str
    verified_pain_points: List[str] = Field(default_factory=list)
    value_proposition: str
    demo_url: str

class CRMStatus(BaseModel):
    current_stage: str
    previous_stage: str
    last_updated: str
    next_action: str

class ClientAcquisitionArtifact(BaseArtifact):
    artifact_type: str = "ClientAcquisitionArtifact"
    contact_validation: ContactValidation
    channel_strategy: ChannelStrategy
    personalization_summary: PersonalizationSummary
    crm_status: CRMStatus
    follow_up_strategy: List[str] = Field(default_factory=list)
    approval_required: bool = True
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class AcquisitionAgentContext(BaseModel):
    lead_id: str
    business_name: str
    category: str
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    verified_issues: List[str] = Field(default_factory=list)
    solution_type: str
    preview_url: str
    deployment_status: str

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'AcquisitionAgentContext':
        suspicious = ["send email", "contact client", "message", "agent", "prompt", "i need you"]
        b_name = self.business_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Acquisition Context.")
        return self

class ContentCampaign(BaseModel):
    name: str = Field(...)
    target_audience: str = Field(...)
    objective: str = Field(...)
    focus_areas: List[str] = Field(default_factory=list)

class ContentCalendarEntry(BaseModel):
    day: str = Field(...)
    frequency: str = Field(...)
    format: str = Field(...)
    theme: str = Field(...)
    recommended_platforms: List[str] = Field(default_factory=list)

class ContentArtifact(BaseArtifact):
    artifact_type: str = "ContentArtifact"
    business_objective: str = Field(...)
    target_audience: List[str] = Field(default_factory=list)
    content_pillars: List[str] = Field(default_factory=list)
    recommended_formats: List[str] = Field(default_factory=list)
    campaigns: List[ContentCampaign] = Field(default_factory=list)
    platform_recommendations: List[str] = Field(default_factory=list)
    calendar: List[ContentCalendarEntry] = Field(default_factory=list)
    brand_alignment: List[str] = Field(default_factory=list)
    publishing_priority: str = Field(...)
    future_dependencies: List[str] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class ContentAgentContext(BaseModel):
    company_id: str
    company_name: str
    active_projects: List[str] = Field(default_factory=list)
    recent_deployments: List[str] = Field(default_factory=list)
    crm_insights: str = Field(...)
    brand_tone: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'ContentAgentContext':
        suspicious = ["write a post", "draft", "tweet", "caption", "agent", "prompt", "i need you"]
        b_name = self.company_name.lower()
        if any(phrase in b_name for phrase in suspicious) or len(b_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Content Context.")
        return self

class MediaRequirement(BaseModel):
    type: str = Field(...)
    description: str = Field(...)
    purpose: str = Field(...)

class PublishingRecommendation(BaseModel):
    best_window: str = Field(...)
    timezone: str = Field(...)
    platform_priority: int = Field(...)

class SocialPost(BaseModel):
    platform: str = Field(...)
    business_objective: str = Field(...)
    audience: str = Field(...)
    headline: str = Field(...)
    message: str = Field(...)
    call_to_action: str = Field(...)
    media_requirements: List[MediaRequirement] = Field(default_factory=list)
    hashtags: List[str] = Field(default_factory=list)
    publishing_recommendation: PublishingRecommendation = Field(...)

class SocialArtifact(BaseArtifact):
    artifact_type: str = "SocialArtifact"
    posts: List[SocialPost] = Field(default_factory=list)
    approval_status: str = Field(default="Awaiting CTO Approval")
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class SocialAgentContext(BaseModel):
    company_id: str
    business_objective: str
    campaigns: List[Dict[str, Any]] = Field(default_factory=list)
    formats: List[str] = Field(default_factory=list)
    brand_tone: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'SocialAgentContext':
        suspicious = ["publish", "tweet", "send post", "agent", "prompt", "i need you"]
        if any(phrase in self.company_id.lower() for phrase in suspicious) or len(self.company_id) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Social Context.")
        return self

class PublishingSequenceStep(BaseModel):
    day: int = Field(...)
    action: str = Field(...)
    channel: str = Field(...)
    asset_reference: str = Field(...)

class CampaignArtifact(BaseArtifact):
    artifact_type: str = "CampaignArtifact"
    campaign_name: str = Field(...)
    business_objective: str = Field(...)
    target_audience: List[str] = Field(default_factory=list)
    channels: List[str] = Field(default_factory=list)
    campaign_timeline: str = Field(...)
    content_sequence: List[PublishingSequenceStep] = Field(default_factory=list)
    assets_coordinated: List[str] = Field(default_factory=list)
    kpis: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    approval_status: str = Field(default="Awaiting CTO Approval")
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class CampaignAgentContext(BaseModel):
    company_id: str
    target_campaign_name: str
    business_objective: str
    target_audience: List[str] = Field(default_factory=list)
    channels: List[str] = Field(default_factory=list)
    available_content: int
    available_social_posts: int

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'CampaignAgentContext':
        suspicious = ["publish", "schedule", "post this", "agent", "prompt", "i need you"]
        if any(phrase in self.target_campaign_name.lower() for phrase in suspicious) or len(self.target_campaign_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Campaign Context.")
        return self

class PlatformPerformance(BaseModel):
    platform: str = Field(...)
    impressions: int = Field(...)
    engagement_rate: str = Field(...)
    leads_generated: int = Field(...)
    rank: int = Field(...)

class AudiencePerformance(BaseModel):
    segment: str = Field(...)
    engagement_score: str = Field(...)
    conversion_rate: str = Field(...)
    quality_rating: str = Field(...)

class BusinessImpact(BaseModel):
    qualified_leads: int = Field(...)
    meetings_scheduled: int = Field(...)
    proposals_requested: int = Field(...)
    revenue_pipeline_generated: str = Field(...)
    marketing_roi: str = Field(...)

class OptimizationRecommendation(BaseModel):
    category: str = Field(...)
    recommendation: str = Field(...)
    evidence: str = Field(...)

class MarketingAnalyticsArtifact(BaseArtifact):
    artifact_type: str = "MarketingAnalyticsArtifact"
    campaign_reference: str = Field(...)
    campaign_performance: str = Field(...)
    platform_performance: List[PlatformPerformance] = Field(default_factory=list)
    audience_performance: List[AudiencePerformance] = Field(default_factory=list)
    business_impact: BusinessImpact = Field(...)
    optimization_recommendations: List[OptimizationRecommendation] = Field(default_factory=list)
    future_opportunities: List[str] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class MarketingAnalyticsAgentContext(BaseModel):
    company_id: str
    campaign_name: str
    active_channels: List[str] = Field(default_factory=list)
    target_audience: List[str] = Field(default_factory=list)
    simulated_telemetry: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'MarketingAnalyticsAgentContext':
        suspicious = ["publish", "schedule", "post this", "agent", "prompt", "i need you"]
        if any(phrase in self.campaign_name.lower() for phrase in suspicious) or len(self.campaign_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Analytics Context.")
        return self

class PublishingPlatformResult(BaseModel):
    platform: str = Field(...)
    post_id: str = Field(...)
    published_url: str = Field(...)
    status: str = Field(...)
    retries: int = Field(...)
    validation_passed: bool = Field(...)
    timestamp: str = Field(...)

class PublishingArtifact(BaseArtifact):
    artifact_type: str = "PublishingArtifact"
    campaign_reference: str = Field(...)
    platforms_published: List[str] = Field(default_factory=list)
    results: List[PublishingPlatformResult] = Field(default_factory=list)
    total_retries: int = Field(...)
    overall_status: str = Field(...)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class PublishingAgentContext(BaseModel):
    company_id: str
    campaign_name: str
    approval_status: str
    platforms_target: List[str] = Field(default_factory=list)
    assets_ready: bool

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'PublishingAgentContext':
        suspicious = ["what is", "help me", "agent", "prompt", "i need you"]
        if any(phrase in self.campaign_name.lower() for phrase in suspicious) or len(self.campaign_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Publishing Context.")
        return self

class MeetingPreparation(BaseModel):
    business_summary: str = Field(...)
    known_pain_points: List[str] = Field(default_factory=list)
    recommended_solution: str = Field(...)
    demo_url: str = Field(...)
    questions_to_ask: List[str] = Field(default_factory=list)
    objections_to_expect: List[str] = Field(default_factory=list)
    suggested_talking_points: List[str] = Field(default_factory=list)
    meeting_goal: str = Field(...)

class SalesArtifact(BaseArtifact):
    artifact_type: str = "SalesArtifact"
    opportunity_summary: str = Field(...)
    buying_intent: str = Field(...)
    buying_intent_reasoning: str = Field(...)
    relationship_health: str = Field(...)
    priority: str = Field(...)
    next_best_action: str = Field(...)
    next_action_reasoning: str = Field(...)
    meeting_preparation: MeetingPreparation = Field(...)
    pipeline_stage: str = Field(...)
    estimated_deal_value: str = Field(...)
    risk_factors: List[str] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class SalesAgentContext(BaseModel):
    lead_id: str
    business_name: str
    category: str
    verified_issues: List[str] = Field(default_factory=list)
    solution_type: str
    preview_url: str

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'SalesAgentContext':
        suspicious = ["call client", "email prospect", "agent", "prompt", "i need you"]
        if any(phrase in self.business_name.lower() for phrase in suspicious) or len(self.business_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Sales Context.")
        return self

class PipelineHealth(BaseModel):
    open_opportunities: int = Field(...)
    qualified_deals: int = Field(...)
    negotiations: int = Field(...)
    won_deals_ytd: int = Field(...)
    lost_deals_ytd: int = Field(...)
    average_deal_size: str = Field(...)
    pipeline_value: str = Field(...)
    pipeline_health_status: str = Field(...)

class RevenueForecast(BaseModel):
    weekly_revenue: str = Field(...)
    monthly_revenue: str = Field(...)
    quarterly_revenue: str = Field(...)
    annual_revenue: str = Field(...)
    recurring_revenue_mrr: str = Field(...)
    confidence_level: str = Field(...)

class IndustryPerformance(BaseModel):
    industry: str = Field(...)
    revenue_generated: str = Field(...)
    win_rate: str = Field(...)
    avg_deal_size: str = Field(...)
    growth_trend: str = Field(...)

class RevenueRisk(BaseModel):
    risk_type: str = Field(...)
    description: str = Field(...)
    potential_loss_value: str = Field(...)
    mitigation_strategy: str = Field(...)

class GrowthOpportunity(BaseModel):
    opportunity_type: str = Field(...)
    description: str = Field(...)
    expected_impact: str = Field(...)

class ExecutiveKPIs(BaseModel):
    total_pipeline_value: str = Field(...)
    monthly_forecast_amount: str = Field(...)
    win_rate_percentage: str = Field(...)
    average_deal_value: str = Field(...)
    revenue_at_risk_amount: str = Field(...)

class RevenueArtifact(BaseArtifact):
    artifact_type: str = "RevenueArtifact"
    revenue_summary: str = Field(...)
    pipeline_health: PipelineHealth = Field(...)
    revenue_forecast: RevenueForecast = Field(...)
    industry_performance: List[IndustryPerformance] = Field(default_factory=list)
    revenue_risks: List[RevenueRisk] = Field(default_factory=list)
    growth_opportunities: List[GrowthOpportunity] = Field(default_factory=list)
    executive_kpis: ExecutiveKPIs = Field(...)
    strategic_recommendations: List[str] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class RevenueAgentContext(BaseModel):
    company_id: str
    active_pipeline_deals: int
    total_pipeline_value: float
    simulated_crm_sync: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'RevenueAgentContext':
        suspicious = ["modify deal", "change value", "agent", "prompt", "i need you"]
        if any(phrase in self.company_id.lower() for phrase in suspicious) or len(self.company_id) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Revenue Context.")
        return self

class CommunicationIdentity(BaseModel):
    communication_id: str
    conversation_id: str
    thread_id: str
    correlation_id: str
    workflow_id: str
    recipient_id: str
    lead_id: str
    proposal_id: str
    campaign_id: str
    deployment_id: str
    message_version: str
    parent_message_id: Optional[str] = None

class MonitoredEvent(BaseModel):
    event_type: str
    timestamp: str
    details: str

class TrackingInfo(BaseModel):
    delivery_status: str
    opened: bool
    clicked: bool
    replied: bool
    tracking_enabled: bool
    follow_up_scheduled: str
    proposal_viewed: bool = False
    proposal_downloaded: bool = False
    demo_viewed: bool = False
    demo_duration: str = "0m"
    meetings_requested: int = 0
    last_interaction: str = ""

class CommunicationArtifact(BaseArtifact):
    artifact_type: str = "CommunicationArtifact"
    identity: Optional[CommunicationIdentity] = None
    recipient: str
    channel: str
    purpose: str
    template_used: str
    personalization_summary: Dict[str, Any] = Field(default_factory=dict)
    tracking_info: TrackingInfo
    crm_updated: bool = Field(default=True)
    crm_timeline_events: List[str] = Field(default_factory=list)
    published_events: List[MonitoredEvent] = Field(default_factory=list)
    subscriber_notifications: List[str] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class CommunicationAgentContext(BaseModel):
    lead_id: str
    business_name: str
    category: str
    recipient_contact: str
    recommended_channel: str
    preview_url: str
    cto_approved: bool
    deployment_successful: bool

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'CommunicationAgentContext':
        suspicious = ["send now", "bypass", "agent", "prompt", "i need you"]
        if any(phrase in self.business_name.lower() for phrase in suspicious) or len(self.business_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Communication Context.")
        return self

class HealthScore(BaseModel):
    score: int = Field(...)
    adoption_level: str = Field(...)
    feature_usage: str = Field(...)
    communication_frequency: str = Field(...)
    engagement_trend: str = Field(...)
    renewal_likelihood: str = Field(...)
    confidence_score: float = Field(...)
    reasoning: str = Field(...)

class OnboardingStatus(BaseModel):
    status: str = Field(...)
    deployment_completed: bool = Field(...)
    customer_training: bool = Field(...)
    admin_account_created: bool = Field(...)
    first_login: bool = Field(...)
    initial_configuration: bool = Field(...)
    documentation_delivered: bool = Field(...)
    next_step: str = Field(...)

class ChurnRisk(BaseModel):
    level: str = Field(...)
    reasoning: str = Field(...)

class ExpansionOpportunity(BaseModel):
    recommendation: str = Field(...)
    business_reasoning: str = Field(...)

class CustomerSuccessArtifact(BaseArtifact):
    artifact_type: str = "CustomerSuccessArtifact"
    customer_summary: str = Field(...)
    health_score: HealthScore = Field(...)
    onboarding_status: OnboardingStatus = Field(...)
    churn_risk: ChurnRisk = Field(...)
    expansion_opportunities: List[ExpansionOpportunity] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)
    timeline_summary: str = Field(...)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class CustomerSuccessAgentContext(BaseModel):
    lead_id: str
    business_name: str
    deployment_status: str
    sales_stage: str
    crm_timeline_events: List[str] = Field(default_factory=list)
    communication_history_count: int

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'CustomerSuccessAgentContext':
        suspicious = ["evaluate", "check health", "agent", "prompt", "i need you"]
        if any(phrase in self.business_name.lower() for phrase in suspicious) or len(self.business_name) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Customer Success Context.")
        return self

class DepartmentHealth(BaseModel):
    department: str = Field(...)
    health_score: int = Field(...)
    trend: str = Field(...)
    operational_status: str = Field(...)
    confidence: str = Field(default="90%")
    agent_count: int = Field(default=0)
    workflow_count: int = Field(default=0)
    success_rate: str = Field(default="100%")
    failure_rate: str = Field(default="0%")
    average_execution_time: str = Field(default="0s")
    resource_utilization: str = Field(default="0%")
    department_load: str = Field(default="Low")
    last_activity: str = Field(default="Just now")
    operational_recommendation: str = Field(default="No Action Required")
    reasoning: str = Field(...)

class WorkflowStatus(BaseModel):
    workflow_name: str = Field(...)
    health_score: int = Field(default=100)
    state: str = Field(...)
    trend: str = Field(default="Stable")
    execution_count: int = Field(...)
    blocked_count: int = Field(...)
    average_runtime: str = Field(default="0s")
    queue_length: int = Field(default=0)
    failure_rate: str = Field(default="0%")
    current_state: str = Field(default="Healthy")
    recommendation: str = Field(default="No Action Required")

class CompanyHealth(BaseModel):
    score: int
    status: str
    trend: str
    confidence: str
    reasoning: List[str]

class DepartmentRanking(BaseModel):
    rank: int
    department: str
    score: int

class WorkflowRanking(BaseModel):
    rank: int
    workflow_name: str
    score: int

class ExecutiveTrend(BaseModel):
    metric: str
    trend: str

class OperationalInsight(BaseModel):
    insight_type: str
    description: str
    recommendation: str

class ExecutiveRecommendation(BaseModel):
    department: str
    recommendation: str
    reason: str
    confidence: str
    priority: str

class AgentPerformanceMetric(BaseModel):
    agent_name: str = Field(...)
    execution_count: int = Field(...)
    success_rate: str = Field(...)
    average_execution_time: str = Field(...)
    status: str = Field(...)

class OperationalKPIs(BaseModel):
    active_projects: int = Field(...)
    deployments_this_week: int = Field(...)
    engineering_velocity: str = Field(...)
    average_proposal_time: str = Field(...)
    customer_retention_rate: str = Field(...)

class Bottleneck(BaseModel):
    description: str = Field(...)
    severity: str = Field(...)
    affected_department: str = Field(...)
    recommended_action: str = Field(...)

class ResourceUtilization(BaseModel):
    overall_capacity_used: str = Field(...)
    highest_load_department: str = Field(...)
    idle_capacity: str = Field(...)

class SLAMetric(BaseModel):
    process: str = Field(...)
    target_time: str = Field(...)
    actual_time: str = Field(...)
    compliance: str = Field(...)

class BusinessOperationsArtifact(BaseArtifact):
    artifact_type: str = "BusinessOperationsArtifact"
    company_health: CompanyHealth = Field(...)
    departments: List[DepartmentHealth] = Field(default_factory=list)
    department_rankings: List[DepartmentRanking] = Field(default_factory=list)
    workflows: List[WorkflowStatus] = Field(default_factory=list)
    workflow_rankings: List[WorkflowRanking] = Field(default_factory=list)
    agents: List[AgentPerformanceMetric] = Field(default_factory=list)
    kpis: OperationalKPIs = Field(...)
    executive_trends: List[ExecutiveTrend] = Field(default_factory=list)
    operational_insights: List[OperationalInsight] = Field(default_factory=list)
    bottlenecks: List[Bottleneck] = Field(default_factory=list)
    resource_utilization: ResourceUtilization = Field(...)
    slas: List[SLAMetric] = Field(default_factory=list)
    operational_risks: List[str] = Field(default_factory=list)
    executive_recommendations: List[ExecutiveRecommendation] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class BusinessOperationsAgentContext(BaseModel):
    company_id: str
    total_artifacts_registered: int
    total_active_agents: int
    total_events_processed: int
    monitoring_status: str

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'BusinessOperationsAgentContext':
        suspicious = ["do work", "re-architect", "agent", "prompt", "i need you"]
        if any(phrase in self.company_id.lower() for phrase in suspicious) or len(self.company_id) > 80:
            pass 
        return self

class FinancialHealth(BaseModel):
    score: int = Field(...)
    trend: str = Field(...)
    confidence: str = Field(...)
    operating_margin: str = Field(...)
    gross_margin: str = Field(...)
    net_margin: str = Field(...)
    revenue_growth: str = Field(...)
    expense_growth: str = Field(...)
    cash_position: str = Field(...)
    liquidity: str = Field(...)
    reasoning: str = Field(...)

class RevenueSummary(BaseModel):
    total_expected_revenue: str = Field(...)
    recurring_revenue: str = Field(...)
    implementation_revenue: str = Field(...)
    annual_forecast: str = Field(...)

class ExpenseSummary(BaseModel):
    total_expenses: str = Field(...)
    infrastructure_costs: str = Field(...)
    marketing_costs: str = Field(...)
    development_costs: str = Field(...)
    ai_provider_costs: str = Field(...)
    future_payroll_allocations: str = Field(...)

class Runway(BaseModel):
    monthly_burn: str = Field(...)
    cash_runway: str = Field(...)
    safe_operating_window: str = Field(...)
    expansion_capacity: str = Field(...)
    hiring_capacity: str = Field(...)
    infrastructure_capacity: str = Field(...)
    investment_capacity: str = Field(...)
    confidence_score: str = Field(...)

class BudgetStatus(BaseModel):
    department: str = Field(...)
    allocated: str = Field(...)
    spent: str = Field(...)
    remaining: str = Field(...)
    forecast: str = Field(...)
    variance: str = Field(...)
    budget_health: str = Field(...)

class ROIAnalysis(BaseModel):
    investment_area: str = Field(...)
    cost: str = Field(...)
    generated_revenue: str = Field(...)
    roi_percentage: str = Field(...)
    recommendation: str = Field(...)

class FinancialRisk(BaseModel):
    risk_type: str = Field(...)
    level: str = Field(...)
    description: str = Field(...)
    reasoning: str = Field(...)

class FinanceArtifact(BaseArtifact):
    artifact_type: str = "FinanceArtifact"
    executive_summary: str = Field(...)
    financial_health: FinancialHealth = Field(...)
    revenue_summary: RevenueSummary = Field(...)
    expense_summary: ExpenseSummary = Field(...)
    runway: Runway = Field(...)
    budgets: List[BudgetStatus] = Field(default_factory=list)
    roi_analysis: List[ROIAnalysis] = Field(default_factory=list)
    financial_risks: List[FinancialRisk] = Field(default_factory=list)
    financial_recommendations: List[str] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class FinanceAgentContext(BaseModel):
    company_id: str
    verified_revenue_pipeline: float
    verified_expenses: float
    monitoring_status: str

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'FinanceAgentContext':
        suspicious = ["do math", "calculate bill", "agent", "prompt", "i need you"]
        if any(phrase in self.company_id.lower() for phrase in suspicious) or len(self.company_id) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Finance Context.")
        return self

class ExecutivePriority(BaseModel):
    level: str = Field(...)
    description: str = Field(...)

class DelegationRecord(BaseModel):
    delegation_id: str = Field(...)
    target_agent: str = Field(...)
    objective: str = Field(...)
    status: str = Field(...)

class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CONSUMED = "CONSUMED"

class ApprovalType(str, Enum):
    MATERIALIZATION = "MATERIALIZATION"
    STRATEGY_CHANGE = "STRATEGY_CHANGE"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    GENERAL = "GENERAL"

class ApprovalRequest(BaseModel):
    approval_id: str = Field(pattern=r"^app_[a-z0-9]{8,64}$")
    mission_id: str = Field(...)
    decision_id: Optional[str] = Field(default=None)
    materialization_id: Optional[str] = Field(default=None)
    execution_request_id: Optional[str] = Field(default=None)
    approval_type: ApprovalType = Field(default=ApprovalType.GENERAL)
    action: str = Field(...)
    action_fingerprint: Optional[str] = Field(default=None)
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    risk_level: str = Field(default="LOW")
    requester: str = Field(default="system")
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None
    resolution_reason: Optional[str] = None
    context_summary: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_invariants(self) -> 'ApprovalRequest':
        if self.status != ApprovalStatus.PENDING and not self.resolved_at:
            self.resolved_at = datetime.now(timezone.utc).isoformat()
        if self.status == ApprovalStatus.PENDING and self.resolved_at:
            raise ValueError("APPROVAL_PENDING_CANNOT_HAVE_RESOLVED_AT")
        return self

class ActiveAgentSession(BaseModel):
    session_id: str = Field(...)
    session_type: str = Field(default="Interactive")
    active_specialist: str = Field(default="None")
    participants: List[str] = Field(default_factory=list)
    director_supervising: bool = Field(default=True)
    context_loaded: bool = Field(default=True)
    status: str = Field(...)

class ExecutiveDecisionType(str, Enum):
    OBJECTIVE_COMPLETE = "OBJECTIVE_COMPLETE"
    FOLLOW_UP_MISSION = "FOLLOW_UP_MISSION"
    CHANGE_STRATEGY = "CHANGE_STRATEGY"
    WAIT = "WAIT"
    ESCALATE = "ESCALATE"
    STOP = "STOP"
    NO_ACTION = "NO_ACTION"
    BLOCKED = "BLOCKED"

class ExecutiveDecisionRecord(BaseModel):
    decision_id: str = Field(...)
    objective_id: str = Field(...)
    objective_version: int = Field(..., ge=1)
    mission_id: str = Field(...)
    mission_terminal_event_id: str = Field(...)
    mission_terminal_state: str = Field(...)
    decision_type: ExecutiveDecisionType = Field(...)
    evidence_artifact_ids: List[str] = Field(default_factory=list)
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
    selected_follow_up_action: Optional[Dict[str, Any]] = Field(default=None)
    authority_scope: str = Field(default="INTERNAL_COMPANY_OBJECTIVE_STATE")
    approval_required: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    strategy_version_before: int = Field(default=1, ge=1)
    strategy_version_after: int = Field(default=1, ge=1)
    objective_progress_before: float = Field(default=0.0, ge=0, le=100)
    objective_progress_after: float = Field(default=0.0, ge=0, le=100)
    zero_progress_detected: bool = Field(default=False)
    terminal: bool = Field(default=False)
    question: str = Field(default="Unknown")
    decision_mode: str = Field(default="STRATEGIC DECISION MODE")
    specialists_consulted: List[str] = Field(default_factory=list)
    decision: str = Field(...)
    reason: str = Field(...)
    evidence: str = Field(...)
    confidence: str = Field(...)
    approval_requirement: str = Field(...)
    action_executed: bool = Field(default=False)

class MutationLedger(BaseModel):
    missions_created: int = 0
    plans_created: int = 0
    decisions_created: int = 0
    materializations_created: int = 0
    execution_requests_created: int = 0
    delegations_created: int = 0
    worker_claims_created: int = 0
    artifacts_created: int = 0
    repository_writes: int = 0
    state_changing_events: int = 0
    specialist_invocations: int = 0
    auto_continue_triggers: int = 0
    external_side_effects: int = 0
    read_only_isolation_integrity: str = "PASSED"

class DirectorArtifact(BaseArtifact):
    artifact_type: str = "DirectorArtifact"
    execution_context: str = Field(default="STANDARD")
    mission_id: Optional[str] = Field(default=None)
    mission_action: Optional[str] = Field(default=None)
    objective_id: Optional[str] = Field(default=None)
    objective_action: Optional[str] = Field(default=None)
    read_only: bool = Field(default=False)
    state_mutation_from_query: str = Field(default="None")
    mutation_ledger: Optional[MutationLedger] = None
    mission_state_validation: str = Field(default="Passed")
    current_autonomous_delegation: str = Field(default="None")
    expected_artifact: str = Field(default="None")
    last_completed_delegation: str = Field(default="None")
    last_result_artifact: str = Field(default="None")
    operating_mode: str = Field(..., description="EXECUTIVE BRIEFING MODE, STRATEGIC DECISION MODE, EXECUTIVE COMMAND MODE, or INTERACTIVE AGENT SESSION")
    company_health: str = Field(...)
    executive_board_consulted: List[str] = Field(default_factory=list)
    executive_summary: str = Field(...)
    top_priorities: List[ExecutivePriority] = Field(default_factory=list)
    major_opportunities: List[str] = Field(default_factory=list)
    major_risks: List[str] = Field(default_factory=list)
    delegations: List[DelegationRecord] = Field(default_factory=list)
    pending_approvals: List[ApprovalRequest] = Field(default_factory=list)
    active_agent_sessions: List[ActiveAgentSession] = Field(default_factory=list)
    executive_decisions: List[ExecutiveDecisionRecord] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)

class DirectorAgentContext(BaseModel):
    company_id: str
    query: str
    operating_mode: str
    resolution_version: str = Field(default="")
    query_digest: str = Field(default="")
    intent_category: str = Field(default="UNKNOWN")
    command_class: str = Field(default="UNKNOWN")
    authority_mode: str = Field(default="READ_ONLY")
    authority_scope: str = Field(default="NONE")
    mutation_allowed: bool = Field(default=False)
    mission_creation_allowed: bool = Field(default=False)
    mission_execution_allowed: bool = Field(default=False)
    objective_creation_allowed: bool = Field(default=False)
    external_side_effect_allowed: bool = Field(default=False)
    execution_context: str = Field(default="STANDARD")
    mission_id: Optional[str] = Field(default=None)
    mission_action: Optional[str] = Field(default=None)
    mission_read_only: bool = Field(default=False)
    objective_id: Optional[str] = Field(default=None)
    objective_action: Optional[str] = Field(default=None)
    objective_read_only: bool = Field(default=True)
    coo_artifact_status: str
    cfo_artifact_status: str
    cro_artifact_status: str
    governance_status: str
    board_consultation_details: List[str] = Field(default_factory=list)
    aggregated_metrics: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def ensure_no_contamination(self) -> 'DirectorAgentContext':
        suspicious = ["do work", "re-architect", "agent", "prompt", "i need you to write"]
        if any(phrase in self.company_id.lower() for phrase in suspicious) or len(self.company_id) > 80:
            raise ValueError("Context Contamination: Prompt leaked into Director Context.")
        return self

class MissionMilestone(BaseModel):
    milestone_id: str = Field(...)
    name: str = Field(...)
    description: str = Field(...)
    status: str = Field(...)
    sequence: int = Field(...)
    success_criteria: str = Field(...)
    progress: int = Field(...)

class MissionArtifact(BaseArtifact):
    artifact_type: str = "MissionArtifact"
    mission_summary: str = Field(...)
    mission_id: str = Field(...)
    objective_id: Optional[str] = Field(default=None)
    original_request: str = Field(default="")
    objective: str = Field(...)
    normalized_objective: str = Field(default="")
    status: str = Field(...)
    priority: str = Field(...)
    autonomy_level: str = Field(...)
    overall_progress: int = Field(...)
    mission_health: str = Field(...)
    success_criteria_progress: str = Field(...)
    mission_type: str = Field(default="CLIENT_ACQUISITION")
    success_criterion: str = Field(default="verified_won_clients")
    target_count: int = Field(default=1)
    verified_count: int = Field(default=0)
    evidence_source: str = Field(default="UNKNOWN")
    simulation_mode: bool = Field(default=False)
    terminal_reason: str = Field(default="None")
    external_side_effects: str = Field(default="NONE")
    canary_pipeline_verified: bool = Field(default=False)
    real_world_business_evidence_verified: bool = Field(default=False)
    mission_objective_achieved: bool = Field(default=False)
    current_phase: str = Field(...)
    current_milestone: str = Field(default="None")
    current_milestone_status: str = Field(default="None")
    milestones: List[MissionMilestone] = Field(default_factory=list)
    plan_version: str = Field(default="v1")
    plan_status: str = Field(default="ACTIVE")
    historical_plans: List[Dict[str, Any]] = Field(default_factory=list)
    progression_state: str = Field(default="RUNNING")
    execution_state: str = Field(default="READY")
    dispatch_state: str = Field(default="NONE")
    stall_detector_status: str = Field(default="Clear")
    loop_safety_status: str = Field(default="Safe")
    director_plan_status: str = Field(...)
    active_delegations: List[Dict[str, Any]] = Field(default_factory=list)
    delegation_history: List[Dict[str, Any]] = Field(default_factory=list)
    execution_requests: List[Dict[str, Any]] = Field(default_factory=list)
    worker_claims: List[Dict[str, Any]] = Field(default_factory=list)
    external_operations: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_lineage: List[Dict[str, Any]] = Field(default_factory=list)
    success_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    replan_count: int = Field(default=0)
    retry_count: int = Field(default=0)
    repeated_strategy_count: int = Field(default=0)
    outreach_batch_count: int = Field(default=0)
    zero_progress_count: int = Field(default=0)
    last_completed_delegation: str = Field(default="None")
    next_eligible_action: str = Field(default="None")
    auto_continue_status: str = Field(default="STOPPED")
    governance_status: str = Field(default="Active")
    budget_status: str = Field(...)
    deadline_status: str = Field(...)
    dependencies: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    pending_approvals: List[str] = Field(default_factory=list)
    recent_mission_events: List[str] = Field(default_factory=list)
    action_history: List[str] = Field(default_factory=list)
    progression_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    progression_materializations: List[Dict[str, Any]] = Field(default_factory=list)
    completion_guard_status: str = Field(default="PENDING")
    mission_completion_integrity: str = Field(default="PASSED")
    progression_materialization_integrity: str = Field(default="PASSED")
    mission_stall_integrity: str = Field(default="PASSED")
    mission_dispatch_integrity: str = Field(default="PASSED")
    execution_state_integrity: str = Field(default="NOT_EVALUATED")
    delegation_execution_integrity: str = Field(default="NOT_EVALUATED")
    worker_claim_integrity: str = Field(default="NOT_EVALUATED")
    next_evaluation: str = Field(...)
    recommended_next_action: str = Field(...)
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)