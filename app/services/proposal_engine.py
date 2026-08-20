"""
NapsterTec AI - Proposal Engine
Module: app/services/proposal_engine.py
"""
import uuid
from typing import Dict, Any
from app.schemas.shared_artifacts import (
    ProposalAgentContext, ProposalArtifact, ProposalDeliverable, 
    ProposalPhase, ProposalRisk, ProposalAssumption
)

class ProposalEngine:
    def design_architecture(self, context: ProposalAgentContext, session_id: str) -> ProposalArtifact:
        
        # 1. Transform Deliverables (Included vs Excluded)
        deliverables = []
        for mod in context.modules:
            deliverables.append(ProposalDeliverable(
                name=mod.get("name", "Module"),
                description=mod.get("justification", "Core system requirement"),
                included=True
            ))
        # Deterministic exclusions
        deliverables.extend([
            ProposalDeliverable(name="Custom ERP Integration", description="Deep enterprise backend syncing", included=False),
            ProposalDeliverable(name="Native Mobile App (iOS/Android)", description="App store compilation and deployment", included=False)
        ])

        # 2. Phases (FIXED: changed 'focus' to 'description')
        phases = [
            ProposalPhase(phase_number=1, name="Discovery & Architecture", description="Finalizing technical blueprints and asset collection."),
            ProposalPhase(phase_number=2, name="UI/UX Design", description="Creating visual layouts for approval."),
            ProposalPhase(phase_number=3, name="Core Development", description="Building the core modules and database."),
            ProposalPhase(phase_number=4, name="Integration & Testing", description="Connecting APIs and ensuring QA."),
            ProposalPhase(phase_number=5, name="Deployment", description="Go-live, cloud hosting, and SSL setup."),
            ProposalPhase(phase_number=6, name="Handover & Training", description="Client onboarding and documentation.")
        ]

        # 3. Assumptions & Risks
        assumptions = [
            ProposalAssumption(description="Client will provide all necessary branding assets (logos, colors) within 7 days."),
            ProposalAssumption(description="Client owns or has access to their target domain name."),
            ProposalAssumption(description="Third-party APIs (e.g., payment gateways) will approve merchant accounts promptly."),
            ProposalAssumption(description="Client provides initial content (text/images) for core pages."),
            ProposalAssumption(description="Feedback cycles will not exceed 3 business days per milestone.")
        ]
        
        risks = [
            ProposalRisk(description="Delayed content delivery from client", mitigation="Use placeholder content to keep development on schedule."),
            ProposalRisk(description="Third-party API downtime", mitigation="Implement graceful fallbacks and robust error handling."),
            ProposalRisk(description="Scope creep during design phase", mitigation="Strict sign-off procedures before development begins."),
            ProposalRisk(description="Slow stakeholder approval", mitigation="Define explicit review windows in the project contract.")
        ]

        # 4. Narratives
        exec_summary = f"NapsterTec proposes a highly optimized {context.solution_type} tailored specifically for {context.business_name} to address critical operational bottlenecks and drive digital growth."
        business_context = f"As a competitive player in the {context.category} sector, transitioning to a modern, scalable digital architecture is required to capture market share."
        solution_overview = f"We will deploy a {context.solution_type} featuring {len(context.modules)} core modules designed to eliminate manual workflows and boost engagement."
        roi_narrative = "By modernizing your digital infrastructure, your team will experience reduced manual administration, greater online visibility, and higher customer conversion rates, leading to compounding operational efficiency."

        artifact_id = f"prop_{uuid.uuid4().hex[:8]}"

        return ProposalArtifact(
            artifact_id=artifact_id,
            agent_run_id=session_id,
            lead_id=context.lead_id,
            proposal_type="Digital Transformation Proposal",
            executive_summary=exec_summary,
            business_context=business_context,
            verified_problems=context.verified_issues,
            solution_overview=solution_overview,
            deliverables=deliverables,
            implementation_phases=phases,
            business_benefits=context.benefits,
            roi_narrative=roi_narrative,
            assumptions=assumptions,
            risks=risks,
            call_to_action="Approve the architecture blueprint to initiate formal project kickoff.",
            execution_metadata={"evaluation_method": "Deterministic Proposal Mapping"}
        )